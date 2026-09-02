from __future__ import annotations

"""Grounded, deterministic figures for the research-report export path.

The report writer is allowed to choose *which* eligible figures matter to the
narrative.  This module owns the other half of the contract: it derives every
number from persisted artifacts and renders the resulting FigureSpec without
calling a model.  A missing data series is represented explicitly, never as a
plausible-looking replacement chart.
"""

from io import BytesIO
import re
from typing import Any, Literal

from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, Field

from backend.app.workflow.phase2_evidence import metric_direction


class ChartSeries(BaseModel):
    name: str
    values: list[float]


class ChartSpec(BaseModel):
    chart_type: Literal[
        "bar",
        "grouped_bar",
        "line",
        "paired",
        "diverging_bar",
        "small_multiples",
        "table",
        "workflow",
        "timeline",
        "model_structure",
        "method_pipeline",
    ]
    labels: list[str] = Field(default_factory=list)
    series: list[ChartSeries] = Field(default_factory=list)
    y_label: str = ""
    metric_name: str = ""
    metric_direction: str = ""
    rows: list[tuple[str, str]] = Field(default_factory=list)


class FigureSpec(BaseModel):
    figure_id: str
    kind: Literal[
        "workflow",
        "control_variables",
        "model_structure",
        "method_pipeline",
        "seed_comparison",
        "seed_delta",
        "main_comparison",
        "training_curve",
        "timeline",
    ]
    title: str
    caption: str
    scientific_purpose: str
    source_artifact_ids: list[str]
    chart: ChartSpec


class OmittedFigureSpec(BaseModel):
    figure_id: str
    reason: str
    source_artifact_ids: list[str] = Field(default_factory=list)


class ReportSpec(BaseModel):
    schema_version: str = "round6.visual-report.v2"
    language: Literal["zh-CN", "en"] = "zh-CN"
    renderer: str = "pillow-publication-v2"
    figures: list[FigureSpec] = Field(default_factory=list)
    omitted_figures: list[OmittedFigureSpec] = Field(default_factory=list)
    decision_rationale: str = ""


def build_report_spec(
    artifacts: list[Any],
    *,
    selected_figure_ids: list[str],
    decision_rationale: str = "",
    language: str = "zh-CN",
) -> dict[str, Any]:
    """Build a persisted spec from real artifact content only.

    ``selected_figure_ids`` comes from the Writer's structured figure-planning
    call.  Invalid selections are rejected by omission rather than being
    converted into a different chart.
    """
    latest = _latest_by_type(artifacts)
    ids = {str(value) for value in selected_figure_ids}
    result_artifact = latest.get("experiment_result")
    result = _content(result_artifact)
    plan_artifact = latest.get("plan")
    plan = _content(plan_artifact)
    source_ids = _ids(latest.get("problem"), latest.get("hypothesis"), plan_artifact, result_artifact, latest.get("revision"))
    candidates: dict[str, FigureSpec] = {
        "model_structure": _model_structure_figure(plan, plan_artifact),
        "method_pipeline": _method_pipeline_figure(plan, plan_artifact, result_artifact),
        "control_variables": _control_figure(plan, plan_artifact),
        "seed_comparison": _seed_figure(result, result_artifact),
        "seed_delta": _seed_delta_figure(result, result_artifact),
        "main_comparison": _main_comparison_figure(result, result_artifact),
        "training_curve": _training_curve_figure(result, result_artifact),
        "workflow_timeline": _timeline_figure(artifacts),
    }
    figures: list[FigureSpec] = []
    omitted: list[OmittedFigureSpec] = []
    # Keep this order aligned with the report's chapter flow so figure numbers
    # remain monotonic after section-aware export.
    for figure_id in (
        "model_structure",
        "method_pipeline",
        "control_variables",
        "workflow_timeline",
        "training_curve",
        "main_comparison",
        "seed_comparison",
        "seed_delta",
    ):
        candidate = candidates[figure_id]
        if candidate is None:
            omitted.append(_omission_for(figure_id, result_artifact, plan_artifact))
        elif figure_id in ids:
            figures.append(candidate)
        else:
            omitted.append(OmittedFigureSpec(figure_id=figure_id, reason="写作模型未将该可用图表选入本报告叙事。", source_artifact_ids=candidate.source_artifact_ids))
    return ReportSpec(
        language="zh-CN" if language != "en" else "en",
        figures=figures,
        omitted_figures=omitted,
        decision_rationale=decision_rationale.strip(),
    ).model_dump()


def render_figure_png(figure: FigureSpec | dict[str, Any]) -> bytes:
    """Render one validated FigureSpec using the local publication preset.

    Titles intentionally remain outside the bitmap and are emitted as Word
    captions.  This follows the project's ``paper-figure`` contract and keeps
    the image reusable in a manuscript or a two-column layout.
    """
    spec = figure if isinstance(figure, FigureSpec) else FigureSpec.model_validate(figure)
    image = Image.new("RGB", (1500, 700), "white")
    draw = ImageDraw.Draw(image)
    label_font = _font(28)
    small_font = _font(23)
    chart = spec.chart
    if chart.chart_type == "workflow":
        _draw_workflow(draw, chart.labels, label_font, small_font)
    elif chart.chart_type == "timeline":
        _draw_timeline(draw, chart.rows, label_font, small_font)
    elif chart.chart_type == "table":
        _draw_table(draw, chart.rows, label_font, small_font)
    elif chart.chart_type == "model_structure":
        _draw_model_structure(draw, chart.rows, label_font, small_font)
    elif chart.chart_type == "method_pipeline":
        _draw_method_pipeline(draw, chart.labels, label_font, small_font)
    elif chart.chart_type == "paired":
        _draw_paired(draw, chart, label_font, small_font)
    elif chart.chart_type == "diverging_bar":
        _draw_diverging_bar(draw, chart, label_font, small_font)
    elif chart.chart_type == "small_multiples":
        _draw_small_multiples(draw, chart, label_font, small_font)
    elif chart.chart_type in {"bar", "grouped_bar", "line"}:
        _draw_series(draw, chart, label_font, small_font)
    else:  # defensive schema completeness; impossible for the current union.
        raise ValueError(f"REPORT_FIGURE_RENDER_UNSUPPORTED:{chart.chart_type}")
    output = BytesIO()
    image.save(output, format="PNG", optimize=False, dpi=(300, 300))
    return output.getvalue()


