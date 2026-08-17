from __future__ import annotations

"""Grounded, deterministic figures for the research-report export path.

The report writer is allowed to choose *which* eligible figures matter to the
narrative.  This module owns the other half of the contract: it derives every
number from persisted artifacts and renders the resulting FigureSpec without
calling a model.  A missing data series is represented explicitly, never as a
plausible-looking replacement chart.
"""

from io import BytesIO
from typing import Any, Literal

from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, Field


class ChartSeries(BaseModel):
    name: str
    values: list[float]


class ChartSpec(BaseModel):
    chart_type: Literal["bar", "grouped_bar", "line", "table", "workflow", "timeline"]
    labels: list[str] = Field(default_factory=list)
    series: list[ChartSeries] = Field(default_factory=list)
    y_label: str = ""
    metric_name: str = ""
    metric_direction: str = ""
    rows: list[tuple[str, str]] = Field(default_factory=list)


class FigureSpec(BaseModel):
    figure_id: str
    kind: Literal["workflow", "control_variables", "seed_comparison", "main_comparison", "training_curve", "timeline"]
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
    schema_version: str = "round6.visual-report.v1"
    language: Literal["zh-CN", "en"] = "zh-CN"
    renderer: str = "pillow-deterministic-v1"
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
        "research_workflow": FigureSpec(
            figure_id="research_workflow",
            kind="workflow",
            title="图 1 研究问题到科学结论的证据链",
            caption="图中每个阶段均对应已持久化的研究产物；箭头表示研究论证顺序，而非未执行的实验。",
            scientific_purpose="呈现研究问题、假设、受控实验、统计评价与结论之间的可追溯关系。",
            source_artifact_ids=source_ids,
            chart=ChartSpec(chart_type="workflow", labels=["研究问题", "研究假设", "实验设计", "真实实验", "科学结论"]),
        ),
        "control_variables": _control_figure(plan, plan_artifact),
        "seed_comparison": _seed_figure(result, result_artifact),
        "main_comparison": _main_comparison_figure(result, result_artifact),
        "training_curve": _training_curve_figure(result, result_artifact),
        "workflow_timeline": _timeline_figure(artifacts),
    }
    figures: list[FigureSpec] = []
    omitted: list[OmittedFigureSpec] = []
    for figure_id in ("research_workflow", "control_variables", "seed_comparison", "main_comparison", "training_curve", "workflow_timeline"):
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
    """Render one validated FigureSpec to a stable, CJK-safe PNG."""
    spec = figure if isinstance(figure, FigureSpec) else FigureSpec.model_validate(figure)
    image = Image.new("RGB", (1500, 760), "white")
    draw = ImageDraw.Draw(image)
    title_font = _font(34, bold=True)
    label_font = _font(25)
    small_font = _font(20)
    draw.text((58, 38), spec.title, fill="#172033", font=title_font)
    chart = spec.chart
    if chart.chart_type == "workflow":
        _draw_workflow(draw, chart.labels, label_font, small_font)
    elif chart.chart_type == "timeline":
        _draw_timeline(draw, chart.rows, label_font, small_font)
    elif chart.chart_type == "table":
        _draw_table(draw, chart.rows, label_font, small_font)
    elif chart.chart_type in {"bar", "grouped_bar", "line"}:
        _draw_series(draw, chart, label_font, small_font)
    else:  # defensive schema completeness; impossible for the current union.
        raise ValueError(f"REPORT_FIGURE_RENDER_UNSUPPORTED:{chart.chart_type}")
    output = BytesIO()
    image.save(output, format="PNG", optimize=False)
    return output.getvalue()


def _control_figure(plan: dict[str, Any], plan_artifact: Any) -> FigureSpec | None:
    parameters = plan.get("parameters") if isinstance(plan.get("parameters"), dict) else {}
    seeds = plan.get("seeds") if isinstance(plan.get("seeds"), list) else []
    rows = [(str(key), _brief(value)) for key, value in parameters.items() if value not in (None, "", [], {})]
    if seeds:
        rows.append(("随机种子", "、".join(str(value) for value in seeds)))
    if not rows:
        return None
    return FigureSpec(
        figure_id="control_variables", kind="control_variables", title="图 2 受控变量与评价设置",
        caption="变量、随机种子和评价设置均直接来自已冻结的实验计划。",
        scientific_purpose="说明比较中保持一致的条件，避免把工程变更误认为科学效应。",
        source_artifact_ids=_ids(plan_artifact), chart=ChartSpec(chart_type="table", rows=rows[:12]),
    )


