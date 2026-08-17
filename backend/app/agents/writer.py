from __future__ import annotations

import re
from typing import Any

from backend.app.providers.llm import LLMProvider
from backend.app.report_visualization import build_report_spec
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


class ReportFactAuditError(ValueError):
    def __init__(
        self,
        failures: list[dict],
        *,
        title: str,
        abstract: str,
        sections: list[dict],
        audit: dict,
    ) -> None:
        codes = list(dict.fromkeys(item["code"] for item in failures))
        super().__init__("REPORT_FACT_AUDIT_FAILED:" + ",".join(codes))
        self.failures = failures
        self.draft = {
            "Report Title": title,
            "Paper Title": title,
            "Paper Abstract": abstract,
            "Narrative Sections": sections,
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
                "selected_figure_ids": ["string"],
                "figure_rationale": "string",
            },
            instructions=self._outline_instructions(instructions),
        )
        selected_figure_ids = [
            str(value) for value in (outline.get("selected_figure_ids") or [])
            if isinstance(value, str)
        ]
        title = self._clean_text(
            outline.get("title")
            or plan.get("objective")
            or hypothesis.get("claim")
            or "实验迭代与科学假设验证报告"
        )
        selected_reference_tokens = list(outline.get("reference_selection") or [])
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
            selected_reference_tokens.extend(section.get("citations") or [])
            previous_tail = self._section_tail(section)

        abstract_response = self.llm_provider.generate_json(
            "writer.report_abstract",
            {
                "title": title,
                "facts": {
                    "research_question": facts["research_question"],
                    "hypothesis": facts["hypothesis"],
                    "final_result": facts["final_result"],
                    "final_revision": facts["final_revision"],
                },
                "sections": sections,
            },
            {"abstract": "string", "keywords": ["string"]},
            instructions=(
                "在所有正文完成后撰写中文摘要。用一个完整段落依次交代背景、问题、方法、"
                "最终关键数值、结论和适用边界，350至600字。不得写目录式摘要、空泛目的句或新增事实。"
            ),
        )
        abstract = self._clean_text(abstract_response.get("abstract"))
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
                "source_path、source_fact和required_correction，其中source_path必须指向facts中的"
                "现有字段。code只能是numeric_mismatch、unit_mismatch、"
                "fabricated_fact、fabricated_citation、verdict_inversion、scope_overreach、"
                "unverified_reference、missing_core_section、internal_leak或"
                "cross_section_contradiction；grounding、quality、style等笼统标签无效，"
                "证据字段不完整的问题应放入revision_required。"
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
            verification = self.llm_provider.generate_json(
                "writer.verify_report_audit",
                {
                    "title": title,
                    "abstract": abstract,
                    "sections": sections,
                    "facts": facts,
                    "previous_hard_failures": hard_failures,
                },
                {"hard_failures": ["object"]},
                instructions=(
                    "复核修订稿中先前指出的事实问题是否仍然存在。只有能够同时提供合法code、"
                    "section_id、paragraph_index、claim、source_path、source_fact和"
                    "required_correction的"
                    "未解决问题才可返回为hard_failures；grounding、quality、style等笼统标签无效。"
                ),
            )
            hard_failures = self._validated_hard_failures(
                verification.get("hard_failures"),
                facts=facts,
                sections=sections,
            )
            if hard_failures:
                raise ReportFactAuditError(
                    hard_failures,
                    title=title,
                    abstract=abstract,
                    sections=sections,
                    audit=verification,
                )
        references = self._select_references(
            verified_references,
            selected_reference_tokens,
            [item for section in sections for item in section.get("citations") or []],
            context=" ".join(
                [
                    title,
                    self._clean_text(facts.get("research_question")),
                    self._clean_text(
                        (facts.get("hypothesis") or {}).get("claim")
                        if isinstance(facts.get("hypothesis"), dict)
                        else facts.get("hypothesis")
                    ),
                    self._clean_text(
                        (facts.get("plan") or {}).get("objective")
                        if isinstance(facts.get("plan"), dict)
                        else ""
                    ),
                ]
            ),
        )

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
            "References": references,
            "Iteration Summary": iteration_summary,
            "Limitations": self._limitations(result, revision),
            "Research Conclusion": self._section_text(sections[-1]),
            "Report Spec": build_report_spec(
                artifacts,
                selected_figure_ids=selected_figure_ids,
                decision_rationale=self._clean_text(outline.get("figure_rationale")),
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

    @staticmethod
    def _outline_instructions(extra: str) -> str:
        instructions = (
            "先规划一份中文本科毕业论文水平的研究报告，不写正文。报告必须围绕一个中心问题逐章推进，"
            "避免执行摘要、Source、Target等字段式章节，避免在多个章节重复同一结论。"
            "section_plans必须覆盖给定的八个章节ID，并说明每章承担的论证任务、所用证据和与前后章节的连接。"
            "reference_selection只选择与研究对象、方法或评价设计直接相关的已验证文献，不得凑数。"
            "同时选择能够服务论证的图表：selected_figure_ids只能从research_workflow、"
            "control_variables、seed_comparison、main_comparison、training_curve、workflow_timeline中选择；"
            "训练曲线只在事实中存在真实epoch/step指标时选择。figure_rationale说明选择依据，"
            "不得要求补造数值、曲线或把工程修复解释为科学结果。"
        )
        return instructions + (f"\n补充要求：\n{extra}" if extra else "")

    @staticmethod
    def _section_instructions(spec: dict, extra: str) -> str:
        instructions = (
            f"只撰写“{spec['title']}”。本章任务：{spec['purpose']}"
            f"正文应有{spec['paragraphs']}个有实质内容的中文段落，总长度不少于{spec['min_chars']}字。"
            "每段围绕一个中心意思展开，包含必要的事实、方法、数值或解释，并与相邻段自然衔接。"
            "不要使用条目堆砌结论，不要照抄artifact字段，不要输出路径、ID、哈希、英文状态值或审计内部字段。"
            "专业名称可保留英文，但整段论述必须使用中文。不能补造输入中不存在的事实。"
        )
        return instructions + (f"\n补充要求：\n{extra}" if extra else "")

    @classmethod
    def _normalize_section(cls, value: Any, spec: dict) -> dict:
        value = value if isinstance(value, dict) else {}
        paragraphs = cls._paragraphs(value.get("paragraphs") or value.get("content"))
        subsections = []
        for item in value.get("subsections") or []:
            if not isinstance(item, dict):
                continue
            child_paragraphs = cls._paragraphs(item.get("paragraphs") or item.get("content"))
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
        revised_abstract = cls._clean_text(audit.get("revised_abstract"))
        if len(revised_abstract) >= 180:
            abstract = revised_abstract
        by_id = {item["id"]: item for item in sections}
        for revision in audit.get("section_revisions") or []:
            if not isinstance(revision, dict):
                continue
            section = by_id.get(str(revision.get("section_id") or ""))
            replacements = cls._paragraphs(
                revision.get("replacement_paragraphs") or revision.get("paragraphs")
            )
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
            if facts is not None and resolve_fact_path(facts, source_path) is None:
                continue
            if sections is not None and not cls._failure_claim_exists(
                sections, section_id, paragraph_index, claim
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
                }
            )
        return failures

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
        index = 0 if paragraph_index == 0 else paragraph_index - 1
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
            "final_revision": cls._sanitize(revision),
            "verified_references": [
                {
                    "title": item.get("title", ""),
                    "authors": item.get("authors") or [],
                    "year": item.get("year"),
                    "identifiers": item.get("identifiers") or {},
                    "abstract": item.get("abstract") or item.get("summary") or "",
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
                references.append(item)
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
                str(reference.get("title") or "").lower(),
                str(reference.get("url") or "").lower(),
                str(identifiers.get("doi") or "").lower(),
                str(identifiers.get("arxiv") or "").lower(),
            ]
            if requested and any(
                request == field or request in field or field in request
                for request in requested
                for field in fields
                if field
            ):
                matched.append(reference)
        if matched:
            return matched[:10]
        generic_tokens = {
            "research", "experiment", "experimental", "result", "results", "data",
            "model", "models", "neural", "network", "networks", "training", "testing",
            "test", "accuracy", "method", "methods", "study", "learning",
        }
        context_tokens = set(re.findall(r"[a-z0-9]{3,}", context.lower())) - generic_tokens
        lexical_matches = []
        for reference in references:
            haystack = " ".join(
                [
                    str(reference.get("title") or ""),
                    str(reference.get("abstract") or reference.get("summary") or ""),
                ]
            ).lower()
            overlap = len(context_tokens.intersection(re.findall(r"[a-z0-9]{3,}", haystack)))
            if overlap:
                lexical_matches.append((overlap, reference))
        if lexical_matches:
            lexical_matches.sort(
                key=lambda item: (
                    item[0],
                    float(item[1].get("relevance") or 0),
                    float(item[1].get("reliability") or 0),
                ),
                reverse=True,
            )
            return [item[1] for item in lexical_matches[:8]]
        ranked = sorted(
            references,
            key=lambda item: (
                float(item.get("relevance") or 0),
                float(item.get("reliability") or 0),
            ),
            reverse=True,
        )
        return ranked[:5]

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
