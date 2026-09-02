from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, InvalidOperation
import re
from typing import Any

from backend.app.providers.llm import LLMProvider
from backend.app.report_visualization import build_report_spec
from backend.app.workflow.literature_contract import literature_summary_text
from backend.app.workflow.research_synthesis import stable_paper_id
from backend.app.workflow.research_state import (
    active_plan_for_report,
    build_research_state,
    resolve_fact_path,
)


SECTION_SPECS = (
    {
        "id": "background",
        "title": "一、研究背景与研究问题",
        "purpose": "从应用背景和已有事实逐步收束到具体研究问题，说明研究对象、现实限制和验证边界。",
        "paragraphs": "2-3",
        "min_chars": 500,
    },
    {
        "id": "hypothesis",
        "title": "二、理论依据与研究假设",
        "purpose": "结合直接相关文献建立已有认识、知识缺口、研究假设和可证伪预测之间的推导链。",
        "paragraphs": "3-4",
        "min_chars": 700,
    },
    {
        "id": "method",
        "title": "三、数据集与实验方法",
        "purpose": "说明数据集、预处理、模型、训练和评估方法，以及这些选择如何服务于假设检验。",
        "paragraphs": "3-5",
        "min_chars": 800,
    },
    {
        "id": "design",
        "title": "四、实验设计与评价标准",
        "purpose": "解释基线与实验组、受控变量、随机种子、评价指标、成功阈值和比较方式。",
        "paragraphs": "3-4",
        "min_chars": 700,
    },
    {
        "id": "iteration",
        "title": "五、实验迭代与方法修正",
        "purpose": "按照时间顺序说明每轮发现的问题、分析依据、所作修正及修正后的结果，不粘贴原始反馈。",
        "paragraphs": "4-7",
        "min_chars": 1100,
    },
    {
        "id": "results",
        "title": "六、实验结果与分析",
        "purpose": "先报告最终受控实验的关键数值，再比较目标阈值，解释结果能够和不能够支持什么。",
        "paragraphs": "4-6",
        "min_chars": 900,
    },
    {
        "id": "discussion",
        "title": "七、讨论与局限性",
        "purpose": "讨论结果的可能含义、方法学收获、统计和实验限制，以及外推时必须保留的条件。",
        "paragraphs": "3-5",
        "min_chars": 700,
    },
    {
        "id": "conclusion",
        "title": "八、研究结论与复现说明",
        "purpose": "形成唯一的收束性结论，并用连贯文字说明复现实验所需的数据、代码、参数和随机种子。",
        "paragraphs": "3-4",
        "min_chars": 650,
    },
)

REPORT_FIGURE_IDS = (
    "model_structure",
    "method_pipeline",
    "control_variables",
    "workflow_timeline",
    "training_curve",
    "main_comparison",
    "seed_comparison",
    "seed_delta",
)

FORBIDDEN_READER_FIELDS = (
    "artifact_id",
    "contract id",
    "contract_id",
    "inspection status",
    "content_fingerprint",
    "sha256",
    "file count",
    "total bytes",
    "provider_mode",
    "fallback_used",
    "D:\\",
    "C:\\",
)
AI_FILLER = (
    "作为AI",
    "作为 AI",
    "综上所述可以看出",
    "具有重要意义",
    "本文旨在",
    "值得注意的是",
)
HARD_AUDIT_CODES = frozenset(
    {
        "numeric_mismatch",
        "unit_mismatch",
        "fabricated_fact",
        "fabricated_citation",
        "verdict_inversion",
        "scope_overreach",
        "unverified_reference",
        "missing_core_section",
        "internal_leak",
        "cross_section_contradiction",
    }
)

PAPER_CITATION_PATTERN = re.compile(r"\bPAPER-[a-z0-9]+\b", re.IGNORECASE)

NUMERIC_CLAIM_PATTERN = re.compile(
    r"(?<![\d.])(?P<number>[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)"
    r"(?P<unit>\s*(?:%|％|(?:个\s*)?百分点|percentage[\s-]+points?|pp))?"
)
DERIVED_NUMERIC_CLAIM_TERMS = (
    "差值",
    "差异",
    "增量",
    "提升",
    "下降",
    "difference",
    "delta",
    "change",
    "improvement",
    "gain",
)
DERIVED_NUMERIC_PATH_TERMS = (
    "difference",
    "delta",
    "change",
    "improvement",
    "gain",
    "diff_",
    "diff.",
)
ABSOLUTE_POINT_PATH_TERMS = (
    "difference",
    "delta",
    "diff_",
    "diff.",
    "std",
    "stdev",
    "standard_deviation",
    "standard_error",
    "stderr",
    "confidence_interval",
    "ci_width",
)
RELATIVE_CHANGE_PATH_TERMS = (
    "relative_change",
    "relative_difference",
    "relative_improvement",
    "percent_change",
)
RELATIVE_CHANGE_CLAIM_TERMS = (
    "相对",
    "relative",
)

INCONCLUSIVE_OVERCLAIM_PATTERNS = (
    re.compile(r"统计(?:上)?(?:不显著的)?中性(?:状态|表现|结果)?"),
    re.compile(r"统计(?:上)?(?:等效|等价)|无本质(?:差异|区别)|泛化能力相当"),
    re.compile(
        r"(?:支持了|得到数据支持|证实了|证明了)[^。；]{0,90}"
        r"(?:无法|不能|并未)[^。；]{0,30}(?:显著)?(?:提升|下降|退化)"
    ),
    re.compile(r"(?:直接)?否定了?[^。；]{0,90}(?:下降|退化|预测方向|略微降低)"),
    re.compile(
        r"(?:无法|不能|并未)[^。；]{0,40}(?:显著)?提升[^。；]{0,50}"
        r"(?:得到数据支持|获得支持)"
    ),
    re.compile(
        r"(?:未显著提升|未建立显著差异)[^。；]{0,120}"
        r"(?:抵消|平衡|改变了?[^。；]{0,30}收敛轨迹)"
    ),
    re.compile(r"准确率(?:轻微)?优势暗示[^。；]{0,120}(?:抵消|收敛)"),
    re.compile(r"(?:达到|处于)[^。；]{0,50}平衡(?:状态)?"),
    re.compile(
        r"(?:无法|不能|未能)[^。；]{0,40}(?:显著)?提升[^。；]{0,90}"
        r"(?:得到|获得)[^。；]{0,50}支持[^。；]{0,90}"
        r"(?:未(?:能)?拒绝|没有拒绝)[^。；]{0,40}零假设"
    ),
    re.compile(
        r"(?:未(?:能)?拒绝|没有拒绝)[^。；]{0,50}零假设[^。；]{0,90}"
        r"(?:支持|证明|表明)[^。；]{0,90}(?:无法|不能|未能)[^。；]{0,30}"
        r"(?:显著)?提升"
    ),
    re.compile(
        r"(?:部分)?(?:证伪|否定)[^。；]{0,90}(?:显著)?(?:下降|降低|退化)"
        r"(?:[^。；]{0,40}(?:预测|方向))?"
    ),
    re.compile(
        r"(?:无法|不能|未能)[^。；]{0,70}(?:显著)?(?:提升|增益)[^。；]{0,180}"
        r"(?:得到|获得|提供了?)[^。；]{0,60}(?:支持|证实)"
    ),
    re.compile(r"(?:统计(?:上)?[^。；]{0,30})?(?:中性|平衡)(?:效应|状态|表现|结果)?"),
    re.compile(
        r"(?:未|没有|并未)[^。；]{0,40}(?:造成|出现|观察到)[^。；]{0,40}"
        r"(?:系统性|实质性)[^。；]{0,30}(?:下降|降低|退化)"
    ),
)

INCONCLUSIVE_OVERCLAIM_RULE_IDS = (
    "statistical_neutrality_claim",
    "equivalence_claim_without_test",
    "support_for_no_improvement_claim",
    "direct_directional_denial",
    "reverse_support_for_no_improvement_claim",
    "mechanism_claim_from_nonsignificance",
    "mechanism_claim_from_point_estimate",
    "balance_state_claim",
    "nonrejection_supports_no_improvement",
    "nonrejection_supports_no_improvement_reverse",
    "directional_prediction_falsified_without_evidence",
    "support_for_no_improvement_claim_broad",
    "neutral_or_balanced_effect_claim",
    "unqualified_no_decline_claim",
)

UNOBSERVED_CONVERGENCE_PATTERNS = (
    re.compile(r"(?:基于|来自)[^。；]{0,30}(?:预分析|收敛特性的初步分析)"),
    re.compile(r"(?:旨在确保|足以使|保证)[^。；]{0,90}(?:收敛|欠训练)"),
    re.compile(r"直接观测[^。；]{0,70}收敛轨迹"),
)

UNPERFORMED_TEST_PATTERN = re.compile(
    r"未进行[^。；]{0,50}(?:显著性检验|统计检验)"
)