def _model_structure_figure(plan: dict[str, Any], plan_artifact: Any) -> FigureSpec | None:
    comparisons = plan.get("comparisons") if isinstance(plan.get("comparisons"), list) else []
    comparison = next(
        (
            item
            for item in comparisons
            if isinstance(item, dict)
            and str(item.get("baseline") or "").strip()
            and str(item.get("variant") or "").strip()
        ),
        None,
    )
    if comparison is None:
        return None
    baseline = str(comparison["baseline"]).strip()
    variant = str(comparison["variant"]).strip()
    rows = [
        ("基线方案", f"共同输入与受控设置 → {_short_node_label(baseline)}"),
        ("实验方案", f"共同输入与受控设置 → {_short_node_label(variant)}"),
    ]
    return FigureSpec(
        figure_id="model_structure",
        kind="model_structure",
        title="基线方案与实验方案对比",
        caption=(
            f"冻结计划中的基线为“{baseline}”，实验方案为“{variant}”；"
            "图中只表达计划声明的比较关系，不补造网络层、通道数或其他结构细节。"
        ),
        scientific_purpose="直观说明冻结计划中基线方案与实验方案的受控差异。",
        source_artifact_ids=_ids(plan_artifact),
        chart=ChartSpec(chart_type="model_structure", rows=rows),
    )


def _method_pipeline_figure(plan: dict[str, Any], plan_artifact: Any, result_artifact: Any) -> FigureSpec | None:
    procedure = plan.get("procedure") if isinstance(plan.get("procedure"), dict) else {}
    evaluations = plan.get("evaluations") if isinstance(plan.get("evaluations"), list) else []
    if not procedure and not evaluations:
        return None
    labels = ["数据与合同校验", "受控配对训练", "多随机种子复现", "指标与统计检验", "结论边界审计"]
    return FigureSpec(
        figure_id="method_pipeline",
        kind="method_pipeline",
        title="实验方法与证据判定流程",
        caption="流程由冻结计划与最终有效实验产物共同确定；每一步均对应可追溯输入或结果。",
        scientific_purpose="概括从数据校验到统计判定的完整方法路径。",
        source_artifact_ids=_ids(plan_artifact, result_artifact),
        chart=ChartSpec(chart_type="method_pipeline", labels=labels),
    )


def _control_figure(plan: dict[str, Any], plan_artifact: Any) -> FigureSpec | None:
    parameters = plan.get("parameters") if isinstance(plan.get("parameters"), dict) else {}
    seeds = plan.get("seeds") if isinstance(plan.get("seeds"), list) else []
    rows = [
        (_metric_label(str(key)), _brief(value))
        for key, value in parameters.items()
        if key != "additional_sections"
        and value not in (None, "", [], {})
        and not isinstance(value, (dict, list))
    ]
    if seeds:
        rows.append(("随机种子", "、".join(str(value) for value in seeds)))
    if not rows:
        return None
    return FigureSpec(
        figure_id="control_variables", kind="control_variables", title="受控变量与评价设置",
        caption="变量、随机种子和评价设置均直接来自已冻结的实验计划。",
        scientific_purpose="说明比较中保持一致的条件，避免把工程变更误认为科学效应。",
        source_artifact_ids=_ids(plan_artifact), chart=ChartSpec(chart_type="table", rows=rows[:12]),
    )


def _main_comparison_figure(result: dict[str, Any], result_artifact: Any) -> FigureSpec | None:
    metrics = result.get("metrics") if isinstance(result.get("metrics"), dict) else {}
    records = _preferred_pair_records(_paired_metric_records(metrics))
    if records:
        accuracy_records = [
            record for record in records if "accuracy" in _normalized_metric_name(record[0])
        ]
        shown = (accuracy_records or records)[:3]
        return FigureSpec(
            figure_id="main_comparison", kind="main_comparison", title="主要实验指标对比",
            caption="数值直接取自最终有效实验结果；每个子图只比较同一指标，不包含失败尝试或推测值。",
            scientific_purpose="呈现基线方案与实验方案在相同指标上的最终对比。",
            source_artifact_ids=_ids(result_artifact),
            chart=ChartSpec(
                chart_type="small_multiples",
                labels=[_metric_label(record[0]) for record in shown],
                series=[
                    ChartSeries(name="基线方案", values=[record[3] for record in shown]),
                    ChartSeries(name="实验方案", values=[record[4] for record in shown]),
                ],
                y_label="指标值",
            ),
        )

    standalone = [
        (str(key), float(value))
        for key, value in metrics.items()
        if _finite_number(value) and not _dispersion_metric(str(key))
    ][:4]
    if not standalone:
        return None
    return FigureSpec(
        figure_id="main_comparison", kind="main_comparison", title="主要实验指标对比",
        caption="数值直接取自最终有效实验结果；图中仅并列展示记录值，不推断这些指标构成配对比较。",
        scientific_purpose="呈现最终结果中已持久化的主要数值指标。",
        source_artifact_ids=_ids(result_artifact),
        chart=ChartSpec(
            chart_type="small_multiples",
            labels=[_metric_label(name) for name, _ in standalone],
            series=[ChartSeries(name="记录值", values=[value for _, value in standalone])],
            y_label="指标值",
        ),
    )