def _main_comparison_figure(result: dict[str, Any], result_artifact: Any) -> FigureSpec | None:
    metrics = result.get("metrics") if isinstance(result.get("metrics"), dict) else {}
    rows = [(str(key), float(value)) for key, value in metrics.items() if _finite_number(value) and not _dispersion_metric(str(key))]
    if len(rows) < 2:
        return None
    return FigureSpec(
        figure_id="main_comparison", kind="main_comparison", title="图 3 主要实验指标对比",
        caption="柱状值直接取自最终有效实验结果；不包含失败 Attempt 或推测值。",
        scientific_purpose="呈现主要对照模型在同一最终结果记录中的可比较指标。",
        source_artifact_ids=_ids(result_artifact),
        chart=ChartSpec(chart_type="bar", labels=[key for key, _ in rows[:8]], series=[ChartSeries(name="最终结果", values=[value for _, value in rows[:8]])], y_label="记录的指标值"),
    )


def _seed_figure(result: dict[str, Any], result_artifact: Any) -> FigureSpec | None:
    raw = result.get("seed_results") or result.get("per_seed_results") or result.get("seed_metrics")
    entries = [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []
    if len(entries) < 2:
        return None
    common: list[str] = []
    for key in entries[0]:
        if key in {"seed", "random_seed"} or _dispersion_metric(str(key)):
            continue
        if all(_finite_number(item.get(key)) for item in entries):
            common.append(str(key))
    if not common:
        return None
    labels = [str(item.get("seed") or item.get("random_seed") or index + 1) for index, item in enumerate(entries)]
    return FigureSpec(
        figure_id="seed_comparison", kind="seed_comparison", title="图 4 分随机种子实验对比",
        caption="每根柱对应同一持久化结果中的一个随机种子；聚合结论仍以最终审计结果为准。",
        scientific_purpose="展示随机初始化下的稳定性，防止只报告单次运行。",
        source_artifact_ids=_ids(result_artifact),
        chart=ChartSpec(chart_type="grouped_bar", labels=labels, series=[ChartSeries(name=key, values=[float(item[key]) for item in entries]) for key in common[:4]], y_label="记录的指标值"),
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
        figure_id="training_curve", kind="training_curve", title="图 5 训练过程曲线",
        caption="曲线只在真实 epoch/step 指标已持久化时生成。",
        scientific_purpose="描述训练过程而非替代最终的保留集评价。",
        source_artifact_ids=_ids(result_artifact),
        chart=ChartSpec(chart_type="line", labels=labels, series=[ChartSeries(name=key, values=[float(item[key]) for item in rows]) for key in numeric_keys[:3]], y_label="记录的训练指标"),
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
        figure_id="workflow_timeline", kind="timeline", title="图 6 工作流与研究推进时间线",
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


def _draw_table(draw: ImageDraw.ImageDraw, rows: list[tuple[str, str]], font, small_font) -> None:
    top, left, width, row_h = 145, 85, 1330, 48
    for index, (key, value) in enumerate(rows):
        y = top + index * row_h
        draw.rectangle((left, y, left + width, y + row_h), fill="#F8FAFC" if index % 2 else "#EFF6FF", outline="#CBD5E1", width=1)
        draw.line((left + 390, y, left + 390, y + row_h), fill="#CBD5E1", width=1)
        draw.text((left + 18, y + 10), key, fill="#1E3A8A", font=small_font)
        draw.text((left + 415, y + 10), value[:58], fill="#172033", font=small_font)


def _draw_series(draw: ImageDraw.ImageDraw, chart: ChartSpec, font, small_font) -> None:
    left, top, right, bottom = 115, 145, 1415, 610
    draw.line((left, top, left, bottom), fill="#64748B", width=3)
    draw.line((left, bottom, right, bottom), fill="#64748B", width=3)
    values = [value for series in chart.series for value in series.values]
    minimum, maximum = min(values), max(values)
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
        draw.rectangle((right - 235, top + series_index * 34, right - 215, top + 20 + series_index * 34), fill=colors[series_index % len(colors)])
        draw.text((right - 205, top - 4 + series_index * 34), series.name[:18], fill="#172033", font=small_font)
    for index, label in enumerate(chart.labels):
        _centered(draw, (left + group * (index + .5), bottom + 35), label[:14], small_font, "#475569")
    draw.text((left, 92), chart.y_label or "指标值", fill="#475569", font=small_font)


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
