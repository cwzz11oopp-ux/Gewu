from copy import deepcopy

from backend.app.providers.llm import LLMProvider
from backend.app.workflow.plan_contract import authoritative_plan_contract


PLAN_REVIEW_PROMPT_SCHEMA_VERSION = 3
PLAN_REVIEW_FIXED_INSTRUCTIONS = (
    "Review only scientific-plan feasibility. Do not select a hypothesis, write scientific "
    "state, produce ExperimentResult, or demand code-level implementation details. A BLOCKER "
    "means that without changing this Plan the experiment cannot scientifically answer the "
    "frozen research question, has a deterministic scientific/design or mathematical error "
    "that invalidates its result, or violates a frozen user constraint/scientific contract. "
    "Everything else is WARNING or SUGGESTION: implementation confirmation, desirable "
    "tightening, Loader checks, code decisions, training-stage decisions, and runtime-verifiable "
    "concerns never block. In particular, tensor axes/dtypes/shapes, loader semantics and "
    "MAT/HDF5/CSV mappings, feature or FFT/window implementation, training code, APIs, paths, "
    "dependencies, output formats, runtime behavior, and Experiment Bundle concerns belong to "
    "the Experiment Validator, Loader Validator, Harness, or bounded repair loop. Follow the "
    "frozen review policy and return stable structured issue IDs. On revision rounds, first mark "
    "every prior OPEN blocker fixed or not fixed and check CLOSED issues only for policy-authorized "
    "regression or new evidence. A CLOSED finding must include resolution and evidence_artifact_ids "
    "pointing to the current candidate; required_fix may be null after closure. A new later-round "
    "BLOCKER requires artifact-backed regression or new_evidence. closed_issue_ids is informational "
    "only. Warnings and suggestions never block; only the governance ledger determines ACCEPT or "
    "REVISE. All contract_fields must use the frozen canonical Plan Contract field registry."
)
PLAN_REVISION_FIXED_INSTRUCTIONS = (
    "Return the complete revised Research Plan, not an explanation. Use the current "
    "candidate as the base and patch only fields required by validated OPEN blockers. "
    "Preserve the frozen problem anchor and CLOSED ledger. fix_map is only a mapping "
    "from each blocker ID to a non-empty list of exact frozen canonical top-level Plan "
    "Contract fields actually changed for that blocker. Do not use nested paths, text, "
    "evidence, values, or metadata in fix_map; it is not a complete plan diff manifest."
)


_PLAN_SCHEMA = {
    "objective": "字符串：用中文描述实验目标或待验证主张",
    "hypotheses": ["字符串：用中文陈述待验证假设"],
    "primary_claim": "字符串：当前实验直接检验的主张",
    "original_question_link": "字符串：主张如何回答、收窄或部分回答原始问题",
    "secondary_endpoints": ["字符串：保留原问题解释所需的最小次要终点或控制"],
    "method": {
        "name": "字符串：待验证方法名称",
        "mechanism": "字符串：方法为何可能影响目标指标",
        "components": ["字符串：实现中必须保留的方法组件"],
    },
    "dataset": {
        "name": "字符串：数据集名称",
        "split": "字符串：数据划分",
        "source": "字符串：数据来源",
        "preprocessing": ["字符串：预处理步骤"],
    },
    "comparisons": [{
        "baseline": "字符串：基线方案",
        "variant": "字符串：变量或实验方案",
        "controls": ["字符串：控制条件"],
    }],
    "evaluations": [{
        "metric": "字符串：评估指标",
        "direction": "字符串：指标方向",
        "method": "字符串：统计或判定方法",
    }],
    "procedure": {"steps": ["字符串：执行步骤"], "repetitions": "整数：重复次数"},
    "parameters": {"参数名称": "固定值或候选值"},
    "seeds": ["整数：随机种子"],
    "statistical_summary": {
        "aggregation": "字符串：如 mean/std 或置信区间",
        "significance_test": "字符串：统计检验；不适用时说明原因",
    },
    "success_criteria": ["字符串：指标满足何条件时支持主张"],
    "failure_criteria": ["字符串：何种结果反驳或限制主张"],
    "expected_artifacts": ["字符串：结果、日志、模型或图表"],
    "stop_conditions": ["字符串：提前停止或阻断条件"],
    "primary_experiment": {"name": "字符串", "purpose": "字符串"},
    "optional_ablations": [{
        "name": "字符串",
        "changed_variables": ["字符串"],
        "fixed_controls": ["字符串"],
        "metric": "字符串",
        "seed_policy": "字符串",
        "positive_interpretation": "字符串",
        "negative_interpretation": "字符串",
        "compute_estimate": "字符串",
        "priority": "high|medium|low",
    }],
    "traceability": [{
        "claim": "字符串：待验证主张",
        "mechanism": "字符串：对应机制",
        "metric": "字符串：对应指标",
        "decision_rule": "字符串：支持或反驳规则",
    }],
    "resources": {"gpu": "字符串：计算资源", "time": "字符串：时间约束"},
    "risks": ["字符串：风险、前置条件或缓解措施"],
    "additional_sections": {"自定义设计项": "字符串：无法归类的补充设计"},
    "diagnosis": {"status": "ready|revise|blocked", "hypothesis_falsified": "bool", "experiment_invalid": "bool", "hypothesis_underspecified": "bool"},
    "revised_hypothesis": {"claim": "字符串", "preserves_user_claim": "bool"},
    "mechanism_and_evidence": {"mechanism": "字符串", "evidence": ["字符串"], "limitations": ["字符串"]},
    "boundary_conditions": ["字符串：结论适用边界"],
    "alignment_contract": [{"claim": "字符串", "dataset": "字符串", "split": "字符串", "controls": ["字符串"], "metric": "字符串", "decision_rule": "字符串"}],
    "baseline_and_controls": {"treatment": "字符串", "control": "字符串", "fixed_controls": ["字符串"], "capacity_control_strategy": "字符串"},
    "feasibility_risks": [{"risk": "字符串", "mitigation": "字符串"}],
    "staged_gates": [{"name": "static|overfit|smoke|pilot|formal", "pass_criteria": ["字符串"], "fail_criteria": ["字符串"]}],
    "formal_experiment_entry_conditions": ["字符串"],
    "positive_negative_inconclusive_rules": {"positive": ["字符串"], "negative": ["字符串"], "inconclusive": ["字符串"]},
    "remaining_unknowns": ["字符串"],
    "capacity_confounder": {"confounder": "字符串", "control_strategy": "字符串", "justification": "字符串", "claim_boundary": "字符串"},
    "local_dataset_loader_verification": {"procedure": "字符串", "expected_shape": "字符串", "expected_labels": "字符串", "failure_policy": "字符串"},
    "fix_map": {"BLOCKER-ID": ["canonical Plan Contract field name"]},
}