def _seed_figure(result: dict[str, Any], result_artifact: Any) -> FigureSpec | None:
    raw = result.get("seed_results") or result.get("per_seed_results") or result.get("seed_metrics")
    entries = [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []
    if len(entries) < 2:
        return None
    metric_maps = [
        item.get("metrics") if isinstance(item.get("metrics"), dict) else item
        for item in entries
    ]
    labels = [str(item.get("seed") or item.get("random_seed") or index + 1) for index, item in enumerate(entries)]
    records = [
        record
        for record in _preferred_pair_records(_paired_metric_records(metric_maps[0]))
        if all(
            _finite_number(metrics.get(record[1]))
            and _finite_number(metrics.get(record[2]))
            for metrics in metric_maps
        )
    ]
    if records:
        metric_name, baseline_key, experiment_key, _, _ = records[0]
        return FigureSpec(
            figure_id="seed_comparison", kind="seed_comparison", title=f"不同随机种子的{_metric_label(metric_name)}对比",
            caption="每条连线对应同一随机种子下的基线方案和实验方案；聚合结论仍以最终统计证据为准。",
            scientific_purpose="展示随机初始化下的配对变化，防止只报告单次运行。",
            source_artifact_ids=_ids(result_artifact),
            chart=ChartSpec(
                chart_type="paired",
                labels=labels,
                series=[
                    ChartSeries(name="基线方案", values=[float(metrics[baseline_key]) for metrics in metric_maps]),
                    ChartSeries(name="实验方案", values=[float(metrics[experiment_key]) for metrics in metric_maps]),
                ],
                y_label=_metric_label(metric_name),
            ),
        )

    common = [
        str(key)
        for key in metric_maps[0]
        if not _dispersion_metric(str(key))
        and all(_finite_number(metrics.get(key)) for metrics in metric_maps)
    ][:4]
    if not common:
        return None
    return FigureSpec(
        figure_id="seed_comparison", kind="seed_comparison", title="不同随机种子的实验指标记录",
        caption="每组柱对应同一持久化结果中的一个随机种子；图中不推断未声明的基线—实验配对关系。",
        scientific_purpose="展示随机初始化下的稳定性，防止只报告单次运行。",
        source_artifact_ids=_ids(result_artifact),
        chart=ChartSpec(
            chart_type="grouped_bar",
            labels=labels,
            series=[
                ChartSeries(name=_metric_label(key), values=[float(metrics[key]) for metrics in metric_maps])
                for key in common
            ],
            y_label="指标值",
        ),
    )


def _seed_delta_figure(result: dict[str, Any], result_artifact: Any) -> FigureSpec | None:
    raw = result.get("seed_results") or result.get("per_seed_results") or result.get("seed_metrics")
    entries = [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []
    if len(entries) < 2:
        return None
    metric_maps = [
        item.get("metrics") if isinstance(item.get("metrics"), dict) else item
        for item in entries
    ]
    records = [
        record
        for record in _preferred_pair_records(_paired_metric_records(metric_maps[0]))
        if all(
            _finite_number(metrics.get(record[1]))
            and _finite_number(metrics.get(record[2]))
            for metrics in metric_maps
        )
    ]
    if not records:
        return None
    metric_name, baseline_key, experiment_key, _, _ = records[0]
    labels = [str(item.get("seed") or item.get("random_seed") or index + 1) for index, item in enumerate(entries)]
    values = [
        float(metrics[experiment_key]) - float(metrics[baseline_key])
        for metrics in metric_maps
    ]
    direction = "lower" if metric_direction(metric_name) == "minimize" else "higher"
    return FigureSpec(
        figure_id="seed_delta",
        kind="seed_delta",
        title=f"逐随机种子的{_metric_label(metric_name)}差值",
        caption="纵轴为实验方案减去基线方案；正值表示实验方案指标更高，负值表示更低，优劣需结合指标方向判断。",
        scientific_purpose="直接展示配对差值方向是否在不同随机种子下保持一致。",
        source_artifact_ids=_ids(result_artifact),
        chart=ChartSpec(
            chart_type="diverging_bar",
            labels=labels,
            series=[ChartSeries(name="实验方案 − 基线方案", values=values)],
            y_label=f"{_metric_label(metric_name)}差值",
            metric_direction=direction,
        ),
    )


def _training_curve_figure(result: dict[str, Any], result_artifact: Any) -> FigureSpec | None:
    history = result.get("epoch_metrics") or result.get("training_history")
    rows = [item for item in history if isinstance(item, dict)] if isinstance(history, list) else []
    if len(rows) < 2:
        return None
    numeric_keys = [key for key in rows[0] if key not in {"epoch", "step"} and all(_finite_number(item.get(key)) for item in rows)]
    if not numeric_keys:
        return None
    labels = [str(item.get("epoch") or item.get("step") or index + 1) for index, item in enumerate(rows)]
    return FigureSpec(
        figure_id="training_curve", kind="training_curve", title="训练过程曲线",
        caption="曲线只在真实 epoch/step 指标已持久化时生成。",
        scientific_purpose="描述训练过程而非替代最终的保留集评价。",
        source_artifact_ids=_ids(result_artifact),
        chart=ChartSpec(chart_type="small_multiples", labels=labels, series=[ChartSeries(name=_metric_label(str(key)), values=[float(item[key]) for item in rows]) for key in numeric_keys[:3]], y_label="训练指标"),
    )


def _timeline_figure(artifacts: list[Any]) -> FigureSpec:
    scientific = {"problem", "hypothesis", "plan", "experiment_task", "experiment_result", "revision", "scientific_analysis", "scientific_review", "scientific_synthesis", "scientific_conclusion"}
    engineering = {"experiment_failure", "experiment_diagnosis", "experiment_bundle", "candidate", "candidate_lineage"}
    rows = []
    sources = []
    for artifact in artifacts[-16:]:
        kind = str(getattr(artifact, "type", ""))
        if kind not in scientific | engineering:
            continue
        rows.append(("科学推进" if kind in scientific else "工程处置", _timeline_label(kind)))
        artifact_id = str(getattr(artifact, "id", ""))
        if artifact_id:
            sources.append(artifact_id)
    return FigureSpec(
        figure_id="workflow_timeline", kind="timeline", title="工作流与研究推进时间线",
        caption="工程处置与科学推进分轨显示：前者保证可运行性，后者才构成研究证据。",
        scientific_purpose="避免把环境修复、重试和失败记录误解释为假设得到支持。",
        source_artifact_ids=list(dict.fromkeys(sources)), chart=ChartSpec(chart_type="timeline", rows=rows or [("科学推进", "尚无可显示的持久化阶段")]),
    )


def _omission_for(figure_id: str, result_artifact: Any, plan_artifact: Any) -> OmittedFigureSpec:
    reasons = {
        "control_variables": "实验计划未持久化可展示的参数或随机种子。",
        "seed_comparison": "真实实验结果未持久化至少两个随机种子的同指标记录。",
        "main_comparison": "真实实验结果未持久化至少两个可比较的数值指标。",
        "training_curve": "未发现真实 epoch/step 级训练指标；为避免补造曲线，本图未生成。",
    }
    return OmittedFigureSpec(figure_id=figure_id, reason=reasons.get(figure_id, "缺少可追溯的数据来源。"), source_artifact_ids=_ids(result_artifact, plan_artifact))


def _latest_by_type(artifacts: list[Any]) -> dict[str, Any]:
    latest: dict[str, Any] = {}
    for artifact in artifacts:
        kind = str(getattr(artifact, "type", ""))
        if kind:
            latest[kind] = artifact
    return latest


def _content(artifact: Any) -> dict[str, Any]:
    value = getattr(artifact, "content", {}) if artifact is not None else {}
    return value if isinstance(value, dict) else {}


def _ids(*artifacts: Any) -> list[str]:
    return list(dict.fromkeys(str(getattr(item, "id", "")) for item in artifacts if getattr(item, "id", "")))


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and float("-inf") < float(value) < float("inf")


def _dispersion_metric(key: str) -> bool:
    return any(token in key.lower() for token in ("std", "variance", "var", "time", "elapsed", "duration"))


def _paired_metrics(metrics: dict[str, Any]) -> list[tuple[str, float, float]]:
    return [
        (name, baseline, experiment)
        for name, _, _, baseline, experiment in _paired_metric_records(metrics)
    ]


def _paired_metric_records(
    metrics: dict[str, Any],
) -> list[tuple[str, str, str, float, float]]:
    """Resolve baseline/variant metric aliases without guessing numeric pairs.

    Harness results may persist ``Baseline_Test_Accuracy`` beside either
    ``Test Accuracy`` or an explicit treatment-prefixed alias such as
    ``LS_Test_Accuracy``.  Matching normalized metric names keeps the chart
    grounded while preferring the unprefixed, reader-facing key when present.
    """
    records: list[tuple[str, str, str, float, float]] = []
    for key, baseline in metrics.items():
        baseline_key = str(key)
        if not baseline_key.lower().startswith("baseline_") or not _finite_number(baseline):
            continue
        baseline_name = baseline_key[len("Baseline_") :]
        target = _normalized_metric_name(baseline_name)
        candidates: list[tuple[int, str, float]] = []
        for candidate_key, candidate_value in metrics.items():
            candidate_name = str(candidate_key)
            if candidate_name == baseline_key or not _finite_number(candidate_value):
                continue
            direct = _normalized_metric_name(candidate_name)
            treatment_stripped = _normalized_metric_name(
                _strip_metric_group_prefix(candidate_name)
            )
            if direct != target and treatment_stripped != target:
                continue
            candidates.append((0 if direct == target else 1, candidate_name, float(candidate_value)))
        if not candidates:
            continue
        _, experiment_key, experiment = min(candidates, key=lambda item: (item[0], item[1]))
        display_name = _strip_metric_group_prefix(experiment_key).replace("_", " ").strip()
        if _dispersion_metric(display_name):
            continue
        records.append(
            (display_name or baseline_name, baseline_key, experiment_key, float(baseline), experiment)
        )
    return records


def _preferred_pair_records(
    records: list[tuple[str, str, str, float, float]],
) -> list[tuple[str, str, str, float, float]]:
    def priority(record: tuple[str, str, str, float, float]) -> tuple[int, str]:
        name = _normalized_metric_name(record[0])
        if "accuracy" in name or "准确率" in record[0]:
            return (0, name)
        if any(token in name for token in ("error", "f1", "auc", "precision", "recall")):
            return (1, name)
        if "loss" in name:
            return (3, name)
        return (2, name)

    return sorted(records, key=priority)


def _strip_metric_group_prefix(value: str) -> str:
    return re.sub(
        r"^(?:ls|label[_\s-]*smoothing|variant|experiment|treatment)[_\s-]+",
        "",
        str(value),
        flags=re.IGNORECASE,
    )


def _normalized_metric_name(value: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", str(value).lower())


def _metric_label(value: str) -> str:
    labels = {
        "Overall Test Accuracy": "整体测试准确率",
        "Test Accuracy": "测试准确率",
        "Final Training Loss": "最终训练损失",
        "Target Class Confusion Error Rate": "目标类别混淆错误率",
        "Total Parameters": "参数量",
        "accuracy": "准确率",
        "loss": "损失",
        "tcer": "目标类别混淆错误率",
        "epochs": "训练轮数",
        "batch_size": "批量大小",
        "learning_rate": "学习率",
        "weight_decay": "权重衰减",
        "optimizer": "优化器",
        "label_smoothing_epsilon": "标签平滑系数 ε",
    }
    return labels.get(value, value.replace("_", " "))


def _display_metric_label(value: str) -> str:
    """Short reader-facing labels that fit a published chart legend or card."""
    text = _metric_label(value)
    normalized = _normalized_metric_name(text)
    group = ""
    if normalized.startswith("baseline"):
        group = "基线："
    elif normalized.startswith(("re", "randomerasing", "experiment", "variant", "treatment")):
        group = "实验组："
    if "occluded" in normalized or "遮挡" in text:
        metric = "遮挡准确率"
    elif "accuracy" in normalized or "acc" in normalized or "准确率" in text:
        metric = "准确率"
    elif "loss" in normalized or "损失" in text:
        metric = "损失"
    else:
        metric = text
    return group + metric


def _short_node_label(value: str, limit: int = 22) -> str:
    text = re.sub(r"\s+", " ", str(value)).strip()
    lowered = text.lower()
    if "random erasing" in lowered:
        return "CNN + 随机擦除"
    if "standard cnn" in lowered and "basic augmentation" in lowered:
        return "标准 CNN + 基础增强"
    if "label smoothing" in lowered:
        epsilon = re.search(r"(?:epsilon|eps|ε)\s*=\s*([0-9.]+)", text, re.IGNORECASE)
        return f"标签平滑（ε={epsilon.group(1)}）" if epsilon else "标签平滑损失"
    if "standard" in lowered and "cross-entropy" in lowered:
        return "标准交叉熵损失"
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _brief(value: Any) -> str:
    if isinstance(value, list):
        return "、".join(str(item) for item in value[:6])
    if isinstance(value, dict):
        return "；".join(f"{key}={item}" for key, item in list(value.items())[:5])
    return str(value)


def _timeline_label(kind: str) -> str:
    labels = {"problem": "研究问题", "hypothesis": "研究假设", "plan": "实验设计", "experiment_task": "实验任务", "experiment_result": "有效实验结果", "revision": "反馈修订", "experiment_failure": "失败记录", "experiment_diagnosis": "故障诊断", "experiment_bundle": "实验包校验", "scientific_analysis": "科学分析", "scientific_review": "独立审查", "scientific_synthesis": "科学综合", "scientific_conclusion": "科学结论"}
    return labels.get(kind, kind)


def _font(size: int, *, bold: bool = False):
    paths = [
        "C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf" if bold else "C:/Windows/Fonts/simsun.ttc",
    ]
    for path in paths:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _draw_workflow(draw: ImageDraw.ImageDraw, labels: list[str], font, small_font) -> None:
    xs = [85, 370, 655, 940, 1225]
    for index, label in enumerate(labels[:5]):
        x = xs[index]
        fill = "#E8F0FF" if index < 3 else "#E9F8EF"
        draw.rounded_rectangle((x, 305, x + 190, 410), radius=16, fill=fill, outline="#2454E6", width=3)
        _centered(draw, (x + 95, 357), label, font, "#172033")
        if index < min(4, len(labels) - 1):
            draw.line((x + 192, 357, xs[index + 1] - 12, 357), fill="#334155", width=5)
            draw.polygon([(xs[index + 1] - 12, 357), (xs[index + 1] - 26, 348), (xs[index + 1] - 26, 366)], fill="#334155")
    _centered(draw, (750, 500), "仅实际执行并持久化的实验结果可进入科学结论", small_font, "#475569")


def _draw_model_structure(draw: ImageDraw.ImageDraw, rows: list[tuple[str, str]], font, small_font) -> None:
    palette = {
        "input": ("#E8F3F8", "#0072B2"),
        "feature": ("#F2F2F2", "#4D4D4D"),
        "variant": ("#FFF1E6", "#D55E00"),
        "output": ("#E8F5EE", "#009E73"),
    }
    y_positions = [205, 475]
    for row_index, (row_name, path) in enumerate(rows[:2]):
        y = y_positions[row_index]
        draw.text((55, y - 16), row_name, fill="#111111", font=small_font)
        labels = [item.strip() for item in path.split("→") if item.strip()]
        start_x, end_x = 235, 1435
        # Comparison labels frequently contain a complete model or augmentation
        # name. Give their boxes a real text lane instead of letting a long
        # English name spill across the connector.
        gap = 90
        count = max(1, len(labels))
        node_w = min(440, int((end_x - start_x - gap * (count - 1)) / count))
        for index, label in enumerate(labels):
            x = start_x + index * (node_w + gap)
            tone = (
                "input"
                if index == 0
                else ("variant" if row_index == 1 else "output")
                if index == count - 1
                else "feature"
            )
            fill, stroke = palette[tone]
            draw.rounded_rectangle((x, y - 70, x + node_w, y + 70), radius=10, fill=fill, outline=stroke, width=3)
            _centered_wrapped(
                draw, (x + node_w / 2, y), label, small_font, "#111111", node_w - 34
            )
            if index < count - 1:
                x1, x2 = x + node_w + 4, x + node_w + gap - 4
                draw.line((x1, y, x2, y), fill="#333333", width=5)
                draw.polygon([(x2, y), (x2 - 13, y - 8), (x2 - 13, y + 8)], fill="#333333")
    draw.line((45, 340, 1455, 340), fill="#D9D9D9", width=2)


def _draw_method_pipeline(draw: ImageDraw.ImageDraw, labels: list[str], font, small_font) -> None:
    if not labels:
        return
    fills = ["#E8F3F8", "#F2F2F2", "#E8F5EE", "#FFF1E6", "#F4ECF7"]
    strokes = ["#0072B2", "#4D4D4D", "#009E73", "#D55E00", "#7B3294"]
    left, right, y, gap = 55, 1445, 330, 34
    count = min(5, len(labels))
    node_w = int((right - left - gap * (count - 1)) / count)
    for index, label in enumerate(labels[:count]):
        x = left + index * (node_w + gap)
        draw.rounded_rectangle((x, y - 70, x + node_w, y + 70), radius=10, fill=fills[index], outline=strokes[index], width=3)
        _centered(draw, (x + node_w / 2, y), label, small_font, "#111111")
        if index < count - 1:
            x1, x2 = x + node_w + 4, x + node_w + gap - 4
            draw.line((x1, y, x2, y), fill="#333333", width=5)
            draw.polygon([(x2, y), (x2 - 13, y - 8), (x2 - 13, y + 8)], fill="#333333")


def _draw_small_multiples(draw: ImageDraw.ImageDraw, chart: ChartSpec, font, small_font) -> None:
    left, top, right, bottom = 80, 70, 1450, 590
    if not chart.series or not chart.labels:
        return
    is_training = len(chart.labels) > 4 and all(len(series.values) == len(chart.labels) for series in chart.series)
    colors = ["#0072B2", "#D55E00", "#009E73", "#7B3294"]
    if is_training:
        top, bottom = 100, 560
        panel_count = min(3, len(chart.series))
        gap = 55
        panel_w = (right - left - gap * (panel_count - 1)) / panel_count
        for panel_index, series in enumerate(chart.series[:panel_count]):
            x0 = left + panel_index * (panel_w + gap)
            x1 = x0 + panel_w
            values = [float(value) for value in series.values]
            minimum, maximum = min(values), max(values)
            span = maximum - minimum or 1.0
            minimum -= span * 0.08
            maximum += span * 0.08
            _draw_panel_axes(draw, x0, top, x1, bottom, minimum, maximum, small_font)
            points = []
            for index, value in enumerate(values):
                x = x0 + (x1 - x0) * index / max(1, len(values) - 1)
                y = bottom - (value - minimum) / (maximum - minimum) * (bottom - top)
                points.append((x, y))
            if len(points) > 1:
                draw.line(points, fill=colors[panel_index], width=4)
            for index, point in enumerate(points):
                if index in {0, len(points) - 1} or (index + 1) % 5 == 0:
                    draw.ellipse((point[0] - 4, point[1] - 4, point[0] + 4, point[1] + 4), fill=colors[panel_index])
            _centered(
                draw,
                ((x0 + x1) / 2, 38),
                f"({chr(97 + panel_index)}) {_display_metric_label(series.name)}",
                small_font,
                "#111111",
            )
            for tick_index in range(0, len(chart.labels), 5):
                x = x0 + (x1 - x0) * tick_index / max(1, len(chart.labels) - 1)
                _centered(draw, (x, bottom + 18), chart.labels[tick_index], _font(19), "#444444")
        return

    if len(chart.series) == 1:
        _draw_metric_cards(draw, chart, font, small_font)
        return

    panel_count = min(3, len(chart.labels))
    gap = 85
    panel_w = (right - left - gap * (panel_count - 1)) / panel_count
    for panel_index, label in enumerate(chart.labels[:panel_count]):
        x0 = left + panel_index * (panel_w + gap)
        x1 = x0 + panel_w
        values = [float(series.values[panel_index]) for series in chart.series if panel_index < len(series.values)]
        maximum = max(values) * 1.15 or 1.0
        _draw_panel_axes(draw, x0, top, x1, bottom, 0.0, maximum, small_font)
        bar_gap = panel_w * 0.12
        bar_w = (panel_w - bar_gap * (len(values) + 1)) / max(1, len(values))
        for series_index, value in enumerate(values):
            x = x0 + bar_gap + series_index * (bar_w + bar_gap)
            y = bottom - value / maximum * (bottom - top)
            draw.rectangle((x, y, x + bar_w, bottom), fill=colors[series_index])
            _centered(draw, (x + bar_w / 2, y - 18), f"{value:.3f}", small_font, "#111111")
        _centered_wrapped(draw, ((x0 + x1) / 2, bottom + 46), _display_metric_label(label), small_font, "#111111", panel_w - 24)
    legend_x = right - 315
    for index, series in enumerate(chart.series[:2]):
        y = 12 + index * 30
        draw.rectangle((legend_x, y, legend_x + 24, y + 14), fill=colors[index])
        draw.text((legend_x + 34, y - 7), _display_metric_label(series.name), fill="#111111", font=small_font)


def _draw_metric_cards(draw: ImageDraw.ImageDraw, chart: ChartSpec, font, small_font) -> None:
    """Use value cards for unrelated recorded metrics, not pseudo-comparable axes."""
    values = [float(value) for value in chart.series[0].values[:3]]
    labels = chart.labels[: len(values)]
    if not values:
        return
    left, right, top, bottom, gap = 90, 1410, 155, 515, 42
    width = (right - left - gap * (len(values) - 1)) / len(values)
    fills = ["#E8F3F8", "#E8F5EE", "#F4ECF7"]
    strokes = ["#0072B2", "#009E73", "#7B3294"]
    for index, (label, value) in enumerate(zip(labels, values)):
        x0 = left + index * (width + gap)
        x1 = x0 + width
        draw.rounded_rectangle((x0, top, x1, bottom), radius=16, fill=fills[index], outline=strokes[index], width=3)
        _centered_wrapped(draw, ((x0 + x1) / 2, top + 88), _display_metric_label(label), small_font, "#334155", width - 44)
        _centered(draw, ((x0 + x1) / 2, top + 205), f"{value:.3f}", _font(46, bold=True), "#172033")
        _centered(draw, ((x0 + x1) / 2, top + 280), "最终记录值", _font(19), "#64748B")


def _draw_panel_axes(draw: ImageDraw.ImageDraw, left: float, top: float, right: float, bottom: float, minimum: float, maximum: float, font) -> None:
    draw.line((left, top, left, bottom), fill="#333333", width=3)
    draw.line((left, bottom, right, bottom), fill="#333333", width=3)
    for tick in range(4):
        y = bottom - (bottom - top) * tick / 3
        value = minimum + (maximum - minimum) * tick / 3
        draw.line((left - 7, y, left, y), fill="#333333", width=2)
        draw.text((left - 62, y - 12), f"{value:.2f}", fill="#444444", font=_font(18))


def _draw_paired(draw: ImageDraw.ImageDraw, chart: ChartSpec, font, small_font) -> None:
    left, top, right, bottom = 180, 65, 1320, 590
    baseline, experiment = chart.series[:2]
    values = [float(value) for series in (baseline, experiment) for value in series.values]
    minimum, maximum = min(values), max(values)
    span = maximum - minimum or 1.0
    minimum -= span * 0.12
    maximum += span * 0.12
    _draw_panel_axes(draw, left, top, right, bottom, minimum, maximum, small_font)
    x_baseline, x_experiment = 470, 1030
    colors = ["#0072B2", "#D55E00", "#009E73", "#7B3294"]
    for index, seed in enumerate(chart.labels):
        y0 = bottom - (baseline.values[index] - minimum) / (maximum - minimum) * (bottom - top)
        y1 = bottom - (experiment.values[index] - minimum) / (maximum - minimum) * (bottom - top)
        color = colors[index % len(colors)]
        draw.line((x_baseline, y0, x_experiment, y1), fill=color, width=4)
        draw.ellipse((x_baseline - 7, y0 - 7, x_baseline + 7, y0 + 7), fill="white", outline=color, width=4)
        draw.ellipse((x_experiment - 7, y1 - 7, x_experiment + 7, y1 + 7), fill=color)
        draw.text((x_experiment + 22, y1 - 14), f"seed {seed}", fill=color, font=small_font)
    _centered(draw, (x_baseline, bottom + 42), baseline.name, small_font, "#111111")
    _centered(draw, (x_experiment, bottom + 42), experiment.name, small_font, "#111111")
    draw.text((20, 18), chart.y_label, fill="#111111", font=small_font)


def _draw_diverging_bar(draw: ImageDraw.ImageDraw, chart: ChartSpec, font, small_font) -> None:
    left, top, right, bottom = 160, 65, 1420, 590
    values = [float(value) for value in chart.series[0].values]
    limit = max(abs(value) for value in values) * 1.25 or 1.0
    zero_y = (top + bottom) / 2
    draw.line((left, zero_y, right, zero_y), fill="#333333", width=3)
    draw.line((left, top, left, bottom), fill="#333333", width=3)
    group = (right - left) / max(1, len(values))
    for index, value in enumerate(values):
        x0 = left + group * index + group * 0.22
        x1 = x0 + group * 0.56
        y = zero_y - value / limit * (bottom - top) / 2
        improved = value < 0 if chart.metric_direction == "lower" else value > 0
        color = "#009E73" if improved else "#D55E00"
        draw.rectangle((x0, min(y, zero_y), x1, max(y, zero_y)), fill=color)
        _centered(draw, ((x0 + x1) / 2, y - 18 if value >= 0 else y + 18), f"{value:+.3f}", small_font, "#111111")
        _centered(draw, ((x0 + x1) / 2, bottom + 35), chart.labels[index], small_font, "#111111")
    draw.text((20, 18), chart.y_label, fill="#111111", font=small_font)
    legend = (
        "负值：指标更优　正值：指标更差"
        if chart.metric_direction == "lower"
        else "正值：指标更高　负值：指标更低"
    )
    draw.text((right - 410, 18), legend, fill="#444444", font=small_font)


def _draw_table(draw: ImageDraw.ImageDraw, rows: list[tuple[str, str]], font, small_font) -> None:
    top, left, width, row_h = 145, 85, 1330, 48
    for index, (key, value) in enumerate(rows):
        y = top + index * row_h
        draw.rectangle((left, y, left + width, y + row_h), fill="#F8FAFC" if index % 2 else "#EFF6FF", outline="#CBD5E1", width=1)
        draw.line((left + 390, y, left + 390, y + row_h), fill="#CBD5E1", width=1)
        draw.text((left + 18, y + 10), key, fill="#1E3A8A", font=small_font)
        draw.text((left + 415, y + 10), value[:58], fill="#172033", font=small_font)


def _draw_series(draw: ImageDraw.ImageDraw, chart: ChartSpec, font, small_font) -> None:
    left, top, right, bottom = 115, 175, 1415, 610
    draw.line((left, top, left, bottom), fill="#64748B", width=3)
    draw.line((left, bottom, right, bottom), fill="#64748B", width=3)
    values = [value for series in chart.series for value in series.values]
    minimum, maximum = min(values), max(values)
    if chart.chart_type in {"bar", "grouped_bar"} and minimum >= 0:
        minimum = 0.0
        maximum = maximum * 1.12 or 1.0
    else:
        span = maximum - minimum or 1.0
        padding = span * 0.12
        minimum -= padding
        maximum += padding
    for tick in range(5):
        y = bottom - (bottom - top) * tick / 4
        value = minimum + (maximum - minimum) * tick / 4
        draw.line((left, y, right, y), fill="#E2E8F0", width=1)
        draw.text((18, y - 12), f"{value:.3g}", fill="#475569", font=small_font)
    colors = ["#2454E6", "#0F9D78", "#E67E22", "#8B5CF6"]
    count = max(1, len(chart.labels))
    group = (right - left) / count
    for series_index, series in enumerate(chart.series):
        if chart.chart_type == "line":
            points = []
            for index, value in enumerate(series.values):
                x = left + group * (index + .5)
                y = bottom - (float(value) - minimum) / (maximum - minimum) * (bottom - top)
                points.append((x, y))
            if len(points) > 1:
                draw.line(points, fill=colors[series_index % len(colors)], width=5)
            for point in points:
                draw.ellipse((point[0] - 6, point[1] - 6, point[0] + 6, point[1] + 6), fill=colors[series_index % len(colors)])
        else:
            bar_width = group * .62 / max(1, len(chart.series))
            for index, value in enumerate(series.values):
                x = left + group * index + group * .19 + series_index * bar_width
                y = bottom - (float(value) - minimum) / (maximum - minimum) * (bottom - top)
                draw.rectangle((x, y, x + bar_width - 4, bottom), fill=colors[series_index % len(colors)])
                draw.text((x, y - 25), f"{value:.3g}", fill="#172033", font=small_font)
        legend_column, legend_row = series_index % 2, series_index // 2
        legend_x = left + legend_column * 620
        legend_y = 30 + legend_row * 48
        draw.rectangle((legend_x, legend_y, legend_x + 22, legend_y + 18), fill=colors[series_index % len(colors)])
        draw.text((legend_x + 34, legend_y - 6), _display_metric_label(series.name), fill="#172033", font=small_font)
    for index, label in enumerate(chart.labels):
        _centered(draw, (left + group * (index + .5), bottom + 35), label[:14], small_font, "#475569")
    draw.text((left, 128), chart.y_label or "指标值", fill="#475569", font=small_font)


def _draw_timeline(draw: ImageDraw.ImageDraw, rows: list[tuple[str, str]], font, small_font) -> None:
    y = 165
    for index, (track, label) in enumerate(rows[:9]):
        color = "#0F9D78" if track == "科学推进" else "#D97706"
        x = 120 + index * 150
        draw.line((x, 310, x + 100, 310), fill="#CBD5E1", width=4)
        draw.ellipse((x + 35, 275, x + 105, 345), fill=color)
        _centered(draw, (x + 70, 310), str(index + 1), small_font, "white")
        _centered(draw, (x + 70, 390), label[:10], small_font, "#172033")
        _centered(draw, (x + 70, 430), track, small_font, color)
    draw.rounded_rectangle((100, 540, 360, 595), radius=10, fill="#E8F7F0", outline="#0F9D78")
    draw.text((122, 556), "绿色：科学推进", fill="#0F6A51", font=small_font)
    draw.rounded_rectangle((390, 540, 680, 595), radius=10, fill="#FFF7E8", outline="#D97706")
    draw.text((412, 556), "橙色：工程处置", fill="#9A5A00", font=small_font)


def _centered(draw: ImageDraw.ImageDraw, position: tuple[float, float], value: str, font, fill: str) -> None:
    box = draw.textbbox((0, 0), value, font=font)
    draw.text((position[0] - (box[2] - box[0]) / 2, position[1] - (box[3] - box[1]) / 2), value, fill=fill, font=font)


def _wrapped_lines(draw: ImageDraw.ImageDraw, value: str, font, max_width: float) -> list[str]:
    text = re.sub(r"\s+", " ", str(value)).strip()
    if not text:
        return [""]
    tokens = text.split(" ") if " " in text else list(text)
    lines: list[str] = []
    current = ""
    for token in tokens:
        candidate = f"{current} {token}".strip() if " " in text else current + token
        if current and draw.textbbox((0, 0), candidate, font=font)[2] > max_width:
            lines.append(current)
            current = token
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def _centered_wrapped(
    draw: ImageDraw.ImageDraw,
    position: tuple[float, float],
    value: str,
    font,
    fill: str,
    max_width: float,
) -> None:
    lines = _wrapped_lines(draw, value, font, max_width)
    line_height = draw.textbbox((0, 0), "Hg", font=font)[3] + 6
    top = position[1] - line_height * len(lines) / 2
    for index, line in enumerate(lines):
        _centered(draw, (position[0], top + line_height * (index + 0.5)), line, font, fill)
