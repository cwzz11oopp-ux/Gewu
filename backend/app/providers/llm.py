from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, replace
from threading import Event, Lock, local
from typing import Protocol

import httpx

from backend.app.config import Settings
from backend.app.providers.qwen_diagnostics import (
    append_trace,
    request_metrics,
    sanitized_response_excerpt,
)


class LLMRequestCancelled(RuntimeError):
    """Raised when a user stops a run while an LLM request is in flight."""

    def __init__(self) -> None:
        super().__init__("PIPELINE_STOPPED: user requested cancellation")


_CODE_TASKS = frozenset({
    "experiment.generate_code",
    "experiment.generate_bundle",
    "experiment.repair_bundle",
    "diagnostic.diagnose_experiment",
})

_TASK_OUTPUT_ROOT_KEYS: dict[str, tuple[str, ...]] = {
    "research.structure_problem": ("problem_statement", "literature_queries"),
    "hypothesis.generate": ("candidates",),
    "hypothesis.analyze_user_hypothesis": ("claim",),
    "idea_selection.review": ("evaluations",),
    "critic.evidence_reasoning": ("status",),
    "critic.review_result": ("verdict", "decision"),
    "reviewer.semantic": ("accepted", "issues"),
    "planning.build_plan": ("objective", "procedure"),
    "planning.refine_plan": ("objective", "procedure"),
    "planning.review_plan": ("verdict", "issues", "experiment_feasibility"),
    "planning.revise_from_review": ("objective", "procedure"),
    "experiment.generate_code": ("entrypoint", "files"),
    "experiment.generate_bundle": ("entrypoint", "files"),
    "experiment.repair_bundle": ("entrypoint", "files"),
    "experiment.analyze_results": ("metrics",),
    "experiment.audit_result": ("integrity_status",),
    "diagnostic.diagnose_experiment": ("category", "root_cause"),
    "writer.build_report": ("Problem Statement", "Methods"),
}


def normalize_task_output_shape(task: str, value: object) -> tuple[dict, bool]:
    """Unwrap a uniquely identifiable task payload without changing its facts."""
    if not isinstance(value, dict):
        raise ValueError(f"STRUCTURED_OUTPUT_NOT_OBJECT:{task}")
    required = _TASK_OUTPUT_ROOT_KEYS.get(task)
    if not required or all(key in value for key in required):
        return value, False

    matches: list[dict] = []

    def visit(candidate: object, depth: int) -> None:
        if depth > 3 or not isinstance(candidate, dict):
            return
        if all(key in candidate for key in required):
            matches.append(candidate)
            return
        for nested in candidate.values():
            if isinstance(nested, dict):
                visit(nested, depth + 1)

    visit(value, 0)
    unique = {id(match): match for match in matches}
    if len(unique) != 1:
        return value, False
    return dict(next(iter(unique.values()))), True

# Task-scoped output caps.  hypothesis.generate must emit 3-4 candidates with
# 20+ fields each; without a bounded max_tokens the model can run past the
# reasoning timeout and surface as a MODAL_REQUEST_TIMEOUT.  Giving only this
# task an explicit cap keeps its output complete (3000-4000 tokens) while every
# other task keeps the provider's configured default.
_TASK_MAX_TOKENS: dict[str, int] = {
    "hypothesis.generate": 4000,
    # Full-plan regeneration tasks: cap runaway generation (~30k tokens is far
    # above any real ~17k-char plan) so an unbounded output cannot hang a request
    # for 20+ min, while a genuine plan is never truncated.
    "planning.build_plan": 30000,
    "planning.revise_from_review": 30000,
}

# Tasks that must run WITHOUT model reasoning/thinking.  Thinking is now
# disabled at the policy level for every task (see _REASONING_POLICY /
# _CODE_POLICY below): the unbounded thinking phase of the qwen reasoning and
# coder models exceeded the request timeout (600-900s) on multiple tasks, while
# enable_thinking=false returned complete high-quality output in tens of
# seconds.  This set is kept as a belt-and-suspenders override so these tasks
# never regress to a thinking path even if a policy default is flipped back.
_TASK_DISABLE_THINKING: frozenset[str] = frozenset({
    "hypothesis.generate",
    "idea_selection.review",
    "critic.evidence_reasoning",
})

# Tasks that MUST run WITH model reasoning/thinking regardless of the global
# policy default.  planning.revise_from_review regressed to repeated
# PLAN_REVIEW_FIX_MAP_UNCHANGED validation failures with thinking OFF (measured
# A/B on the failed run: OFF leaves the BLOCK-03 dataset/split_contract/
# procedure contract fields materially unchanged while its fix_map over-claims
# them, so the revision never passes governance), whereas thinking ON, when it
# completes, produces a valid revision.  These tasks also STREAM: the
# unbounded thinking phase would otherwise trip the total read-timeout (900s),
# but streamed reasoning chunks keep the connection alive for as long as the
# model needs to finish thinking.
_TASK_ENABLE_THINKING: frozenset[str] = frozenset({
    "planning.revise_from_review",
})


def _max_tokens_payload(task: str, qwen_max_tokens: int) -> dict[str, int]:
    override = _TASK_MAX_TOKENS.get(task)
    if override:
        return {"max_tokens": override}
    if qwen_max_tokens > 0:
        return {"max_tokens": qwen_max_tokens}
    return {}


_REASONING_TASKS = frozenset({
    "hypothesis.generate",
    "hypothesis.analyze_user_hypothesis",
    "idea_selection.review",
    "critic.evidence_reasoning",
    "critic.review_result",
    "reviewer.semantic",
    "planning.refine_plan",
    "planning.review_plan",
    "experiment.analyze_results",
    "experiment.audit_result",
})


@dataclass(frozen=True)
class _TaskPolicy:
    route: str
    primary_setting: str
    fallback_setting: str | None
    enable_thinking: bool | None
    timeout_setting: str