class ReportFactAuditError(ValueError):
    def __init__(
        self,
        failures: list[dict],
        *,
        title: str,
        abstract: str,
        sections: list[dict],
        audit: dict,
        facts: dict | None = None,
    ) -> None:
        codes = list(dict.fromkeys(item["code"] for item in failures))
        super().__init__("REPORT_FACT_AUDIT_FAILED:" + ",".join(codes))
        self.failures = failures
        self.draft = {
            "Report Title": title,
            "Paper Title": title,
            "Paper Abstract": abstract,
            "Narrative Sections": sections,
            "Report Evidence": deepcopy(facts or {}),
            "Report Status": {
                "complete": False,
                "grounded_in_artifacts": False,
                "failure_codes": codes,
            },
        }
        self.audit = {
            "hard_failures": failures,
            "verification": audit,
        }


class WriterAgent:
    name = "Writer Skill"

    def __init__(self, llm_provider: LLMProvider) -> None:
        self.llm_provider = llm_provider

    def build_report(self, artifacts: list, *, instructions: str = "") -> dict:
        by_type = self._latest_by_type(artifacts)
        research_state = build_research_state(artifacts)
        problem = by_type.get("problem", {})
        hypothesis = self._active_hypothesis(by_type, research_state)
        plan = active_plan_for_report(by_type.get("plan", {}), research_state)
        result = by_type.get("experiment_result", {})
        deterministic_evidence = by_type.get("result_evidence", {})
        revision = by_type.get("revision", {})
        iteration_summary = self._iteration_summary(artifacts, result, revision)
        verified_references = self._verified_references(artifacts)
        facts = self._fact_sheet(
            problem,
            hypothesis,
            plan,
            result,
            revision,
            iteration_summary,
            verified_references,
            research_state,
            deterministic_evidence,
        )

        outline = self.llm_provider.generate_json(
            "writer.report_outline",
            {"facts": facts, "required_sections": list(SECTION_SPECS)},
            {
                "title": "string",
                "central_question": "string",
                "narrative_logic": "string",
                "section_plans": ["object"],
                "reference_selection": ["string"],
            },
            instructions=self._outline_instructions(instructions),
        )
        title = self._clean_text(
            outline.get("title")
            or plan.get("objective")
            or hypothesis.get("claim")
            or "实验迭代与科学假设验证报告"
        )
        section_plans = {
            str(item.get("id")): item
            for item in (outline.get("section_plans") or [])
            if isinstance(item, dict) and item.get("id")
        }

        sections: list[dict[str, Any]] = []
        previous_tail = ""
        for spec in SECTION_SPECS:
            section_input = {
                **spec,
                "outline_plan": section_plans.get(spec["id"], {}),
            }
            drafted = self.llm_provider.generate_json(
                "writer.report_section",
                {
                    "facts": facts,
                    "section": section_input,
                    "previous_section_tail": previous_tail,
                    "written_section_titles": [item["title"] for item in sections],
                },
                {
                    "id": "string",
                    "title": "string",
                    "paragraphs": ["string"],
                    "subsections": ["object"],
                    "citations": ["string"],
                },
                instructions=self._section_instructions(spec, instructions),
            )
            section = self._normalize_section(drafted, spec)
            issues = self._section_quality_issues(section, spec)
            if issues:
                revised = self.llm_provider.generate_json(
                    "writer.revise_report_section",
                    {
                        "facts": facts,
                        "section": section,
                        "required_section": section_input,
                        "quality_issues": issues,
                        "previous_section_tail": previous_tail,
                    },
                    {
                        "id": "string",
                        "title": "string",
                        "paragraphs": ["string"],
                        "subsections": ["object"],
                        "citations": ["string"],
                    },
                    instructions=self._section_instructions(spec, instructions)
                    + "\n必须逐项修复质量问题，不能只解释问题。",
                )
                candidate = self._normalize_section(revised, spec)
                if candidate["paragraphs"] or candidate["subsections"]:
                    section = candidate
            hard_issues = self._section_hard_quality_issues(section)
            if hard_issues:
                raise ValueError(
                    "REPORT_SECTION_QUALITY_FAILED:"
                    + str(spec["id"])
                    + ":"
                    + "；".join(hard_issues)
                )
            sections.append(section)
            previous_tail = self._section_tail(section)

        abstract_response = self.llm_provider.generate_json(
            "writer.report_abstract",
            {
                "title": title,
                "facts": facts,
                "sections": sections,
            },
            {"abstract": "string", "keywords": ["string"]},
            instructions=(
                "在所有正文完成后撰写中文摘要。用一个完整段落依次交代背景、问题、方法、"
                "最终关键数值、结论和适用边界，350至600字。不得写目录式摘要、空泛目的句或新增事实。"
            ),
        )
        abstract = self._redact_reader_text(
            self._clean_text(abstract_response.get("abstract"))
        )
        if not abstract:
            abstract = self._fallback_abstract(facts)

        audit = self.llm_provider.generate_json(
            "writer.audit_report",
            {
                "title": title,
                "abstract": abstract,
                "sections": sections,
                "facts": facts,
            },
            {
                "accepted": "boolean",
                "hard_failures": ["object"],
                "revision_required": ["object"],
                "soft_style_issues": ["object"],
                "section_scores": ["object"],
                "revised_abstract": "string",
                "section_revisions": ["object"],
            },
            instructions=(
                "执行内部研究报告质量审核，不判断作者身份。先核对事实、实验数值、百分比与百分点、"
                "引用、结论边界和内部信息泄露，再检查章节职责、逻辑衔接、重复内容与自然中文表达。"
                "hard_failures只列出应用替换段落后仍无法解决的事实或证据问题；单个套话、段落偏短、"
                "语言正式或可预测只能进入soft_style_issues，绝不能单独阻止导出。"
                "每个hard_failure必须包含code、section_id、非负整数paragraph_index、claim、"
                "source_path、source_fact和required_correction，其中source_path必须指向facts中的现有字段。"
                "paragraph_index统一从0开始；摘要用section_id=abstract、paragraph_index=0。"
                "numeric_mismatch还须给出claimed_value，即claim中有争议的单个数值及其单位，"
                "不能用同段其他指标的正确数字代替该数值。"
                "code只能是numeric_mismatch、unit_mismatch、"
                "fabricated_fact、fabricated_citation、verdict_inversion、scope_overreach、"
                "unverified_reference、missing_core_section、internal_leak或"
                "cross_section_contradiction；grounding、quality、style等笼统标签无效，"
                "证据字段不完整的问题应放入revision_required。"
                "final_revision.verdict中的partial只是流程状态，不能单独证明主假设得到部分支持；"
                "判断主假设时必须同时读取supported_claims、unsupported_claims、failed_criteria和feedback。"
                "若正文明确保留均值方向或参数效率，同时明确主假设未获统计支持、次要终点未满足，"
                "不得把这种审慎结论标为verdict_inversion。"
                "按正文显示精度正确四舍五入的数值不是numeric_mismatch；差值、增量或提升等派生数值"
                "必须引用facts中对应的difference、delta或同义派生字段，不能只引用某一侧的均值。"
                "比例事实x可等价显示为100x%；绝对差值、标准差、标准误或置信区间宽度还可等价显示为"
                "100x个百分点。数学等价或术语偏好不得列为hard_failure；只有真实数值矛盾，或相对"
                "百分比与绝对百分点混淆，才分别使用numeric_mismatch或unit_mismatch。"
                "显著性检验p值不显著或置信区间包含零，只能说明未建立差异或提升证据，不能证明两组"
                "等效、相同、无本质区别，也不能证实‘无法提升’或某机制无效；除非facts明确提供预设"
                "等效界值和等效性检验。违反这一边界属于scope_overreach或verdict_inversion。"
                "plan.parameters.additional_sections中的训练预算说明只是设计阶段理由，不是已经执行的"
                "收敛预实验；若final_result没有真实epoch_metrics/training_history，不得声称做过收敛"
                "初步分析、记录了完整逐epoch曲线或由曲线证明收敛滞后。"
                "只在确有问题时给出完整替换段落，不得改变实验数值或补造事实。\n"
                + instructions
            ),
        )
        abstract, sections = self._apply_audit_revisions(abstract, sections, audit)
        self._ensure_sections_exportable(sections)
        hard_failures = self._validated_hard_failures(
            audit.get("hard_failures"),
            facts=facts,
            sections=sections,
            abstract=abstract,
        )
        if hard_failures:
            repair = self.llm_provider.generate_json(
                "writer.repair_report_audit",
                {
                    "title": title,
                    "abstract": abstract,
                    "sections": sections,
                    "facts": facts,
                    "hard_failures": hard_failures,
                    "revision_required": audit.get("revision_required") or [],
                },
                {
                    "revised_abstract": "string",
                    "section_revisions": ["object"],
                },
                instructions=(
                    "只修复列出的、带完整证据的事实问题。逐项核对section_id、claim、source_fact和"
                    "source_path、required_correction，给出完整替换段落；保留事实表中的数值、单位、否定结果、"
                    "不确定性和适用边界，不得新增事实或引用。不要解释修订过程。"
                ),
            )
            abstract, sections = self._apply_audit_revisions(abstract, sections, repair)
            self._ensure_sections_exportable(sections)
        abstract, sections = self._repair_scientific_boundaries(
            title,
            abstract,
            sections,
            facts,
        )
        self._verify_report(title, abstract, sections, facts)
        # The bibliography is a trace of the whole evidence search for this
        # run, not only of the subset the narrative happened to cite.  Inline
        # citations are still audited independently in ``_verify_report``.
        references = self._all_exportable_references(verified_references)

        report = {
            "Report Title": title,
            "Paper Title": title,
            "Paper Abstract": abstract,
            "Keywords": [
                self._clean_text(item)
                for item in (abstract_response.get("keywords") or [])
                if self._clean_text(item)
            ][:6],
            "Narrative Sections": sections,
            # Compatibility and evidence fields remain machine-readable but are
            # intentionally not expanded as reader-facing Word chapters.
            "Problem Statement": problem.get("problem_statement")
            or problem.get("problem_input")
            or facts["research_question"],
            "Rationale": self._section_text(sections[1]),
            "Technical Details": (plan.get("procedure") or {}).get("steps")
            or (plan.get("method") or {}).get("components")
            or [],
            "Datasets": plan.get("dataset") or {},
            "Source": [reference.get("title") for reference in references if reference.get("title")],
            "Target": hypothesis.get("claim") or plan.get("objective") or "",
            "Methods": (plan.get("method") or {}).get("components")
            or (plan.get("procedure") or {}).get("steps")
            or [],
            "Experiments": {
                "comparisons": plan.get("comparisons") or [],
                "evaluations": plan.get("evaluations") or [],
                "parameters": plan.get("parameters") or {},
                "seeds": plan.get("seeds") or [],
                "procedure": plan.get("procedure") or {},
            },
            "Results": result,
            "Report Evidence": deepcopy(facts),
            "References": references,
            "Iteration Summary": iteration_summary,
            "Limitations": self._limitations(result, revision),
            "Research Conclusion": self._section_text(sections[-1]),
            "Report Spec": build_report_spec(
                artifacts,
                selected_figure_ids=list(REPORT_FIGURE_IDS),
                decision_rationale=(
                    "按研究叙事分布纳入可用图表：方法与设计阶段先建立视觉锚点，"
                    "迭代阶段呈现过程证据，结果章节保留最终比较与随机种子差异。"
                ),
                language="zh-CN",
            ),
            "Reproducibility": {
                "experiment_id": result.get("experiment_id", ""),
                "provider": result.get("provider", ""),
                "parameters": result.get("parameters") or plan.get("parameters") or {},
                "seeds": result.get("seeds") or plan.get("seeds") or [],
                "environment": result.get("environment") or {},
            },
            "Report Status": {
                "complete": True,
                "grounded_in_artifacts": True,
                "real_experiment": result.get("is_real_experiment") is True,
                "verified_reference_count": len(references),
                "feedback_iteration": int(revision.get("iteration") or 0),
                "final_verdict": revision.get("verdict")
                or result.get("verdict")
                or result.get("status")
                or "",
                "paper_writing_available": True,
                "paper_writing_automatic": False,
            },
        }
        return report

    def repair_report(
        self,
        report: dict,
        artifacts: list,
        issues: list[str],
    ) -> dict:
        """Repair only paragraphs named by the report reviewer."""
        by_type = self._latest_by_type(artifacts)
        research_state = build_research_state(artifacts)
        problem = by_type.get("problem", {})
        hypothesis = self._active_hypothesis(by_type, research_state)
        plan = active_plan_for_report(by_type.get("plan", {}), research_state)
        result = by_type.get("experiment_result", {})
        deterministic_evidence = by_type.get("result_evidence", {})
        revision = by_type.get("revision", {})
        iteration_summary = self._iteration_summary(artifacts, result, revision)
        facts = self._fact_sheet(
            problem,
            hypothesis,
            plan,
            result,
            revision,
            iteration_summary,
            self._verified_references(artifacts),
            research_state,
            deterministic_evidence,
        )
        # Continue from the same source snapshot that the reviewer saw.
        if isinstance(report.get("Report Evidence"), dict):
            facts = deepcopy(report["Report Evidence"])
        candidate = deepcopy(report)
        sections = deepcopy(candidate.get("Narrative Sections") or [])
        abstract = self._clean_text(candidate.get("Paper Abstract"))
        repaired = self.llm_provider.generate_json(
            "writer.repair_supervisor_report",
            {
                "title": candidate.get("Paper Title") or candidate.get("Report Title") or "",
                "abstract": abstract,
                "sections": sections,
                "facts": facts,
                "blocking_issues": list(issues),
            },
            {
                "revised_abstract": "string",
                "section_revisions": ["object"],
            },
            instructions=(
                "只修复blocking_issues直接指出的段落，不重写整篇报告。section_revisions中的每项"
                "必须给出section_id、paragraph_index和replacement_paragraph；paragraph_index从0开始。"
                "涉及引用时同时返回该节完整citations，只用facts.verified_references中的paper_id，"
                "正文用[PAPER-...]标记；无法由来源原文支持的论点应删改，不得换一篇文献凑数。"
                "除非问题明确涉及摘要，否则revised_abstract返回空字符串。保留所有真实数值、否定结果、"
                "不确定性和适用边界，不得新增事实、实验、引用或图表，也不要解释修改过程。"
            ),
        )
        abstract, sections = self._apply_audit_revisions(abstract, sections, repaired)
        self._ensure_sections_exportable(sections)
        abstract, sections = self._repair_scientific_boundaries(
            str(candidate.get("Paper Title") or ""), abstract, sections, facts,
        )
        self._verify_report(str(candidate.get("Paper Title") or ""), abstract, sections, facts)
        references = self._all_exportable_references(
            facts.get("verified_references") or [],
        )
        candidate["Paper Abstract"] = abstract
        candidate["Narrative Sections"] = sections
        candidate["Report Evidence"] = deepcopy(facts)
        candidate["References"] = references
        candidate["Source"] = [item["title"] for item in references]
        candidate.setdefault("Report Status", {})["verified_reference_count"] = len(references)
        if len(sections) > 1:
            candidate["Rationale"] = self._section_text(sections[1])
        if sections:
            candidate["Research Conclusion"] = self._section_text(sections[-1])
        return candidate

    @staticmethod
    def _report_citations(abstract: str, sections: list[dict]) -> list[str]:
        citations = PAPER_CITATION_PATTERN.findall(abstract)
        for section in sections:
            citations.extend(str(item) for item in section.get("citations") or [])
            paragraphs = list(section.get("paragraphs") or [])
            for child in section.get("subsections") or []:
                paragraphs.extend(child.get("paragraphs") or [])
            for paragraph in paragraphs:
                citations.extend(PAPER_CITATION_PATTERN.findall(str(paragraph)))
        return list(dict.fromkeys(citations))

    def _verify_report(self, title: str, abstract: str, sections: list[dict], facts: dict) -> None:
        """One final gate for both newly written and repaired reports."""
        references = facts.get("verified_references") or []
        unresolved = [
            token for token in self._report_citations(abstract, sections)
            if len(self._select_references(references, [], [token])) != 1
        ]
        if unresolved:
            failures = [{
                "code": "unverified_reference", "section_id": "references",
                "paragraph_index": 0, "claim": token,
                "source_path": "verified_references", "source_fact": "本次已验证文献清单",
                "required_correction": "引用必须唯一对应已有来源；无法支持的论点应删改，不得补造引用。",
            } for token in unresolved]
            raise ReportFactAuditError(
                failures, title=title, abstract=abstract, sections=sections, audit={}, facts=facts,
            )
        verification = self.llm_provider.generate_json(
            "writer.verify_report_audit",
            {"title": title, "abstract": abstract, "sections": sections, "facts": facts},
            {"hard_failures": ["object"]},
            instructions=(
                "核对最终正文和摘要的数值、单位、比较方向、检验方法、引用支持关系与结论边界。"
                "仅使用同一份facts；实验统计以deterministic_result_evidence为准，计划不是已执行证据。"
                "每个hard_failure给出code、section_id、从0开始的paragraph_index、claim、source_path、"
                "source_fact、required_correction；摘要用abstract和0，子章节段落接在所属章节段落之后编号。"
                "numeric_mismatch须给claimed_value（争议的单个数值及单位），不要拿同段其他数字代替。"
                "按显示精度正确四舍五入不算错误；比例x与100x%等价，绝对差值或标准差可用百分点；"
                "差值必须对应差值事实，不得用单侧均值判错。"
                "每个引用不仅要存在，还必须由verified_references中available_text或abstract支持具体论点；"
                "仅标题相关或元数据已验证不能证明论点。无法核实的断言必须删改或明确标为假设。"
                "partial不是主张得到支持的证据；非显著或跨零区间不证明等效、无效或机制成立。"
                "只列出有原文位置和事实依据的未解决问题，无问题返回hard_failures=[]。"
            ),
        )
        if not isinstance(verification.get("hard_failures"), list):
            raise ValueError("REPORT_AUDIT_INVALID:hard_failures_required")
        failures = self._validated_hard_failures(
            verification.get("hard_failures"), facts=facts, sections=sections, abstract=abstract,
        )
        if failures:
            raise ReportFactAuditError(
                failures, title=title, abstract=abstract, sections=sections, audit=verification, facts=facts,
            )

    def _repair_scientific_boundaries(
        self,
        title: str,
        abstract: str,
        sections: list[dict],
        facts: dict,
    ) -> tuple[str, list[dict]]:
        """Repair a small set of deterministic inferential-boundary violations.

        The semantic auditor remains responsible for broad scientific review.
        This guard only covers two mechanically decidable cases that models
        repeatedly paraphrased around: non-significance is not equivalence or
        proof of no effect, and a planning rationale is not an executed
        convergence pilot.
        """
        for _ in range(1):
            issues = self._scientific_boundary_issues(
                abstract,
                sections,
                facts,
            )
            if not issues:
                return abstract, sections
            repaired = self.llm_provider.generate_json(
                "writer.repair_report_audit",
                {
                    "title": title,
                    "abstract": abstract,
                    "sections": sections,
                    "facts": facts,
                    "hard_failures": issues,
                    "revision_required": [],
                },
                {
                    "revised_abstract": "string",
                    "section_revisions": ["object"],
                },
                instructions=(
                    "只改写hard_failures点名的摘要或段落，并保持段落原有主题和可核对数值。"
                    "section_revisions必须给出section_id、从0开始的paragraph_index和完整"
                    "replacement_paragraph；摘要问题必须返回完整revised_abstract。"
                    "统计数值、样本量和检验方法只能来自facts.deterministic_result_evidence；"
                    "缺失则明确未计算。非显著检验或跨零置信区间不能据此证明等效、"
                    "中性、无法提升、没有退化、预测方向被否定或某种机制成立。"
                    "训练预算只能读取facts.plan或实际结果；计划中的收敛理由不等于已执行的预实验。"
                    "没有对应实测证据，不得声称训练预算确保收敛或观察到收敛滞后。"
                    "不要新增实验、引用、数值、因果解释或内部字段，也不要解释修订过程。"
                ),
            )
            abstract, sections = self._apply_audit_revisions(
                abstract,
                sections,
                repaired,
            )
            self._ensure_sections_exportable(sections)
        remaining = self._scientific_boundary_issues(
            abstract,
            sections,
            facts,
        )
        if remaining:
            abstract, sections = self._apply_scientific_boundary_fallback(
                abstract,
                sections,
                remaining,
            )
            self._ensure_sections_exportable(sections)
            remaining = self._scientific_boundary_issues(
                abstract,
                sections,
                facts,
            )
        if remaining:
            locations = ",".join(
                f"{item['section_id']}:{item['paragraph_index']}:"
                f"rule={item.get('rule_id') or 'unknown'}"
                for item in remaining
            )
            raise ValueError(
                "REPORT_SCIENTIFIC_BOUNDARY_FAILED:" + locations
            )
        return abstract, sections

    @classmethod
    def _apply_scientific_boundary_fallback(
        cls,
        abstract: str,
        sections: list[dict],
        issues: list[dict],
    ) -> tuple[str, list[dict]]:
        """Remove only a still-invalid sentence and append restrained wording."""
        by_id = {str(item.get("id") or ""): item for item in sections}
        inferential_fallback = (
            "当前统计证据尚未建立总体方向性差异证据。"
            "点估计可以描述本次样本中的观测方向，但非显著检验结果及跨零置信区间不能证明"
            "两种方案等效、证明某种变化不存在，或认定某种作用机制已经成立；"
            "结论只适用于本次数据、模型和冻结训练预算。"
        )
        convergence_fallback = (
            "冻结实验计划规定的训练预算本身不能证明收敛；判断收敛、收敛滞后或欠训练"
            "需要对应的实测证据。"
        )

        for issue in issues:
            section_id = str(issue.get("section_id") or "")
            paragraph_index = issue.get("paragraph_index")
            if not isinstance(paragraph_index, int) or isinstance(paragraph_index, bool):
                continue
            if section_id == "abstract":
                paragraph = abstract
            else:
                section = by_id.get(section_id)
                paragraphs = section.get("paragraphs") if section else None
                if not isinstance(paragraphs, list) or not 0 <= paragraph_index < len(paragraphs):
                    continue
                paragraph = str(paragraphs[paragraph_index])

            reason = str(issue.get("required_correction") or "")
            convergence_issue = "5 epoch" in reason.lower() or "收敛预实验" in reason
            inferential_issue = "非显著检验" in reason or "配对t检验" in reason
            patterns = []
            fallbacks = []
            if convergence_issue:
                patterns.extend(UNOBSERVED_CONVERGENCE_PATTERNS)
                fallbacks.append(convergence_fallback)
            if inferential_issue or not convergence_issue:
                patterns.extend(INCONCLUSIVE_OVERCLAIM_PATTERNS)
                patterns.append(UNPERFORMED_TEST_PATTERN)
                fallbacks.append(inferential_fallback)
            chunks = re.split(r"(?<=[。！？!?；;])", paragraph)
            retained = [
                chunk
                for chunk in chunks
                if chunk.strip() and not any(pattern.search(chunk) for pattern in patterns)
            ]
            replacement = cls._redact_reader_text(
                "".join(retained) + "".join(fallbacks)
            )
            if section_id == "abstract":
                abstract = replacement
            else:
                by_id[section_id]["paragraphs"][paragraph_index] = replacement

        return abstract, sections

    @classmethod
    def _scientific_boundary_issues(
        cls,
        abstract: str,
        sections: list[dict],
        facts: dict,
    ) -> list[dict]:
        inconclusive = cls._has_inconclusive_statistical_evidence(facts)
        test = (facts.get("deterministic_result_evidence") or {}).get("paired_t_test") or {}
        test_performed = test.get("status") == "computed" or isinstance(test.get("p_value"), (int, float))
        has_epoch_history = cls._has_epoch_history(facts)
        locations: dict[tuple[str, int], dict[str, Any]] = {}

        def add_issue(
            section_id: str,
            paragraph_index: int,
            paragraph: str,
            reason: str,
            rule_id: str,
        ) -> None:
            key = (section_id, paragraph_index)
            issue = locations.setdefault(
                key,
                {
                    "code": "scope_overreach",
                    "section_id": section_id,
                    "paragraph_index": paragraph_index,
                    "claim": paragraph,
                    "source_path": "deterministic_result_evidence",
                    "source_fact": cls._sanitize(
                        facts.get("deterministic_result_evidence") or {}
                    ),
                    "required_correction": [],
                    "rule_ids": [],
                },
            )
            issue["required_correction"].append(reason)
            issue["rule_ids"].append(rule_id)

        candidates = [("abstract", 0, abstract)]
        for section in sections:
            section_id = str(section.get("id") or "")
            for index, paragraph in enumerate(section.get("paragraphs") or []):
                candidates.append((section_id, index, str(paragraph)))

        for section_id, index, paragraph in candidates:
            if not paragraph:
                continue
            if inconclusive and section_id in {
                "abstract",
                "iteration",
                "results",
                "discussion",
                "conclusion",
            }:
                for rule_id in cls._matching_inconclusive_overclaim_rules(paragraph):
                    add_issue(
                        section_id,
                        index,
                        paragraph,
                        "非显著检验或跨零置信区间不能证明等效、中性、无提升/下降或机制成立；改为未建立方向性差异证据。",
                        rule_id,
                    )
                if test_performed and UNPERFORMED_TEST_PATTERN.search(paragraph):
                    add_issue(
                        section_id,
                        index,
                        paragraph,
                        "已完成配对t检验；应说明样本量和统计功效有限，而不是声称未进行显著性检验。",
                        "unperformed_statistical_test_claim",
                    )
            if not has_epoch_history and section_id in {"abstract", "method", "design"}:
                if any(pattern.search(paragraph) for pattern in UNOBSERVED_CONVERGENCE_PATTERNS):
                    add_issue(
                        section_id,
                        index,
                        paragraph,
                        "冻结计划预算不是收敛预实验；缺少对应实测曲线时不能声称预算确保收敛或显现收敛滞后。",
                        "unobserved_convergence_claim",
                    )

        result = []
        for issue in locations.values():
            issue["required_correction"] = "；".join(issue["required_correction"])
            issue["rule_id"] = "+".join(dict.fromkeys(issue.pop("rule_ids")))
            result.append(issue)
        return result

    @staticmethod
    def _matching_inconclusive_overclaim_rules(paragraph: str) -> list[str]:
        return [
            rule_id
            for rule_id, pattern in zip(
                INCONCLUSIVE_OVERCLAIM_RULE_IDS,
                INCONCLUSIVE_OVERCLAIM_PATTERNS,
            )
            if pattern.search(paragraph)
        ]

    @staticmethod
    def _has_epoch_history(facts: dict) -> bool:
        result = facts.get("final_result") if isinstance(facts.get("final_result"), dict) else {}
        for key in ("epoch_metrics", "training_history"):
            value = result.get(key)
            if isinstance(value, list) and len(value) >= 2:
                return True
        return False

    @staticmethod
    def _has_inconclusive_statistical_evidence(facts: dict) -> bool:
        evidence = facts.get("deterministic_result_evidence")
        if not isinstance(evidence, dict):
            return False
        tests = [evidence]
        direct = evidence.get("paired_t_test")
        if isinstance(direct, dict):
            tests.append(direct)
        for value in evidence.values():
            if isinstance(value, dict) and value.get("method") == "paired_t_test":
                tests.append(value)
        for test in tests:
            p_value = test.get("p_value")
            interval = test.get("confidence_interval_95")
            alpha = test.get("alpha", 0.05)
            if isinstance(p_value, (int, float)) and isinstance(alpha, (int, float)) and p_value >= alpha:
                return True
            if (
                isinstance(interval, list)
                and len(interval) == 2
                and all(isinstance(item, (int, float)) for item in interval)
                and float(interval[0]) <= 0 <= float(interval[1])
            ):
                return True
        return False

    @staticmethod
    def _outline_instructions(extra: str) -> str:
        instructions = (
            "先规划一份中文本科毕业论文水平的研究报告，不写正文。报告必须围绕一个中心问题逐章推进，"
            "避免执行摘要、Source、Target等字段式章节，避免在多个章节重复同一结论。"
            "section_plans必须覆盖给定的八个章节ID，并说明每章承担的论证任务、所用证据和与前后章节的连接。"
            "reference_selection只选择与研究对象、方法或评价设计直接相关的已验证文献，不得凑数。"
            "reference_selection填写facts.verified_references中的paper_id。"
            "图表由固定报告模板根据持久化数据自动选择，写作模型不得补造数值、曲线，"
            "也不得把工程修复解释为科学结果。"
        )
        return instructions + (f"\n补充要求：\n{extra}" if extra else "")

    @staticmethod
    def _section_instructions(spec: dict, extra: str) -> str:
        instructions = (
            f"只撰写“{spec['title']}”。本章任务：{spec['purpose']}"
            f"正文应有{spec['paragraphs']}个有实质内容的中文段落，总长度不少于{spec['min_chars']}字。"
            "每段围绕一个中心意思展开，包含必要的事实、方法、数值或解释，并与相邻段自然衔接。"
            "不要使用条目堆砌结论，不要照抄artifact字段，不要输出路径、ID、哈希、英文状态值或审计内部字段。"
            "引用除外：正文用[PAPER-...]引用facts.verified_references中已有paper_id，citations列出同样编号。"
            "只能引用available_text或abstract确实支持的论点；不得用相关标题代替证据。"
            "统计值、单位、比较方向与检验名称直接取facts.deterministic_result_evidence，缺失则写未计算。"
            "专业名称可保留英文，但整段论述必须使用中文。不能补造输入中不存在的事实。"
            "若双侧显著性检验不显著或置信区间包含零，只能写‘未建立显著差异/提升证据’；"
            "可以如实报告点估计方向，但不得写成统计等效、相同、无本质区别、证实无法提升、"
            "证明机制无效或直接否定某一方向。只有facts明确给出预设等效界值和等效性检验时才可声称等效。"
            "实验计划中的training_budget_rationale或expected_artifacts只是设计意图，不是已经执行的证据；"
            "若final_result没有真实epoch_metrics/training_history，不得声称执行了收敛预分析、"
            "记录了完整逐epoch曲线或由曲线确认收敛滞后，训练轮数只能表述为冻结计划规定的预算。"
        )
        if spec["id"] == "conclusion":
            instructions += (
                "结论第一段必须直接复述final_revision中的最终判定及其事实依据。"
                "若假设未获支持、结果不确定或次要终点未满足，必须使用明确的否定或未证实表述；"
                "不得改写为优化方向、潜力、突破、有效性或其他弱化失败结论的措辞。"
                "不得在读者可见正文中使用partial、supported、unsupported、inconclusive等内部状态值，"
                "也不得用‘部分支持’替代具体结论；必须直接说明哪些指标变化、哪些标准未达到。"
            )
        return instructions + (f"\n补充要求：\n{extra}" if extra else "")

    @classmethod
    def _normalize_section(cls, value: Any, spec: dict) -> dict:
        value = value if isinstance(value, dict) else {}
        paragraphs = [
            cls._redact_reader_text(item)
            for item in cls._paragraphs(value.get("paragraphs") or value.get("content"))
        ]
        subsections = []
        for item in value.get("subsections") or []:
            if not isinstance(item, dict):
                continue
            child_paragraphs = [
                cls._redact_reader_text(paragraph)
                for paragraph in cls._paragraphs(item.get("paragraphs") or item.get("content"))
            ]
            if child_paragraphs:
                subsections.append(
                    {
                        "title": cls._clean_text(item.get("title") or ""),
                        "paragraphs": child_paragraphs,
                    }
                )
        return {
            "id": spec["id"],
            "title": spec["title"],
            "paragraphs": paragraphs,
            "subsections": subsections,
            "citations": [
                cls._clean_text(item)
                for item in (value.get("citations") or [])
                if cls._clean_text(item)
            ],
        }

    @classmethod
    def _section_quality_issues(cls, section: dict, spec: dict) -> list[str]:
        paragraphs = list(section.get("paragraphs") or [])
        for subsection in section.get("subsections") or []:
            paragraphs.extend(subsection.get("paragraphs") or [])
        issues = []
        total = sum(len(item) for item in paragraphs)
        minimum_paragraphs = int(str(spec["paragraphs"]).split("-")[0])
        if len(paragraphs) < minimum_paragraphs:
            issues.append(f"有效段落不足：至少需要{minimum_paragraphs}段")
        if total < int(spec["min_chars"]):
            issues.append(f"论述过短：至少需要{spec['min_chars']}字")
        for index, paragraph in enumerate(paragraphs, 1):
            compact = paragraph.replace(" ", "")
            if len(compact) < 90:
                issues.append(f"第{index}段过短，尚未形成完整论述")
            if any(token.lower() in paragraph.lower() for token in FORBIDDEN_READER_FIELDS):
                issues.append(f"第{index}段包含内部运行字段或路径")
            if any(token in compact for token in AI_FILLER):
                issues.append(f"第{index}段包含模板化AI表达")
            chinese = len(re.findall(r"[\u4e00-\u9fff]", paragraph))
            latin = len(re.findall(r"[A-Za-z]", paragraph))
            if len(paragraph) > 120 and latin > chinese:
                issues.append(f"第{index}段英文比例过高，应改为中文论述")
        normalized = [re.sub(r"\s+", "", item) for item in paragraphs]
        if len(normalized) != len(set(normalized)):
            issues.append("存在重复段落")
        return list(dict.fromkeys(issues))

    @classmethod
    def _section_hard_quality_issues(cls, section: dict) -> list[str]:
        paragraphs = list(section.get("paragraphs") or [])
        for subsection in section.get("subsections") or []:
            paragraphs.extend(subsection.get("paragraphs") or [])
        if not paragraphs or sum(len(item.strip()) for item in paragraphs) < 120:
            return ["章节缺少实质内容"]
        issues = []
        for index, paragraph in enumerate(paragraphs, 1):
            if any(token.lower() in paragraph.lower() for token in FORBIDDEN_READER_FIELDS):
                issues.append(f"第{index}段包含内部运行字段或路径")
        return list(dict.fromkeys(issues))

    @classmethod
    def _apply_audit_revisions(
        cls,
        abstract: str,
        sections: list[dict],
        audit: dict,
    ) -> tuple[str, list[dict]]:
        revised_abstract = cls._redact_reader_text(
            cls._clean_text(audit.get("revised_abstract"))
        )
        if revised_abstract:
            abstract = revised_abstract
        by_id = {item["id"]: item for item in sections}
        for revision in audit.get("section_revisions") or []:
            if not isinstance(revision, dict):
                continue
            section = by_id.get(str(revision.get("section_id") or ""))
            if section is not None and isinstance(revision.get("citations"), list):
                section["citations"] = [str(item) for item in revision["citations"]]
            paragraph_index = revision.get("paragraph_index")
            replacement_paragraph = cls._redact_reader_text(
                cls._clean_text(revision.get("replacement_paragraph"))
            )
            paragraph_slots = []
            if section is not None:
                for container in [section, *(section.get("subsections") or [])]:
                    paragraph_slots.extend(
                        (container["paragraphs"], index)
                        for index in range(len(container.get("paragraphs") or []))
                    )
            if (
                section is not None
                and isinstance(paragraph_index, int)
                and not isinstance(paragraph_index, bool)
                and 0 <= paragraph_index < len(paragraph_slots)
                and replacement_paragraph
            ):
                paragraphs, index = paragraph_slots[paragraph_index]
                paragraphs[index] = replacement_paragraph
                continue
            replacements = [
                cls._redact_reader_text(item)
                for item in cls._paragraphs(
                    revision.get("replacement_paragraphs") or revision.get("paragraphs")
                )
            ]
            if section is not None and replacements:
                section["paragraphs"] = replacements
        return abstract, sections

    @classmethod
    def _validated_hard_failures(
        cls,
        value: Any,
        *,
        facts: dict | None = None,
        sections: list[dict] | None = None,
        abstract: str | None = None,
    ) -> list[dict]:
        failures = []
        for item in value if isinstance(value, list) else []:
            if not isinstance(item, dict):
                continue
            code = cls._clean_text(item.get("code")).lower()
            section_id = cls._clean_text(item.get("section_id"))
            claim = cls._clean_text(item.get("claim"))
            source_path = cls._clean_text(item.get("source_path"))
            source_fact = cls._clean_text(item.get("source_fact"))
            correction = cls._clean_text(item.get("required_correction"))
            paragraph_index = item.get("paragraph_index")
            if (
                code not in HARD_AUDIT_CODES
                or not section_id
                or not claim
                or not source_path
                or not source_fact
                or not correction
                or not isinstance(paragraph_index, int)
                or isinstance(paragraph_index, bool)
                or paragraph_index < 0
            ):
                continue
            # A few provider responses put a correctly verified statement in
            # ``hard_failures`` while their own correction explicitly says
            # that no error exists.  Such a self-negating item cannot describe
            # an unresolved hard failure and must not block report export.
            normalized_correction = re.sub(r"\s+", "", correction).casefold()
            if any(
                phrase in normalized_correction
                for phrase in (
                    "原文表述正确",
                    "数值匹配",
                    "逻辑正确",
                    "支持关系成立",
                    "无错误",
                    "无需修正",
                    "noerror",
                    "nomismatch",
                )
            ):
                continue
            resolved_fact = (
                resolve_fact_path(facts, source_path) if facts is not None else None
            )
            if facts is not None and resolved_fact is None:
                continue
            if (
                code == "numeric_mismatch"
                and facts is not None
                and not cls._numeric_mismatch_is_evidenced(
                    claim,
                    source_path,
                    resolved_fact,
                    claimed_value=item.get("claimed_value"),
                )
            ):
                continue
            if (
                code == "unit_mismatch"
                and facts is not None
                and not cls._unit_mismatch_is_evidenced(
                    claim,
                    source_path,
                    resolved_fact,
                )
            ):
                continue
            if (
                code == "internal_leak"
                and not cls._internal_leak_is_evidenced(
                    claim,
                    resolved_fact,
                )
            ):
                continue
            if (
                source_path.endswith(".verdict")
                and source_fact.lower() in {"partial", "supported", "unsupported", "inconclusive"}
            ):
                # A bare workflow enum is not claim-level evidence. The auditor
                # must cite the supported/unsupported claim or failed criterion
                # that the report actually contradicts.
                continue
            locations = list(sections or [])
            if abstract is not None:
                locations.append({"id": "abstract", "paragraphs": [abstract]})
            if (sections is not None or abstract is not None) and not cls._failure_claim_exists(
                locations, section_id, paragraph_index, claim
            ):
                continue
            failures.append(
                {
                    "code": code,
                    "section_id": section_id,
                    "paragraph_index": paragraph_index,
                    "claim": claim,
                    "source_path": source_path,
                    "source_fact": source_fact,
                    "required_correction": correction,
                    **({"claimed_value": item["claimed_value"]} if "claimed_value" in item else {}),
                }
            )
        return failures

    @classmethod
    def _internal_leak_is_evidenced(
        cls,
        claim: str,
        resolved_fact: Any,
    ) -> bool:
        """Require the reader-facing claim itself to contain internal data.

        An auditor may cite a private fact path as evidence, but that path is
        not part of the report.  Generic phrases such as "本地路径" therefore
        are not leaks unless the paragraph exposes an actual path, identifier,
        hash, command, provider field, or other forbidden token.
        """
        lowered = claim.lower()
        if any(token.lower() in lowered for token in FORBIDDEN_READER_FIELDS):
            return True
        if re.search(r"(?i)(?:\b(?:run|art)_[0-9a-f]{8,}\b|sha-?256|[a-z]:\\|/home/|/users/)", claim):
            return True
        if isinstance(resolved_fact, (str, int, float)):
            source_text = cls._clean_text(resolved_fact)
            if len(source_text) >= 6 and source_text.lower() in lowered:
                return True
        return False

    @classmethod
    def _numeric_mismatch_is_evidenced(
        cls,
        claim: str,
        source_path: str,
        resolved_fact: Any,
        *,
        claimed_value: Any = None,
    ) -> bool:
        """Reject audit-model false positives before they can block export.

        The audit model identifies candidate discrepancies, but the backend owns
        the final numeric decision.  A value displayed at lower precision is
        equivalent when the authoritative fact falls inside that display bin.
        Claims about a derived difference must also cite a derived fact rather
        than one operand, otherwise the alleged mismatch is not reproducible.
        """
        expected = cls._decimal_fact(resolved_fact)
        if expected is None:
            # Structured statistical facts are often cited as a whole.  A
            # confidence interval is still mechanically verifiable when both
            # displayed endpoints round to the saved endpoints.
            if cls._claim_contains_rounded_interval(claim, resolved_fact):
                return False
            # Preserve the existing behavior for other non-scalar legacy
            # evidence; scalar fact paths are handled deterministically below.
            return True

        normalize_sign = lambda text: re.sub(r"[−–—]|负(?=[\d.])", "-", text)
        numeric_claim = normalize_sign(claim)
        if claimed_value is not None:
            token = normalize_sign(str(claimed_value).strip())
            matches = [match.group(0).strip() for match in NUMERIC_CLAIM_PATTERN.finditer(numeric_claim)]
            compact = lambda text: re.sub(r"\s+", "", text)
            if not NUMERIC_CLAIM_PATTERN.fullmatch(token) or compact(token) not in {compact(text) for text in matches}:
                return False  # An invented display value is not evidence of a mismatch.
            numeric_claim = token
        # Only a single identified value can exonerate a numeric claim. A correct
        # baseline elsewhere in the paragraph must not hide an incorrect variant.
        if len(list(NUMERIC_CLAIM_PATTERN.finditer(numeric_claim))) == 1:
            if cls._claim_contains_rounded_fact(numeric_claim, expected):
                return False
            # Chinese report prose commonly presents a signed delta as an
            # unsigned magnitude after an explicitly neutral phrase such as
            # ``差值为``.  The compared levels carry the direction, while the
            # displayed number answers "how large is the gap?".  Treat that as
            # the same fact, but keep positively directional wording (for
            # example ``提升``) blocking when the authoritative delta is
            # negative.
            if (
                expected < 0
                and cls._claim_contains_rounded_fact(numeric_claim, abs(expected))
                and cls._negative_fact_is_expressed_as_magnitude(numeric_claim)
            ):
                return False

        normalized_claim = claim.casefold()
        normalized_path = source_path.casefold()
        if any(term in normalized_claim for term in DERIVED_NUMERIC_CLAIM_TERMS):
            if not any(term in normalized_path for term in DERIVED_NUMERIC_PATH_TERMS):
                return False

        # A numeric hard failure must point to an actual numeric statement in
        # the cited paragraph.  Otherwise there is nothing deterministic to
        # compare and the issue must not block export.
        return bool(NUMERIC_CLAIM_PATTERN.search(claim))

    @staticmethod
    def _negative_fact_is_expressed_as_magnitude(claim: str) -> bool:
        normalized = re.sub(r"\s+", "", claim).casefold()
        if any(term in normalized for term in ("提升", "增加", "高于", "上升", "改善", "增益")):
            return False
        return any(
            term in normalized
            for term in ("差值", "差距", "相差", "幅度", "下降", "降低", "低于", "减少")
        )

    @classmethod
    def _claim_contains_rounded_interval(cls, claim: str, resolved_fact: Any) -> bool:
        interval = resolved_fact
        if isinstance(resolved_fact, dict):
            interval = resolved_fact.get("confidence_interval_95")
        if not isinstance(interval, (list, tuple)) or len(interval) != 2:
            return False
        expected = [cls._decimal_fact(value) for value in interval]
        if any(value is None for value in expected):
            return False

        bracketed = re.search(r"[\[\uff3b\u3010]\s*(.*?)\s*[\]\uff3d\u3011]", claim)
        if bracketed is None:
            return False
        displayed = [match.group(0).strip() for match in NUMERIC_CLAIM_PATTERN.finditer(bracketed.group(1))]
        if len(displayed) != 2:
            return False
        return all(
            cls._claim_contains_rounded_fact(token, endpoint)
            for token, endpoint in zip(displayed, expected)
        )

    @staticmethod
    def _decimal_fact(value: Any) -> Decimal | None:
        if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
            return None
        try:
            parsed = Decimal(str(value))
        except (InvalidOperation, ValueError):
            return None
        return parsed if parsed.is_finite() else None

    @staticmethod
    def _claim_contains_rounded_fact(claim: str, expected: Decimal) -> bool:
        for match in NUMERIC_CLAIM_PATTERN.finditer(claim):
            token = match.group("number")
            try:
                displayed = Decimal(token)
            except InvalidOperation:
                continue
            if (
                not token.startswith(("+", "-"))
                and match.start() > 0
                and claim[match.start() - 1] in {"负", "−", "–", "—"}
            ):
                displayed = -displayed

            mantissa, _, exponent_text = token.lower().partition("e")
            decimal_places = len(mantissa.partition(".")[2])
            exponent = int(exponent_text or 0)
            quantum = Decimal(10) ** (exponent - decimal_places)
            unit = re.sub(r"\s+", " ", (match.group("unit") or "").strip().casefold())
            if unit:
                displayed /= Decimal(100)
                quantum /= Decimal(100)

            tolerance = abs(quantum) / Decimal(2)
            epsilon = max(abs(expected), Decimal(1)) * Decimal("1e-15")
            if abs(displayed - expected) <= tolerance + epsilon:
                return True
        return False

    @classmethod
    def _unit_mismatch_is_evidenced(
        cls,
        claim: str,
        source_path: str,
        resolved_fact: Any,
    ) -> bool:
        """Keep semantic unit errors hard while accepting valid ratio displays."""
        expected = cls._decimal_fact(resolved_fact)
        if expected is None or not cls._claim_contains_rounded_fact(claim, expected):
            return True

        units = {
            re.sub(r"\s+", " ", (match.group("unit") or "").strip().casefold())
            for match in NUMERIC_CLAIM_PATTERN.finditer(claim)
            if (match.group("unit") or "").strip()
        }
        point_units = {
            unit for unit in units
            if "百分点" in unit or unit == "pp" or unit.startswith("percentage")
        }
        normalized_claim = claim.casefold()
        normalized_path = source_path.casefold()
        path_is_relative = any(
            term in normalized_path
            for term in RELATIVE_CHANGE_PATH_TERMS
        )
        claim_is_relative = any(
            term in normalized_claim
            for term in RELATIVE_CHANGE_CLAIM_TERMS
        )
        if not point_units:
            # Percent is a conventional display unit for a proportion fact.
            path_is_absolute = any(
                term in normalized_path
                for term in ABSOLUTE_POINT_PATH_TERMS
            )
            return claim_is_relative and path_is_absolute and not path_is_relative

        if path_is_relative:
            return True
        return not any(term in normalized_path for term in ABSOLUTE_POINT_PATH_TERMS)

    @classmethod
    def _failure_claim_exists(
        cls,
        sections: list[dict],
        section_id: str,
        paragraph_index: int,
        claim: str,
    ) -> bool:
        section = next(
            (item for item in sections if str(item.get("id") or "") == section_id),
            None,
        )
        if section is None:
            return False
        paragraphs = list(section.get("paragraphs") or [])
        for subsection in section.get("subsections") or []:
            paragraphs.extend(subsection.get("paragraphs") or [])
        if not paragraphs:
            return False
        index = paragraph_index
        if index < 0 or index >= len(paragraphs):
            return False
        normalized_claim = re.sub(r"[\s，。；：、“”‘’（）()]+", "", claim).lower()
        normalized_paragraph = re.sub(
            r"[\s，。；：、“”‘’（）()]+", "", paragraphs[index]
        ).lower()
        return bool(normalized_claim and normalized_claim in normalized_paragraph)

    @classmethod
    def _ensure_sections_exportable(cls, sections: list[dict]) -> None:
        issues = []
        for section in sections:
            for issue in cls._section_hard_quality_issues(section):
                issues.append(f"{section.get('id') or 'section'}:{issue}")
        if issues:
            raise ValueError("REPORT_SECTION_QUALITY_FAILED:" + "；".join(issues))

    @classmethod
    def _fact_sheet(
        cls,
        problem: dict,
        hypothesis: dict,
        plan: dict,
        result: dict,
        revision: dict,
        iteration_summary: dict,
        references: list[dict],
        research_state: dict,
        deterministic_evidence: dict | None = None,
    ) -> dict:
        return {
            "research_question": cls._sanitize(
                problem.get("problem_statement") or problem.get("problem_input") or ""
            ),
            "problem_context": cls._sanitize(problem),
            "hypothesis": cls._sanitize(hypothesis),
            "plan": cls._sanitize(plan),
            "iterations": cls._sanitize(iteration_summary),
            "final_result": cls._sanitize(result),
            **({"optimization": cls._sanitize(research_state["optimization"])}
               if research_state.get("optimization") else {}),
            "deterministic_result_evidence": cls._sanitize(
                deterministic_evidence or {}
            ),
            "final_revision": cls._sanitize(revision),
            "verified_references": [
                {
                    "paper_id": item.get("paper_id") or stable_paper_id(item),
                    "title": item.get("title", ""),
                    "authors": item.get("authors") or [],
                    "year": item.get("year"),
                    "identifiers": item.get("identifiers") or {},
                    "abstract": literature_summary_text(item),
                    "available_text": item.get("available_text") or "",
                    "url": item.get("url") or item.get("source_url") or "",
                    "verified": item.get("verified") is True,
                    "relevance": item.get("relevance"),
                    "reliability": item.get("reliability"),
                }
                for item in references
            ],
            "authoritative_research_state": cls._report_research_state(
                research_state
            ),
        }

    @classmethod
    def _active_hypothesis(cls, by_type: dict, research_state: dict) -> dict:
        selection = by_type.get("hypothesis_selection", {})
        selected = selection.get("selected") if isinstance(selection, dict) else None
        hypothesis = (
            dict(selected[0])
            if isinstance(selected, list) and selected and isinstance(selected[0], dict)
            else dict(by_type.get("hypothesis", {}) or {})
        )
        canonical = (
            research_state.get("canonical")
            if isinstance(research_state.get("canonical"), dict)
            else {}
        )
        active_claim = cls._clean_text(canonical.get("active_hypothesis"))
        if active_claim:
            hypothesis["original_claim"] = hypothesis.get("claim") or ""
            hypothesis["claim"] = active_claim
        hypothesis["status"] = next(
            (
                item.get("status")
                for item in research_state.get("claims") or []
                if item.get("claim_id") == "active_hypothesis"
            ),
            "unverified",
        )
        return hypothesis

    @classmethod
    def _report_research_state(cls, state: dict) -> dict:
        claims = []
        for item in state.get("claims") or []:
            if not isinstance(item, dict):
                continue
            claims.append(
                {
                    key: cls._sanitize(value)
                    for key, value in item.items()
                    if key != "source_artifact_id"
                }
            )
        conflicts = []
        for item in state.get("conflicts") or []:
            if not isinstance(item, dict):
                continue
            conflicts.append(
                {
                    key: cls._sanitize(value)
                    for key, value in item.items()
                    if key != "source_artifact_id"
                }
            )
        artifact_ledger = []
        for item in state.get("artifact_states") or []:
            if not isinstance(item, dict):
                continue
            artifact_ledger.append(
                {
                    "artifact_type": cls._sanitize(item.get("artifact_type") or ""),
                    "version": item.get("version"),
                    "source_step": cls._sanitize(item.get("source_step") or ""),
                    "lifecycle_status": cls._sanitize(
                        item.get("lifecycle_status") or item.get("status") or ""
                    ),
                    "validity_status": cls._sanitize(
                        item.get("validity_status") or ""
                    ),
                    "validity_reason": cls._sanitize(
                        item.get("validity_reason") or ""
                    ),
                }
            )
        return {
            "schema_version": state.get("schema_version"),
            "ledger_policy": cls._sanitize(state.get("ledger_policy") or {}),
            "authority_order": cls._sanitize(state.get("authority_order") or []),
            "artifact_ledger": artifact_ledger,
            "claims": claims,
            "conflicts": conflicts,
            "canonical": cls._sanitize(state.get("canonical") or {}),
        }

    @classmethod
    def _sanitize(cls, value: Any) -> Any:
        hidden = {
            "files",
            "sample_sha256",
            "sha256",
            "content_fingerprint",
            "artifact_id",
            "parent_artifact_id",
            "command",
            "workdir",
            "log_path",
            "result_path",
            "bundle",
            "attempts",
            "provider_mode",
            "fallback_used",
            "fallback_reason",
            "root",
            "contract_id",
            "inspection",
            "file_types",
            "schemas",
            "card",
        }
        if isinstance(value, dict):
            return {
                str(key): cls._sanitize(item)
                for key, item in value.items()
                if str(key) not in hidden and item not in (None, "", [], {})
            }
        if isinstance(value, list):
            return [cls._sanitize(item) for item in value]
        return value

    @staticmethod
    def _latest_by_type(artifacts: list) -> dict[str, dict]:
        values: dict[str, dict] = {}
        for artifact in artifacts:
            if isinstance(getattr(artifact, "content", None), dict):
                values[artifact.type] = artifact.content
        return values

    @staticmethod
    def _verified_references(artifacts: list) -> list[dict]:
        references = []
        seen = set()
        for artifact in artifacts:
            if artifact.type != "evidence" or not isinstance(artifact.content, dict):
                continue
            for item in artifact.content.get("references") or []:
                if not isinstance(item, dict) or item.get("verified") is not True:
                    continue
                identifiers = item.get("identifiers") if isinstance(item.get("identifiers"), dict) else {}
                identity = (
                    str(identifiers.get("doi") or "").lower(),
                    str(identifiers.get("arxiv") or "").lower(),
                    str(item.get("title") or "").strip().lower(),
                )
                if identity in seen:
                    continue
                seen.add(identity)
                references.append({**item, "paper_id": item.get("paper_id") or stable_paper_id(item)})
        return references

    @staticmethod
    def _select_references(
        references: list[dict],
        selections,
        citations,
        *,
        context: str = "",
    ) -> list[dict]:
        requested = [
            str(item).strip().lower()
            for item in [*(selections or []), *(citations or [])]
            if str(item).strip()
        ]
        matched = []
        for reference in references:
            identifiers = reference.get("identifiers") if isinstance(reference.get("identifiers"), dict) else {}
            fields = [
                str(reference.get("paper_id") or stable_paper_id(reference)).lower(),
                str(reference.get("title") or "").lower(),
                str(reference.get("url") or "").lower(),
                str(identifiers.get("doi") or "").lower(),
                str(identifiers.get("arxiv") or "").lower(),
            ]
            if requested and any(
                request == field
                for request in requested
                for field in fields
                if field
            ):
                matched.append(reference)
        return matched

    @staticmethod
    def _all_exportable_references(references: list[dict]) -> list[dict]:
        """Return every verified, de-duplicated source collected for this run.

        ``_verified_references`` is the single evidence snapshot that already
        filters unverified cards and assigns stable paper IDs.  Reusing its
        order keeps Word, HTML, and paper-package exports reproducible while
        making the final bibliography complete.
        """
        return list(references)

    @staticmethod
    def _iteration_summary(artifacts: list, result: dict, revision: dict) -> dict:
        revisions_by_result = {
            artifact.parent_artifact_id: artifact.content
            for artifact in artifacts
            if artifact.type == "revision" and artifact.parent_artifact_id
        }
        rounds = []
        for artifact in artifacts:
            if artifact.type != "experiment_result":
                continue
            content = artifact.content
            feedback = revisions_by_result.get(artifact.id, {})
            rounds.append(
                {
                    "round": int(feedback.get("iteration") or len(rounds) + 1),
                    "experiment_id": content.get("experiment_id", ""),
                    "status": content.get("status") or content.get("verdict") or "",
                    "metrics": content.get("metrics") or {},
                    "comparisons": WriterAgent._result_comparisons(content),
                    "diagnosis": feedback.get("feedback") or "",
                    "feedback_verdict": feedback.get("verdict") or "",
                    "revision": feedback.get("required_revision")
                    or feedback.get("next_action")
                    or "",
                    "requires_follow_up": feedback.get("requires_follow_up") is True,
                }
            )
        return {
            "round_count": len(rounds),
            "final_status": result.get("status") or result.get("verdict") or "",
            "final_feedback_verdict": revision.get("verdict") or "",
            "requires_follow_up": revision.get("requires_follow_up") is True,
            "rounds": rounds,
        }

    @staticmethod
    def _result_comparisons(result: dict) -> list[dict]:
        analysis = result.get("analysis") if isinstance(result.get("analysis"), dict) else {}
        comparisons = analysis.get("comparisons") or result.get("comparisons") or []
        normalized = [item for item in comparisons if isinstance(item, dict)]
        metrics = result.get("metrics") if isinstance(result.get("metrics"), dict) else {}
        for key, baseline in metrics.items():
            if not key.startswith("baseline_") or not isinstance(baseline, (int, float)):
                continue
            metric = key.removeprefix("baseline_")
            improved = metrics.get(f"improved_{metric}")
            if isinstance(improved, (int, float)):
                normalized.append(
                    {
                        "metric": metric,
                        "baseline_value": baseline,
                        "improved_value": improved,
                        "delta": improved - baseline,
                    }
                )
        return normalized

    @staticmethod
    def _limitations(result: dict, revision: dict) -> list[str]:
        analysis = result.get("analysis") if isinstance(result.get("analysis"), dict) else {}
        audit = result.get("audit") if isinstance(result.get("audit"), dict) else {}
        values = []
        for source in (
            analysis.get("limitations"),
            audit.get("issues"),
            revision.get("unsupported_claims"),
            revision.get("overclaim_risks"),
        ):
            for value in source or []:
                text = str(value).strip()
                if text and text not in values:
                    values.append(text)
        return values

    @classmethod
    def _paragraphs(cls, value: Any) -> list[str]:
        if isinstance(value, str):
            candidates = re.split(r"\n\s*\n|\n", value)
        elif isinstance(value, list):
            candidates = [str(item) for item in value if not isinstance(item, dict)]
        else:
            candidates = []
        paragraphs = []
        for item in candidates:
            text = cls._clean_text(item)
            text = re.sub(r"^(?:[-*•]|\d+[.、])\s*", "", text)
            if text and text not in paragraphs:
                paragraphs.append(text)
        return paragraphs

    @staticmethod
    def _clean_text(value: Any) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()

    @classmethod
    def _redact_reader_text(cls, value: Any) -> str:
        """Remove concrete runtime identifiers while preserving readable prose."""
        text = cls._clean_text(value)
        text = re.sub(
            r"(?i)\b[a-z]:\\[^\s，。；、）)]+",
            "本地数据目录",
            text,
        )
        text = re.sub(
            r"(?i)(?:/home/|/users/)[^\s，。；、）)]+",
            "本地数据目录",
            text,
        )
        text = re.sub(r"(?i)\b(?:run|art)_[0-9a-f]{8,}\b", "内部记录", text)
        text = re.sub(r"(?i)\bsha-?256\s*:?\s*[0-9a-f]{16,}\b", "已验证指纹", text)
        replacements = {
            "artifact_id": "内部记录",
            "contract_id": "数据契约",
            "provider_mode": "运行方式",
            "fallback_used": "备用路径状态",
            "content_fingerprint": "数据指纹",
        }
        for source, replacement in replacements.items():
            text = re.sub(re.escape(source), replacement, text, flags=re.IGNORECASE)
        return cls._clean_text(text)

    @staticmethod
    def _section_tail(section: dict) -> str:
        paragraphs = section.get("paragraphs") or []
        if section.get("subsections"):
            paragraphs = (section["subsections"][-1].get("paragraphs") or []) or paragraphs
        return paragraphs[-1][-500:] if paragraphs else ""

    @staticmethod
    def _section_text(section: dict) -> str:
        parts = list(section.get("paragraphs") or [])
        for subsection in section.get("subsections") or []:
            parts.extend(subsection.get("paragraphs") or [])
        return "\n".join(parts)

    @staticmethod
    def _fallback_abstract(facts: dict) -> str:
        question = WriterAgent._clean_text(facts.get("research_question")) or "既定研究问题"
        result = facts.get("final_result") if isinstance(facts.get("final_result"), dict) else {}
        metrics = result.get("metrics") if isinstance(result.get("metrics"), dict) else {}
        metric_text = "、".join(f"{key}为{value}" for key, value in list(metrics.items())[:4])
        return (
            f"本研究围绕{question}展开，通过受控对照和逐轮方法修正检验既定假设。"
            f"实验使用统一的数据划分、训练设置和评价程序比较基线方案与改进方案，"
            f"并以多个随机种子降低单次运行波动的影响。最终实验记录的主要结果为{metric_text or '当前产物所载指标'}。"
            "报告据此给出与证据强度相匹配的判断，同时将结论限定在本次数据集、模型结构、参数设置和评价方法之内。"
        )