_DATASET_CONTRACT = (
    "dataset_options lists every dataset the experiment runtime can provide, with an "
    "availability status (cached: already on disk; downloadable: provisioned before the run; "
    "missing: not available in local mode) and a data card describing the exact input shape, "
    "class count, split sizes, and normalization statistics. Choose plan.dataset.name from the "
    "options whose status is cached or downloadable and design the experiment around that data "
    "card, or explicitly design a synthetic-data experiment. Never pick a dataset whose status "
    "is missing and never invent a dataset outside the options. For a bound local dataset, "
    "observed_structure is a read-only inspection of real files: use only its actual keys, "
    "shapes, dtypes, and columns; do not infer meanings it does not state."
)

_PLAN_REVIEW_SCHEMA = {
    "verdict": "ACCEPT | REVISE | REJECT",
    "issues": [{
        "issue_id": "string stable across rounds",
        "blocker_class": "string from frozen policy or null",
        "severity": "BLOCKER | WARNING | SUGGESTION",
        "title": "string",
        "contract_fields": ["string"],
        "evidence": ["concrete current Plan/Run evidence"],
        "reason": "string",
        "required_fix": "string required for BLOCKER",
        "resolution": "string required when status=CLOSED",
        "status": "OPEN | CLOSED | REOPENED | DEFERRED | REJECTED",
        "introduced_round": "integer",
        "last_checked_round": "integer",
        "reopen_basis": "regression or null",
        "new_blocker_basis": "regression | new_evidence | null",
        "evidence_artifact_ids": ["artifact id used for new_evidence chronology"],
    }],
    "closed_issue_ids": ["informational only; never changes ledger state"],
    "reopened_issue_ids": ["stable issue id reopened by regression"],
    "required_changes": ["string"],
    "suggested_fixes": [{"problem": "string", "recommended_fix": "string", "alternative_fix": "string", "reason": "string"}],
    "revised_plan_guidance": ["string"],
    "experiment_feasibility": "FEASIBLE | FEASIBLE_AFTER_REVISION | NOT_FEASIBLE",
}


def plan_review_schema_snapshot() -> dict:
    return deepcopy(_PLAN_REVIEW_SCHEMA)


def plan_revision_schema_snapshot() -> dict:
    return deepcopy(_PLAN_SCHEMA)


def build_plan_review_runtime_contract(
    instructions: str,
    contract: dict[str, str],
    fixed_instructions: str = PLAN_REVIEW_FIXED_INSTRUCTIONS,
) -> str:
    return _join_runtime_contract(instructions, contract, fixed_instructions)


