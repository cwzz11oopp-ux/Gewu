from backend.app.providers.llm import LLMProvider
from backend.app.workflow.plan_contract import authoritative_plan_contract


_PLAN_SCHEMA = {
    "objective": "字符串：用中文描述实验目标或待验证主张",
    "hypotheses": ["字符串：用中文陈述待验证假设"],
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
}


_DATASET_CONTRACT = (
    "dataset_options lists every dataset the experiment runtime can provide, with an "
    "availability status (cached: already on disk; downloadable: provisioned before the run; "
    "missing: not available in local mode) and a data card describing the exact input shape, "
    "class count, split sizes, and normalization statistics. Choose plan.dataset.name from the "
    "options whose status is cached or downloadable and design the experiment around that data "
    "card, or explicitly design a synthetic-data experiment. Never pick a dataset whose status "
    "is missing and never invent a dataset outside the options."
)

_PLAN_REVIEW_SCHEMA = {
    "verdict": "ACCEPT | REVISE | REJECT",
    "issues": [{"type": "string", "description": "string", "reason": "string", "affected_plan_section": "string"}],
    "required_changes": ["string"],
    "suggested_fixes": [{"problem": "string", "recommended_fix": "string", "alternative_fix": "string", "reason": "string"}],
    "revised_plan_guidance": ["string"],
    "experiment_feasibility": "FEASIBLE | FEASIBLE_AFTER_REVISION | NOT_FEASIBLE",
}


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
    ) -> dict:
        return self.llm_provider.generate_json(
            "planning.build_plan",
            {
                "active_hypothesis": hypothesis,
                "dataset_options": dataset_options or [],
                "plan_context": plan_context or {},
            },
            _PLAN_SCHEMA,
            instructions=self._with_plan_contract(instructions, dataset_options),
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
    ) -> dict:
        return self.llm_provider.generate_json(
            "planning.refine_plan",
            {
                "selection": selection,
                "current_plan": current_plan,
                "experiment_result": experiment_result,
                "feedback": feedback,
                "dataset_options": dataset_options or [],
                "plan_context": plan_context or {},
            },
            _PLAN_SCHEMA,
            instructions=self._with_plan_contract(instructions, dataset_options),
        )

    def review_plan(self, context: dict, *, instructions: str = "") -> dict:
        return self.llm_provider.generate_json(
            "planning.review_plan", context, _PLAN_REVIEW_SCHEMA,
            instructions=(self._with_plan_contract(instructions, None) + "\nReview only. Do not select a hypothesis, write scientific state, "
                          "produce ExperimentResult, or bypass the Supervisor final gate. Identify where, why, "
                          "and how to fix each problem; preserve the selected hypothesis when feasible."),
        )

    def revise_from_review(self, context: dict, *, instructions: str = "") -> dict:
        return self.llm_provider.generate_json(
            "planning.revise_from_review", context, _PLAN_SCHEMA,
            instructions=(self._with_plan_contract(instructions, None) + "\nReturn the complete revised Research Plan, not an explanation. "
                          "Apply issues, required_changes, suggested_fixes, and revised_plan_guidance while "
                          "preserving the selected hypothesis's scientific intent whenever feasible."),
        )

    @staticmethod
    def _with_plan_contract(instructions: str, dataset_options: list[dict] | None) -> str:
        contract = "Authoritative Plan Contract (the generator and reviewer use this exact vocabulary):\n" + "\n".join(
            f"- {name}: {description}" for name, description in authoritative_plan_contract().items()
        )
        dataset_contract = _DATASET_CONTRACT if dataset_options else ""
        return "\n\n".join(part for part in (instructions, contract, dataset_contract) if part)