# Thinking is disabled globally.  Measured on qwen3.7-plus / qwen3.7-max:
# reasoning tasks with enable_thinking=true routinely exceeded the 600-900s
# request timeout without emitting any content, while the same tasks with
# enable_thinking=false completed in tens of seconds with full-quality output.
_REASONING_POLICY = _TaskPolicy(
    route="reasoning",
    primary_setting="qwen_reasoning_model",
    fallback_setting="qwen_model",
    enable_thinking=False,
    timeout_setting="qwen_reasoning_timeout_seconds",
)
_GENERAL_POLICY = _TaskPolicy(
    route="general",
    primary_setting="qwen_model",
    fallback_setting="qwen_reasoning_model",
    enable_thinking=False,
    timeout_setting="qwen_general_timeout_seconds",
)
_CODE_POLICY = _TaskPolicy(
    route="code",
    primary_setting="qwen_code_model",
    fallback_setting="qwen_code_fallback_model",
    # Thinking disabled globally (same reasoning as _REASONING_POLICY).
    # Previously None (switch not sent), which let qwen3.7-max keep its server
    # default thinking ON: experiment.generate_bundle then timed out at 900s on
    # every attempt, while enable_thinking=false returned in ~40s.  The switch
    # IS accepted by the qwen coder models (verified by A/B test), so send it
    # explicitly.
    enable_thinking=False,
    timeout_setting="qwen_code_timeout_seconds",
)
_FAST_POLICY = _TaskPolicy(
    route="fast",
    primary_setting="qwen_fast_model",
    fallback_setting=None,
    enable_thinking=False,
    timeout_setting="qwen_fast_timeout_seconds",
)

_RETRYABLE_STATUS_CODES = frozenset({408, 409, 429, 500, 502, 503, 504})


class LLMProvider(Protocol):
    mode: str
    fallback: bool

    def generate_json(self, task: str, inputs: dict, schema_hint: dict, instructions: str = "") -> dict: ...

    def preflight(self, provider_id: str) -> dict: ...