def build_plan_revision_runtime_contract(
    instructions: str,
    contract: dict[str, str],
    fixed_instructions: str = PLAN_REVISION_FIXED_INSTRUCTIONS,
) -> str:
    return _join_runtime_contract(instructions, contract, fixed_instructions)


def _join_runtime_contract(
    instructions: str, contract: dict[str, str], fixed_instructions: str
) -> str:
    contract_text = "Authoritative Plan Contract (canonical field IDs):\n" + "\n".join(
        f"- {name}: {description}" for name, description in contract.items()
    )
    return "\n\n".join(
        part for part in (instructions, contract_text, fixed_instructions) if part
    )


class PlanningAgent:
    name = "Planning Skill"

    def __init__(self, llm_provider: LLMProvider) -> None:
        self.llm_provider = llm_provider

    def build_plan(
        self,
        hypothesis: dict,
        *,
        instructions: str = "",
        dataset_options: list[dict] | None = None,
        plan_context: dict | None = None,
        authoritative_contract_snapshot: dict[str, str] | None = None,
    ) -> dict:
        return self.llm_provider.generate_json(
            "planning.build_plan",
            {
                "active_hypothesis": hypothesis,
                "dataset_options": dataset_options or [],
                "observed_structure": self._observed_structure(dataset_options),
                "plan_context": plan_context or {},
            },
            _PLAN_SCHEMA,
            instructions=self._with_plan_contract(
                instructions,
                dataset_options,
                authoritative_contract_snapshot=authoritative_contract_snapshot,
            ),
        )

    def refine_plan(
        self,
        selection: dict,
        current_plan: dict,
        experiment_result: dict,
        feedback: dict,
        *,
        instructions: str = "",
        dataset_options: list[dict] | None = None,
        plan_context: dict | None = None,
        authoritative_contract_snapshot: dict[str, str] | None = None,
    ) -> dict:
        return self.llm_provider.generate_json(
            "planning.refine_plan",
            {
                "selection": selection,
                "current_plan": current_plan,
                "experiment_result": experiment_result,
                "feedback": feedback,
                "dataset_options": dataset_options or [],
                "observed_structure": self._observed_structure(dataset_options),
                "plan_context": plan_context or {},
            },
            _PLAN_SCHEMA,
            instructions=self._with_plan_contract(
                instructions,
                dataset_options,
                authoritative_contract_snapshot=authoritative_contract_snapshot,
            ),
        )

    def review_plan(
        self,
        context: dict,
        *,
        instructions: str = "",
        runtime_contract_snapshot: str | None = None,
        schema_snapshot: dict | None = None,
    ) -> dict:
        return self.llm_provider.generate_json(
            "planning.review_plan",
            context,
            deepcopy(schema_snapshot) if schema_snapshot is not None else plan_review_schema_snapshot(),
            instructions=(
                runtime_contract_snapshot
                if runtime_contract_snapshot is not None
                else build_plan_review_runtime_contract(
                    instructions, authoritative_plan_contract()
                )
            ),
        )

    def revise_from_review(
        self,
        context: dict,
        *,
        instructions: str = "",
        runtime_contract_snapshot: str | None = None,
        schema_snapshot: dict | None = None,
    ) -> dict:
        return self.llm_provider.generate_json(
            "planning.revise_from_review",
            context,
            deepcopy(schema_snapshot) if schema_snapshot is not None else plan_revision_schema_snapshot(),
            instructions=(
                runtime_contract_snapshot
                if runtime_contract_snapshot is not None
                else build_plan_revision_runtime_contract(
                    instructions, authoritative_plan_contract()
                )
            ),
        )

    @staticmethod
    def _with_plan_contract(
        instructions: str,
        dataset_options: list[dict] | None,
        *,
        authoritative_contract_snapshot: dict[str, str] | None = None,
    ) -> str:
        contract_fields = (
            authoritative_contract_snapshot
            if authoritative_contract_snapshot is not None
            else authoritative_plan_contract()
        )
        contract = "Authoritative Plan Contract (the generator and reviewer use this exact vocabulary):\n" + "\n".join(
            f"- {name}: {description}" for name, description in contract_fields.items()
        )
        dataset_contract = _DATASET_CONTRACT if dataset_options else ""
        return "\n\n".join(part for part in (instructions, contract, dataset_contract) if part)

    @staticmethod
    def _observed_structure(dataset_options: list[dict] | None) -> list[dict]:
        return [
            {
                "contract_id": str(option.get("contract_id") or ""),
                "observed_structure": deepcopy(
                    ((option.get("card") or {}).get("observed_structure") or []
                )),
            }
            for option in dataset_options or []
            if ((option.get("card") or {}).get("observed_structure"))
        ]