class MockLLMProvider:
    mode = "mock"
    fallback = True

    def generate_json(self, task: str, inputs: dict, schema_hint: dict, instructions: str = "") -> dict:
        if task == "research.structure_problem":
            return {
                "problem_statement": str(inputs.get("problem_input") or ""),
                "constraints": ["Development fallback only; use Qwen for competition reasoning."],
                "knowledge_gaps": ["Evidence and experiment design require verification."],
                "literature_queries": [str(inputs.get("problem_input") or "")],
                "provider_mode": self.mode,
                "fallback_used": True,
                "fallback_reason": "Development fallback; not competition reasoning.",
            }
        if task == "hypothesis.generate":
            # Development fixtures must still obey the production provenance
            # contract.  This is an explicit mock response built from the full
            # persisted gap set, not an engine-side positional fallback.
            gap_processing = dict((inputs.get("research_synthesis") or {}).get("gap_processing") or {})
            source_gap_ids = [
                str(item) for item in gap_processing.get("source_gap_ids") or []
                if str(item).strip()
            ]
            candidate = {
                "method": "Train a compact baseline and one controlled variant with fixed seeds.",
                "mechanism": "Holding data, optimization, and seeds fixed isolates the intervention effect.",
                "evidence_basis": [{
                    "statement": "This fallback candidate still requires verified external evidence.",
                    "source_title": "Development fallback",
                    "source_url": "",
                    "evidence_type": "ASSUMPTION",
                }],
                "claim": "一个轻量神经网络变体可以在固定随机种子下通过可复现实验消融验证。",
                "verifiability": "固定随机种子、数据集和指标，对比基线与方法变体的实验结果。",
                "novelty_basis": ["开发模式候选假设来自已验证证据的元数据。"],
                "risks": ["Mock 推理不能作为正式证据。"],
                "source": "mock_fallback",
                "source_gap_ids": source_gap_ids,
            }
            return {
                "provider_mode": self.mode,
                "fallback_used": True,
                "fallback_reason": "Development fallback; not competition reasoning.",
                "candidates": [
                    candidate,
                    {
                        **candidate,
                        "claim": "A normalized low-rank variant can be tested against the same fixed baseline.",
                        "method": "Replace the full intervention with a low-rank controlled variant.",
                    },
                    {
                        **candidate,
                        "claim": "A parameter-matched variant can test whether gains come from capacity rather than mechanism.",
                        "method": "Compare the proposed intervention with a parameter-matched control.",
                    },
                ],
            }
        if task == "hypothesis.analyze_user_hypothesis":
            return {
                "provider_mode": self.mode,
                "fallback_used": True,
                "fallback_reason": "Development fallback; not competition reasoning.",
                "claim": inputs["user_hypothesis"],
                "method": "Run a controlled baseline-versus-variant experiment.",
                "mechanism": "The isolated intervention should explain any reproducible metric difference.",
                "evidence_basis": [],
                "verifiability": "Review against verified evidence, then test with a fixed-seed ablation.",
                "novelty_basis": ["User-supplied hypothesis analyzed in development fallback mode."],
                "risks": ["Requires Qwen review before competition use."],
                "analysis": "Development fallback analysis: the claim is only a draft until Qwen reviews evidence.",
                "source": "user",
            }
        if task == "idea_selection.review":
            evaluations = []
            for index, candidate in enumerate(inputs.get("candidates") or []):
                evaluations.append(
                    {
                        "candidate_index": index,
                        "idea_card": {"claim": candidate.get("claim", "")},
                        "evidence_ledger": [],
                        "closest_prior_work": [],
                        "gates": {"testability": "CONDITIONAL"},
                        "scores": {
                            "novelty": 1,
                            "scientific_soundness": 1,
                            "impact": 1,
                            "testability": 1,
                            "execution_feasibility": 1,
                            "reproducibility_compliance": 1,
                        },
                        "mde": {},
                        "risks": ["Development fallback is not competition evidence."],
                        "decision": "REVISE" if index == 0 else "EVIDENCE_INSUFFICIENT",
                        "confidence": "low",
                        "unknowns": ["Requires Qwen review before competition use."],
                    }
                )
            return {"evaluations": evaluations}
        if task == "critic.evidence_reasoning":
            hypothesis = inputs.get("hypothesis") or {}
            active = hypothesis.get("active") or hypothesis
            return {
                "active_hypothesis": active,
                "selected": active,
                "revised_hypothesis": {
                    **active,
                    "claim": f"{active.get('claim', '')} (development-only controlled test).",
                },
                "revision_reason": "Narrowed the fallback claim to a controlled development test.",
                "support": list(inputs.get("evidence") or []),
                "warnings": ["Development fallback; Qwen review is still required."],
                "provider_mode": self.mode,
                "fallback_used": True,
                "fallback_reason": "Development fallback; not competition reasoning.",
            }
        if task == "critic.review_result":
            return {
                "verdict": "partial",
                "decision": "REVISE",
                "feedback": "Development fallback cannot validate a scientific claim.",
                "required_revision": "Review real metrics and verified evidence with Qwen.",
                "supported_claims": [],
                "unsupported_claims": ["Competition claim is unsupported by mock evidence."],
                "revisions": ["Run and audit a real experiment."],
                "next_action": "Run and audit a real experiment with the configured provider.",
                "evidence_links": [],
                "overclaim_risks": ["Mock reasoning is not competition evidence."],
                "provider_mode": self.mode,
                "fallback_used": True,
                "fallback_reason": "Development fallback; not competition reasoning.",
            }
        if task in {"scientific.primary_result_analysis", "scientific.independent_result_review"}:
            return {
                "hypothesis_status": "INCONCLUSIVE",
                "supported_findings": [],
                "contradicting_findings": [],
                "alternative_explanations": ["Development fallback does not establish a scientific conclusion."],
                "confounders": ["Mock reasoning"],
                "evidence_gaps": ["Requires configured scientific review."],
                "interpretation": "No scientific conclusion is justified in development fallback mode.",
                "recommended_action": "MORE_EVIDENCE",
                "proposed_hypothesis": None,
                "confidence": 0.0,
            }
        if task == "critic.select_iteration_direction":
            candidate = {
                "name": "保持控制条件的单变量复核",
                "problem_addressed": "验证当前结果是否能在固定条件下复现",
                "result_basis": ["当前结果尚未形成充分结论"],
                "evidence_basis": [],
                "changed_variable": "增加一次预先声明的单变量消融",
                "fixed_controls": ["数据划分", "随机种子", "评价指标"],
                "target_metrics": ["主要评价指标"],
                "possible_regressions": ["计算成本增加"],
                "information_gain": "high",
                "expected_benefit": "medium",
                "evidence_confidence": "low",
                "compute_cost": "一次小规模受控实验",
                "scientific_risk": "low",
                "success_rule": "达到原计划阈值且多个随机种子方向一致",
                "failure_rule": "未达到原计划阈值或结果方向不稳定",
                "stop_rule": "完成该消融后依据预设阈值停止或形成新假设",
            }
            return {
                "decision": "REVISE",
                "evidence_sufficiency": "SUFFICIENT",
                "evidence_assessment": [],
                "optimization_candidates": [
                    candidate,
                    {
                        **candidate,
                        "name": "保持方法不变并增加重复实验",
                        "changed_variable": "仅增加重复次数",
                        "information_gain": "medium",
                    },
                ],
                "selected_direction": candidate,
                "selection_reason": "优先选择能够区分原因且不改变原始成功标准的最小实验。",
                "next_action": "按固定控制生成下一轮实验合同。",
            }
        if task == "experiment.analyze_results":
            result = dict(inputs.get("result") or {})
            return {
                "experiment_id": result.get("experiment_id", ""),
                "result_id": result.get("result_id", ""),
                "metrics": dict(result.get("metrics") or {}),
                "comparisons": [],
                "observations": ["Development fixture completed."],
                "limitations": ["Mock results are not competition evidence."],
                "verdict": "partial",
                "provider_mode": self.mode,
                "fallback_used": True,
                "fallback_reason": "Development fallback; not competition reasoning.",
            }
        if task == "experiment.audit_result":
            return {
                "integrity_status": "passed",
                "issues": [],
                "verified_files": [
                    {"path": item.get("path", ""), "sha256": item.get("sha256", "")}
                    for item in inputs.get("files") or []
                ],
                "environment_summary": dict(
                    (inputs.get("result") or {}).get("environment") or {}
                ),
                "is_real_experiment": False,
                "provider_mode": self.mode,
                "fallback_used": True,
                "fallback_reason": "Development fallback; not competition reasoning.",
            }
        if task == "diagnostic.diagnose_experiment":
            return {
                "category": "unknown",
                "root_cause": "Development fallback cannot determine a safe repair.",
                "evidence": [str(inputs.get("error") or "unknown failure")],
                "retryable": False,
                "auto_repairable": False,
                "repair_action": "none",
                "repair_scope": "none",
                "user_message": "实验失败，开发模式未找到可安全执行的自动修复。",
                "next_action": "查看原始错误并人工处理。",
                "provider_mode": self.mode,
                "fallback_used": True,
                "fallback_reason": "Development fallback; diagnosis is advisory only.",
            }
        if task == "planning.refine_plan":
            current_plan = dict(inputs.get("current_plan") or {})
            feedback = dict(inputs.get("feedback") or {})
            additional_sections = dict(current_plan.get("additional_sections") or {})
            additional_sections["feedback_revision"] = str(
                feedback.get("required_revision") or feedback.get("feedback") or ""
            )
            return {
                **current_plan,
                "additional_sections": additional_sections,
                "provider_mode": self.mode,
                "fallback_used": True,
                "fallback_reason": "Development fallback; not competition reasoning.",
            }
        if task == "planning.review_plan":
            return {
                "verdict": "ACCEPT", "issues": [], "required_changes": [],
                "suggested_fixes": [], "revised_plan_guidance": [],
                "experiment_feasibility": "FEASIBLE", "provider_mode": self.mode,
            }
        if task == "writer.report_outline":
            return {
                "title": "开发环境中的受控实验流程验证报告",
                "central_question": "研究流程能否保存证据、实验与修订产物并形成系统报告？",
                "narrative_logic": "从问题界定进入方法设计，再按实验迭代、结果分析和结论边界展开。",
                "section_plans": [
                    {
                        "id": item.get("id", ""),
                        "purpose": item.get("purpose", ""),
                        "evidence": ["facts"],
                        "transition": "承接前文并为下一章建立事实基础。",
                    }
                    for item in inputs.get("required_sections") or []
                ],
                "reference_selection": ["DOME"],
            }
        if task in {"writer.report_section", "writer.revise_report_section"}:
            section = inputs.get("required_section") or inputs.get("section") or {}
            if task == "writer.report_section":
                section = inputs.get("section") or {}
            title = section.get("title") or "研究报告章节"
            purpose = section.get("purpose") or "说明本章的研究事实与边界。"
            paragraphs = [
                (
                    f"{title}围绕开发环境中的流程验证展开。本章的具体任务是{purpose}"
                    "由于当前使用的是开发模式，以下论述只检查研究问题、方法、实验结果与结论之间能否形成"
                    "连续关系，不把模拟产物解释为真实的科学证据，也不据此扩展研究结论。"
                ) * 3,
                (
                    "本章采用已保存的研究产物作为唯一事实来源，并将问题背景、受控变量、评价指标和"
                    "实验状态放在同一论证链中说明。这样的组织方式用于验证报告能否从原始记录中筛选"
                    "与读者有关的信息，同时排除路径、哈希、内部状态和值班日志等实现细节。"
                ) * 3,
                (
                    "在解释结果时，报告只描述产物中已经记录的数值和限制，不使用宣传性判断，也不把"
                    "流程成功等同于研究假设成立。开发模式尚不能回答真实实验中的效应大小、统计稳定性"
                    "和外部有效性，因此这些问题仍需在配置正式模型、数据和计算环境后重新验证。"
                ) * 3,
                (
                    "从章节衔接看，本章提供的事实将作为后续分析的边界。下一部分只能在相同的数据、"
                    "模型、参数和评价口径下继续推导；若关键记录缺失，应直接说明缺失内容，而不是依靠"
                    "一般经验补写。由此可以保证最终报告结构完整，但不会超出证据强度给出结论。"
                ) * 3,
            ]
            return {
                "id": section.get("id") or "section",
                "title": title,
                "paragraphs": paragraphs,
                "subsections": [],
                "citations": [],
            }
        if task == "writer.report_abstract":
            return {
                "abstract": (
                    "本报告在开发环境中检验自动研究流程能否将问题界定、实验设计、逐轮修订和最终结果"
                    "组织成连续的中文研究文本。系统以已经保存的产物为事实边界，先规划章节，再分别生成"
                    "正文并执行一致性审查；涉及实验的内容只保留可核对的设置、指标和限制。由于本次运行"
                    "采用模拟模型，报告不提出真实科学主张，也不把流程完成解释为假设得到支持。该结果"
                    "仅说明分章写作、证据约束和文档导出链路能够工作，正式研究结论仍需由真实数据、"
                    "受控实验和经核验的文献共同支持。"
                ),
                "keywords": ["研究流程", "受控实验", "分章写作", "证据约束"],
            }
        if task == "writer.audit_report":
            return {
                "accepted": True,
                "hard_failures": [],
                "revision_required": [],
                "soft_style_issues": [],
                "section_scores": [],
                "revised_abstract": "",
                "section_revisions": [],
            }
        if task == "writer.repair_report_audit":
            return {
                "revised_abstract": "",
                "section_revisions": [],
            }
        if task == "writer.verify_report_audit":
            return {"hard_failures": []}
        if task == "writer.build_report":
            return {
                "Problem Statement": "这是用于验证报告流程的开发环境示例，不构成正式研究结论。",
                "Rationale": "仅检查报告生成、打包和下载链路，不据此提出科学主张。",
                "Technical Details": ["使用 Mock provider 运行开发流程"],
                "Datasets": "开发模式未提供正式数据集。",
                "Source": [],
                "Target": "验证报告生成与导出流程。",
                "Paper Title": "开发环境报告流程验证",
                "Paper Abstract": "本报告由开发模式生成，只用于检查研究汇报、复现材料和下载包是否能够正确产出，不可作为竞赛证据。",
                "Methods": ["运行 Mock 工作流并检查持久化产物"],
                "Experiments": {"mode": "mock", "说明": "未执行真实竞赛实验"},
                "provider_mode": self.mode,
                "fallback_used": True,
                "fallback_reason": "Development fallback; not competition reasoning.",
            }
        if task == "paper.plan":
            return {
                "title": "开发环境论文写作流程验证",
                "research_question": "交互式论文写作流程能否保存阶段产物并完成导出？",
                "terminal_verdict": "仅验证流程，不构成研究结论。",
                "contributions": ["验证大纲确认、逐章写作和最终导出链路。"],
                "sections": [
                    {
                        "id": "introduction",
                        "title": "引言",
                        "purpose": "说明问题与写作边界。",
                        "key_points": ["开发模式仅用于流程测试。"],
                        "evidence": ["report"],
                        "citations": [],
                    },
                    {
                        "id": "results",
                        "title": "实验结果",
                        "purpose": "说明持久化实验结果。",
                        "key_points": ["不得将 Mock 数值作为竞赛证据。"],
                        "evidence": ["experiment_result"],
                        "citations": [],
                    },
                ],
                "claims_evidence": [
                    {"claim": "论文流程能够完成阶段持久化。", "evidence": ["paper_writing"]}
                ],
                "figures": [],
                "limitations": ["开发模式不生成可投稿论文。"],
            }
        if task in {"paper.write_section", "paper.revise_section"}:
            section = inputs.get("section") or {}
            return {
                "id": section.get("id") or "section",
                "title": section.get("title") or "章节",
                "content": (
                    "本章节由开发模式生成，用于验证 Qwen 论文写作流程的阶段保存、"
                    "人工确认与导出能力。这里不提出新的科学主张，也不补造实验数据。"
                ),
                "citations": [],
            }
        if task == "paper.audit":
            return {
                "accepted": True,
                "summary": "开发模式审计通过，仅代表流程结构完整。",
                "issues": [],
                "numeric_claim_checks": [],
                "citation_checks": [],
            }
        if task == "experiment.generate_code":
            seed = int(inputs.get("task", {}).get("seed") or 7)
            return {
                "provider_mode": self.mode,
                "fallback_used": True,
                "fallback_reason": "Development fallback; generated toy experiment code.",
                "entrypoint": "train.py",
                "files": [{
                    "path": "train.py",
                    "content": (
                        "from pathlib import Path\n"
                        "import argparse, json, random\n"
                        "parser = argparse.ArgumentParser()\n"
                        "parser.add_argument('--seed', type=int, default=7)\n"
                        "parser.add_argument('--output', required=True)\n"
                        "args = parser.parse_args()\n"
                        "random.seed(args.seed)\n"
                        "metrics = {'accuracy': 0.5 + (args.seed % 10) / 100, 'seed': args.seed}\n"
                        "Path(args.output).parent.mkdir(parents=True, exist_ok=True)\n"
                        "Path(args.output).write_text(json.dumps(metrics), encoding='utf-8')\n"
                        "print(json.dumps(metrics))\n"
                    ),
                }],
                "metrics_path": f"results/run_seed_{seed}.json",
                "log_path": f"logs/run_seed_{seed}.log",
                "assumptions": ["Mock LLM generated deterministic toy code."],
                "validation": {"requires_network": False, "expected_metrics": ["accuracy"]},
            }
        return {
            "task": task,
            "provider_mode": self.mode,
            "fallback_used": True,
            "fallback_reason": "Development fallback; not competition reasoning.",
            "content": inputs,
            "schema_hint": schema_hint,
        }


class QwenLLMProvider:
    mode = "qwen"
    fallback = False

    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        self.settings = settings
        self._call_state = local()
        self._cancel_events: dict[str, Event] = {}
        self._cancel_guard = Lock()
        self._owns_client = client is None
        self.client = client or httpx.Client(
            timeout=httpx.Timeout(
                connect=10.0,
                read=float(settings.qwen_timeout_seconds),
                write=30.0,
                pool=10.0,
            )
        )

    def begin_run(self, run_id: str) -> None:
        event = Event()
        with self._cancel_guard:
            self._cancel_events[run_id] = event
        self._call_state.run_id = run_id
        self._call_state.cancel_event = event

    def end_run(self, run_id: str) -> None:
        with self._cancel_guard:
            self._cancel_events.pop(run_id, None)
        if getattr(self._call_state, "run_id", None) == run_id:
            self._call_state.run_id = None
            self._call_state.cancel_event = None

    def cancel_run(self, run_id: str) -> bool:
        with self._cancel_guard:
            event = self._cancel_events.get(run_id)
        if event is None:
            return False
        event.set()
        return True

    def _raise_if_cancelled(self) -> None:
        event = getattr(self._call_state, "cancel_event", None)
        if event is not None and event.is_set():
            raise LLMRequestCancelled()

    def preflight(self, provider_id: str = "qwen") -> dict:
        """Verify endpoint, authentication, model and JSON-mode without exposing secrets."""
        response = self.generate_json(
            "provider.preflight",
            {"purpose": "run_admission", "provider": provider_id},
            {"ready": "boolean"},
            "Return exactly one JSON object with a boolean ready field.",
        )
        if not isinstance(response, dict):
            raise RuntimeError(f"{provider_id.upper()}_PREFLIGHT_STRUCTURED_RESPONSE_INVALID")
        return {"provider": provider_id, "model": self._model_for_task("provider.preflight"), "structured": True}

    def generate_json(self, task: str, inputs: dict, schema_hint: dict, instructions: str = "") -> dict:
        if not self.settings.qwen_api_key:
            raise RuntimeError("QWEN_API_KEY_MISSING")
        policy = self._policy_for_task(task)
        # Task-level overrides win: a whitelist forces thinking ON (these tasks
        # also stream, see _post), a denylist forces it OFF, otherwise the
        # policy default (now globally disabled) applies.
        if task in _TASK_ENABLE_THINKING:
            enable_thinking = True
        else:
            enable_thinking = False if task in _TASK_DISABLE_THINKING else policy.enable_thinking
        models = self._models_for_policy(policy)
        timeout_seconds = int(getattr(self.settings, policy.timeout_setting))
        failures: list[str] = []
        response: httpx.Response | None = None
        model = models[0]
        last_exception: Exception | None = None
        diagnostic_path = os.environ.get("QWEN_DIAGNOSTIC_LOG", "").strip()
        metrics = request_metrics(task, inputs, schema_hint, instructions)

        for model in models:
            for attempt in range(self.settings.qwen_retries_per_model + 1):
                self._raise_if_cancelled()
                attempt_number = attempt + 1
                attempt_started = time.monotonic()
                if diagnostic_path:
                    append_trace(
                        diagnostic_path,
                        {
                            "event": "attempt_started",
                            "run_id": getattr(self._call_state, "run_id", None),
                            "task": task,
                            "route": policy.route,
                            "model": model,
                            "attempt": attempt_number,
                            "attempt_limit": self.settings.qwen_retries_per_model + 1,
                            "timeout_seconds": timeout_seconds,
                            "enable_thinking": enable_thinking,
                            **metrics,
                        },
                    )
                try:
                    candidate = self._post(
                        task,
                        inputs,
                        schema_hint,
                        instructions,
                        model,
                        enable_thinking,
                        timeout_seconds,
                    )
                except httpx.TimeoutException as exc:
                    last_exception = exc
                    failures.append(f"{model}:timeout")
                    if diagnostic_path:
                        append_trace(
                            diagnostic_path,
                            {
                                "event": "attempt_finished",
                                "run_id": getattr(self._call_state, "run_id", None),
                                "task": task,
                                "route": policy.route,
                                "model": model,
                                "attempt": attempt_number,
                                "outcome": "timeout",
                                "duration_seconds": round(
                                    time.monotonic() - attempt_started, 3
                                ),
                                "exception_type": type(exc).__name__,
                                "exception_message": str(exc),
                                **metrics,
                            },
                        )
                except httpx.TransportError as exc:
                    last_exception = exc
                    failures.append(f"{model}:transport:{type(exc).__name__}")
                    if diagnostic_path:
                        append_trace(
                            diagnostic_path,
                            {
                                "event": "attempt_finished",
                                "run_id": getattr(self._call_state, "run_id", None),
                                "task": task,
                                "route": policy.route,
                                "model": model,
                                "attempt": attempt_number,
                                "outcome": "transport_error",
                                "duration_seconds": round(
                                    time.monotonic() - attempt_started, 3
                                ),
                                "exception_type": type(exc).__name__,
                                "exception_message": str(exc),
                                **metrics,
                            },
                        )
                else:
                    if diagnostic_path:
                        append_trace(
                            diagnostic_path,
                            {
                                "event": "attempt_finished",
                                "run_id": getattr(self._call_state, "run_id", None),
                                "task": task,
                                "route": policy.route,
                                "model": model,
                                "attempt": attempt_number,
                                "outcome": (
                                    "http_success"
                                    if candidate.status_code < 400
                                    else "http_error"
                                ),
                                "duration_seconds": round(
                                    time.monotonic() - attempt_started, 3
                                ),
                                "http_status": candidate.status_code,
                                "response_characters": len(candidate.text),
                                "response_excerpt": (
                                    sanitized_response_excerpt(
                                        candidate.text,
                                        self.settings.qwen_api_key,
                                    )
                                    if candidate.status_code >= 400
                                    else ""
                                ),
                                "request_id": (
                                    candidate.headers.get("x-request-id")
                                    or candidate.headers.get("x-dashscope-request-id")
                                    or ""
                                ),
                                **metrics,
                            },
                        )
                    if candidate.status_code < 400:
                        response = candidate
                        break
                    failures.append(f"{model}:http_{candidate.status_code}")
                    if candidate.status_code not in _RETRYABLE_STATUS_CODES:
                        request_id = candidate.headers.get("x-request-id") or candidate.headers.get("x-dashscope-request-id") or ""
                        excerpt = sanitized_response_excerpt(candidate.text, self.settings.qwen_api_key, limit=800)
                        raise RuntimeError(
                            f"MODEL_PROVIDER_CONFIG_ERROR:provider={self.mode}:model={model}:task={task}:"
                            f"http_status={candidate.status_code}:request_id={request_id}:response_excerpt={excerpt}"
                        )
                    last_exception = httpx.HTTPStatusError(
                        f"Retryable Qwen response: {candidate.status_code}",
                        request=candidate.request,
                        response=candidate,
                    )
                if attempt < self.settings.qwen_retries_per_model:
                    time.sleep(min(2.0, float(2 ** attempt)))
            if response is not None:
                break

        if response is None:
            failure_summary = ",".join(failures)
            if failures and all(":timeout" in failure for failure in failures):
                raise RuntimeError(
                    f"MODEL_REQUEST_TIMEOUT:provider={self.mode}:model={model}:task={task}:"
                    f"timeout_seconds={timeout_seconds}:attempts={failure_summary}"
                ) from last_exception
            raise RuntimeError(
                f"MODEL_REQUEST_FAILED:provider={self.mode}:model={model}:task={task}:"
                f"route={policy.route}:exception_type={type(last_exception).__name__ if last_exception else 'Unknown'}:"
                f"attempts={failure_summary}"
            ) from last_exception

        choice = response.json()["choices"][0]
        message = str((choice.get("message") or {}).get("content") or "")
        finish_reason = str(choice.get("finish_reason") or "unknown")
        if not message.strip():
            raise ValueError(
                "MODEL_EMPTY_OUTPUT:"
                f"provider={self.mode}:model={model}:task={task}:"
                f"finish_reason={finish_reason}"
            )
        if finish_reason == "length":
            code = (
                "EXPERIMENT_CODE_OUTPUT_TRUNCATED"
                if task.startswith("experiment.")
                else "QWEN_OUTPUT_TRUNCATED"
            )
            raise ValueError(
                f"{code}: model={model}:characters={len(message)}:"
                "increase QWEN_MAX_TOKENS or reduce the requested output"
            )
        json_repaired = False
        try:
            parsed = _loads_model_json(message, task)
        except json.JSONDecodeError as exc:
            parsed = self._repair_json(message, schema_hint, task=task)
            if parsed is None:
                if not task.startswith("experiment."):
                    raise
                raw_tail = json.dumps(message[-240:], ensure_ascii=True)
                raise ValueError(
                    "EXPERIMENT_CODE_GENERATION_INVALID_JSON:"
                    f"finish_reason={finish_reason}:characters={len(message)}:"
                    f"line={exc.lineno}:column={exc.colno}:raw_tail={raw_tail}"
                ) from exc
            json_repaired = True
        parsed, shape_normalized = normalize_task_output_shape(task, parsed)
        parsed.setdefault("provider_mode", self.mode)
        parsed.setdefault("fallback_used", False)
        parsed["model_used"] = model
        parsed["model_route"] = policy.route
        parsed["model_fallback_used"] = model != models[0]
        parsed["model_fallback_reason"] = ",".join(failures) if model != models[0] else ""
        parsed["thinking_enabled"] = enable_thinking is True
        parsed["json_repaired"] = json_repaired
        parsed["shape_normalized"] = shape_normalized
        self._call_state.metadata = {
            "task": task,
            "model_used": model,
            "model_route": policy.route,
            "model_fallback_used": model != models[0],
            "model_fallback_reason": parsed["model_fallback_reason"],
            "thinking_enabled": enable_thinking is True,
            "json_repaired": json_repaired,
            "shape_normalized": shape_normalized,
        }
        return parsed

    def consume_call_metadata(self) -> dict:
        metadata = dict(getattr(self._call_state, "metadata", {}) or {})
        self._call_state.metadata = {}
        return metadata

    @staticmethod
    def _policy_for_task(task: str) -> _TaskPolicy:
        if task in _CODE_TASKS:
            return _CODE_POLICY
        if task in _REASONING_TASKS:
            return _REASONING_POLICY
        if task == "format.repair_json":
            return _FAST_POLICY
        return _GENERAL_POLICY

    def _models_for_policy(self, policy: _TaskPolicy) -> list[str]:
        names = [str(getattr(self.settings, policy.primary_setting) or "").strip()]
        if policy.fallback_setting:
            names.append(str(getattr(self.settings, policy.fallback_setting) or "").strip())
        return list(dict.fromkeys(name for name in names if name))

    def _model_for_task(self, task: str) -> str:
        return self._models_for_policy(self._policy_for_task(task))[0]

    def _repair_json(
        self, raw_output: str, schema_hint: dict, *, task: str
    ) -> dict | None:
        policy = _FAST_POLICY
        model = self._models_for_policy(policy)[0]
        try:
            response = self._post(
                "format.repair_json",
                {"raw_output": raw_output},
                schema_hint,
                (
                    "Repair JSON syntax and schema shape only. Preserve all factual values, code, "
                    "metrics, paths, citations, and claims exactly. Do not add new facts."
                ),
                model,
                policy.enable_thinking,
                int(getattr(self.settings, policy.timeout_setting)),
            )
            response.raise_for_status()
            repaired = _loads_model_json(
                response.json()["choices"][0]["message"]["content"], task
            )
        except (httpx.HTTPError, KeyError, TypeError, json.JSONDecodeError):
            return None
        return repaired if isinstance(repaired, dict) else None

    def _post(
        self,
        task: str,
        inputs: dict,
        schema_hint: dict,
        instructions: str,
        model: str,
        enable_thinking: bool | None,
        timeout_seconds: int,
    ) -> httpx.Response:
        url = f"{self.settings.qwen_base_url}/chat/completions"
        request_kwargs = dict(
            headers={"Authorization": f"Bearer {self.settings.qwen_api_key}"},
            timeout=httpx.Timeout(
                connect=min(10.0, float(timeout_seconds)),
                read=float(timeout_seconds),
                write=min(30.0, float(timeout_seconds)),
                pool=min(10.0, float(timeout_seconds)),
            ),
            json={
                "model": model,
                **(
                    {"enable_thinking": enable_thinking}
                    if enable_thinking is not None
                    else {}
                ),
                **_max_tokens_payload(task, self.settings.qwen_max_tokens),
                "response_format": {"type": "json_object"},
                "messages": [
                    {
                        "role": "system",
                        "content": "You are an AI Scientist assistant for verifiable neural-network experiments.",
                    },
                    *([
                        {"role": "system", "content": instructions},
                    ] if instructions else []),
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "task": task,
                                "inputs": inputs,
                                "schema_hint": schema_hint,
                                "rules": [
                                    "Return JSON only.",
                                    "Do not invent references.",
                                    "Do not invent experiment metrics, seeds, datasets, commands, logs, or result paths.",
                                    "Use only verified evidence and existing artifacts when making claims.",
                                ],
                            },
                            ensure_ascii=False,
                        ),
                    },
                ],
            },
        )
        cancel_event = getattr(self._call_state, "cancel_event", None)
        if enable_thinking is True:
            # Thinking-ON tasks stream.  With non-streaming the total read
            # timeout covers the WHOLE response, so the unbounded thinking phase
            # trips it (measured: qwen3.7-plus revise >600s).  Streaming makes
            # the read timeout per-chunk: as long as reasoning deltas keep
            # arriving the request completes whenever thinking finishes.
            request_kwargs["json"]["stream"] = True
            if cancel_event is None or not self._owns_client:
                return self._post_stream(url, request_kwargs)
            return asyncio.run(
                self._post_stream_cancellable(url, request_kwargs, cancel_event)
            )
        if cancel_event is None or not self._owns_client:
            return self.client.post(url, **request_kwargs)
        return asyncio.run(self._post_cancellable(url, request_kwargs, cancel_event))

    @staticmethod
    async def _post_cancellable(
        url: str,
        request_kwargs: dict,
        cancel_event: Event,
    ) -> httpx.Response:
        async with httpx.AsyncClient() as client:
            request_task = asyncio.create_task(client.post(url, **request_kwargs))

            async def wait_for_cancel() -> None:
                while not cancel_event.is_set():
                    await asyncio.sleep(0.1)

            cancel_task = asyncio.create_task(wait_for_cancel())
            done, _ = await asyncio.wait(
                {request_task, cancel_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if cancel_task in done:
                request_task.cancel()
                try:
                    await request_task
                except asyncio.CancelledError:
                    pass
                raise LLMRequestCancelled()
            cancel_task.cancel()
            return await request_task

    def _post_stream(self, url: str, request_kwargs: dict) -> httpx.Response:
        """SSE-stream a chat-completions call and rebuild a single JSON response.

        Accumulates ``delta.content`` (and ``delta.reasoning_content`` when the
        model thinks) across chunks, captures usage + finish_reason, and returns
        a synthetic ``httpx.Response`` shaped exactly like the non-streaming
        body so every caller (``generate_json`` / ``_repair_json``) is
        unchanged.  On an HTTP error the raw body is preserved so the retry
        loop can inspect status/text/request-id as usual.
        """
        with self.client.stream("POST", url, **request_kwargs) as response:
            if response.status_code >= 400:
                error_body = response.read()
                return httpx.Response(
                    response.status_code,
                    content=error_body,
                    headers=dict(response.headers),
                    request=response.request,
                )
            content: list[str] = []
            reasoning: list[str] = []
            usage: dict = {}
            finish_reason = None
            for line in response.iter_lines():
                if not line or not line.startswith("data: "):
                    continue
                data = line[6:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except (json.JSONDecodeError, ValueError):
                    continue
                if chunk.get("usage"):
                    usage = chunk["usage"]
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                if delta.get("reasoning_content"):
                    reasoning.append(delta["reasoning_content"])
                if delta.get("content"):
                    content.append(delta["content"])
                if choices[0].get("finish_reason"):
                    finish_reason = choices[0].get("finish_reason")
            body = {
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "".join(content),
                            **(
                                {"reasoning_content": "".join(reasoning)}
                                if reasoning
                                else {}
                            ),
                        },
                        "finish_reason": finish_reason,
                    }
                ],
                "usage": usage or {},
            }
            return httpx.Response(
                200,
                json=body,
                headers=dict(response.headers),
                request=response.request,
            )

    @staticmethod
    async def _post_stream_cancellable(
        url: str,
        request_kwargs: dict,
        cancel_event: Event,
    ) -> httpx.Response:
        async with httpx.AsyncClient() as client:
            async def read_stream() -> httpx.Response:
                async with client.stream("POST", url, **request_kwargs) as response:
                    if response.status_code >= 400:
                        error_body = await response.aread()
                        return httpx.Response(
                            response.status_code,
                            content=error_body,
                            headers=dict(response.headers),
                            request=response.request,
                        )
                    content: list[str] = []
                    reasoning: list[str] = []
                    usage: dict = {}
                    finish_reason = None
                    async for line in response.aiter_lines():
                        if not line or not line.startswith("data: "):
                            continue
                        data = line[6:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                        except (json.JSONDecodeError, ValueError):
                            continue
                        if chunk.get("usage"):
                            usage = chunk["usage"]
                        choices = chunk.get("choices") or []
                        if not choices:
                            continue
                        delta = choices[0].get("delta") or {}
                        if delta.get("reasoning_content"):
                            reasoning.append(delta["reasoning_content"])
                        if delta.get("content"):
                            content.append(delta["content"])
                        if choices[0].get("finish_reason"):
                            finish_reason = choices[0].get("finish_reason")
                    body = {
                        "choices": [
                            {
                                "index": 0,
                                "message": {
                                    "role": "assistant",
                                    "content": "".join(content),
                                    **(
                                        {"reasoning_content": "".join(reasoning)}
                                        if reasoning
                                        else {}
                                    ),
                                },
                                "finish_reason": finish_reason,
                            }
                        ],
                        "usage": usage or {},
                    }
                    return httpx.Response(
                        200,
                        json=body,
                        headers=dict(response.headers),
                        request=response.request,
                    )

            stream_task = asyncio.create_task(read_stream())

            async def wait_for_cancel() -> None:
                while not cancel_event.is_set():
                    await asyncio.sleep(0.1)

            cancel_task = asyncio.create_task(wait_for_cancel())
            done, _ = await asyncio.wait(
                {stream_task, cancel_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if cancel_task in done:
                stream_task.cancel()
                try:
                    await stream_task
                except asyncio.CancelledError:
                    pass
                raise LLMRequestCancelled()
            cancel_task.cancel()
            return await stream_task


def get_llm_provider(settings: Settings, client: httpx.Client | None = None) -> LLMProvider:
    if settings.llm_provider == "mock":
        if settings.competition_mode:
            raise RuntimeError("QWEN_PROVIDER_REQUIRED")
        return MockLLMProvider()
    instances = _build_role_providers(settings, client=client)
    return ModelRoleRouter(settings, instances)


class DeepSeekLLMProvider(QwenLLMProvider):
    """OpenAI-compatible DeepSeek client using the existing call lifecycle and policy."""

    mode = "deepseek"

    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        compatible = replace(
            settings,
            qwen_api_key=settings.deepseek_api_key,
            qwen_base_url=settings.deepseek_base_url,
            qwen_model=settings.deepseek_model,
            qwen_reasoning_model=settings.deepseek_model,
            qwen_code_model=settings.deepseek_model,
            qwen_code_fallback_model=settings.deepseek_model,
            qwen_fast_model=settings.deepseek_model,
        )
        super().__init__(compatible, client=client)

    def generate_json(self, task: str, inputs: dict, schema_hint: dict, instructions: str = "") -> dict:
        if not self.settings.deepseek_api_key:
            raise RuntimeError("MODEL_PROVIDER_FAILURE:DEEPSEEK_API_KEY_MISSING")
        return super().generate_json(task, inputs, schema_hint, instructions)

    def _models_for_policy(self, policy: _TaskPolicy) -> list[str]:
        return [self.settings.deepseek_model]


class _ConfiguredProvider(QwenLLMProvider):
    """A role-routed provider bound to one concrete provider_id + model."""

    def __init__(self, settings: Settings, provider_id: str, client: httpx.Client | None = None) -> None:
        super().__init__(settings, client=client)
        self.mode = provider_id


def _build_role_providers(settings: Settings, client: httpx.Client | None = None) -> dict[tuple[str, str], QwenLLMProvider]:
    """Build one provider instance per (provider_id, model) the roles actually use.

    The user's saved model config is the single source of truth: a role's
    provider_id and model come only from ``model_role_assignments``, and each
    provider's base_url / api_key come only from ``model_provider_configs``.
    """
    configs: dict[str, dict[str, object]] = getattr(settings, "model_provider_configs", {}) or {}
    instances: dict[tuple[str, str], QwenLLMProvider] = {}
    for assignment in (settings.model_role_assignments or {}).values():
        provider_id = str((assignment or {}).get("provider_id") or "").strip()
        model = str((assignment or {}).get("model") or "").strip()
        if not provider_id or not model:
            continue
        config = configs.get(provider_id)
        if not config or not str(config.get("base_url") or ""):
            continue
        key = (provider_id, model)
        if key in instances:
            continue
        compatible = replace(
            settings,
            qwen_api_key=str(config.get("api_key") or ""),
            qwen_base_url=str(config.get("base_url") or ""),
            qwen_model=model,
            qwen_reasoning_model=model,
            qwen_code_model=model,
            qwen_code_fallback_model=model,
            qwen_fast_model=model,
        )
        instances[key] = _ConfiguredProvider(compatible, provider_id, client=client)
    return instances


class ModelRoleRouter:
    """Routes scientific roles to the provider/model pairs the user configured."""

    mode = "role_router"
    fallback = False

    def __init__(self, settings: Settings, providers: dict[tuple[str, str], LLMProvider]) -> None:
        self.settings = settings
        self.providers = providers
        self._last_provider: LLMProvider | None = None

    def _route(self, role: str):
        assignment = self.settings.model_role_assignments.get(role) or {}
        provider_id = str(assignment.get("provider_id") or "").strip()
        model = str(assignment.get("model") or "").strip()
        if not provider_id or not model:
            raise RuntimeError(f"MODEL_ROLE_NOT_CONFIGURED:{role}")
        provider = self.providers.get((provider_id, model))
        if provider is None:
            raise RuntimeError(
                f"MODEL_PROVIDER_NOT_CONFIGURED:{provider_id}:{model}:role={role}"
            )
        return provider

    def _provider_for_id(self, provider_id: str):
        for (pid, _model), provider in self.providers.items():
            if pid == provider_id:
                return provider
        return None

    def generate_json(self, task: str, inputs: dict, schema_hint: dict, instructions: str = "") -> dict:
        role = "WRITER" if task.startswith("writer.") else {
            "research.structure_problem": "RESEARCH",
            "hypothesis.generate": "HYPOTHESIS_GENERATION",
            "hypothesis.analyze_user_hypothesis": "HYPOTHESIS_GENERATION",
            "idea_selection.review": "EVIDENCE_REASONING",
            "critic.evidence_reasoning": "EVIDENCE_REASONING",
            "planning.build_plan": "RESEARCH_PLAN_GENERATION",
            "planning.revise_from_review": "RESEARCH_PLAN_GENERATION",
            "planning.review_plan": "RESEARCH_PLAN_REVIEW",
            "experiment.generate_code": "EXPERIMENT_CODE_GENERATION",
            "experiment.generate_bundle": "EXPERIMENT_CODE_GENERATION",
            "experiment.repair_bundle": "EXPERIMENT_CODE_GENERATION",
            "critic.review_result": "CRITIC",
            # Semantic review must be independent of report generation.  Reuse
            # the configured critic role rather than adding another workflow role.
            "reviewer.semantic": "CRITIC",
        }.get(task, "GENERAL_REASONING")
        provider = self._route(role)
        self._last_provider = provider
        return provider.generate_json(task, inputs, schema_hint, instructions)

    def generate_json_for_provider(self, provider_id: str, task: str, inputs: dict, schema_hint: dict, instructions: str = "") -> dict:
        provider = self._provider_for_id(provider_id)
        if provider is None:
            raise RuntimeError(f"MODEL_PROVIDER_NOT_CONFIGURED:{provider_id}")
        self._last_provider = provider
        return provider.generate_json(task, inputs, schema_hint, instructions)

    def _preflight_instance(self, provider, provider_id: str) -> dict:
        check = getattr(provider, "preflight", None)
        if callable(check):
            return check(provider_id)
        # Compatibility with custom providers: still issue the same minimal JSON request.
        provider.generate_json(
            "provider.preflight",
            {"purpose": "run_admission", "provider": provider_id},
            {"ready": "boolean"},
            "Return exactly one JSON object with a boolean ready field.",
        )
        return {"provider": provider_id, "structured": True}

    def preflight(self, provider_id: str) -> dict:
        provider = self._provider_for_id(provider_id)
        if provider is None:
            raise RuntimeError(f"MODEL_PROVIDER_NOT_CONFIGURED:{provider_id}")
        return self._preflight_instance(provider, provider_id)

    def preflight_model(self, provider_id: str, model: str) -> dict:
        provider = self.providers.get((provider_id, model))
        if provider is None:
            raise RuntimeError(f"MODEL_PROVIDER_NOT_CONFIGURED:{provider_id}:{model}")
        return self._preflight_instance(provider, provider_id)

    def configured_provider_models(self) -> list[tuple[str, str]]:
        return sorted(self.providers.keys())

    def begin_run(self, run_id: str) -> None:
        for provider in self.providers.values():
            provider.begin_run(run_id)

    def end_run(self, run_id: str) -> None:
        for provider in self.providers.values():
            provider.end_run(run_id)

    def cancel_run(self, run_id: str) -> bool:
        return any(provider.cancel_run(run_id) for provider in self.providers.values())

    def consume_call_metadata(self) -> dict:
        provider = self._last_provider
        self._last_provider = None
        if provider is not None:
            metadata = provider.consume_call_metadata()
            for other in self.providers.values():
                if other is not provider:
                    other.consume_call_metadata()
            return metadata
        for provider in self.providers.values():
            metadata = provider.consume_call_metadata()
            if metadata:
                return metadata
        return {}
class DuplicateJSONKeyError(ValueError):
    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


def _duplicate_key_rejecting_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateJSONKeyError(str(key))
        result[key] = value
    return result


def _loads_model_json(raw: str, task: str):
    try:
        return json.loads(raw, object_pairs_hook=_duplicate_key_rejecting_object)
    except DuplicateJSONKeyError as exc:
        code = (
            "FIX_MAP_DUPLICATE_KEY"
            if task == "planning.revise_from_review"
            else "PLAN_REVIEW_DUPLICATE_KEY"
        )
        raise ValueError(f"{code}:{exc.key}") from exc
