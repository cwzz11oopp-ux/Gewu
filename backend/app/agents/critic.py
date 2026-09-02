from backend.app.providers.llm import LLMProvider


class CriticAgent:
    name = "Critic Skill"

    def __init__(self, llm_provider: LLMProvider) -> None:
        self.llm_provider = llm_provider

    def evidence_reasoning(
        self,
        hypothesis: dict,
        evidence: list[dict],
        *,
        evidence_audit: dict | None = None,
        evaluation: dict | None = None,
        instructions: str = "",
    ) -> dict:
        return self.llm_provider.generate_json(
            "critic.evidence_reasoning",
            {
                "hypothesis": hypothesis,
                "evidence": evidence,
                "evidence_audit": evidence_audit or {},
                "evaluation": evaluation or {},
            },
            {
                "decision": "GO|REVISE|TARGETED_RETRIEVAL|REJECT",
                "status": "verified|revised|evidence_insufficient|rejected",
                "selected": "object",
                "revised_hypothesis": {
                    "claim": "complete revised claim",
                    "verifiability": "complete test procedure",
                    "novelty_basis": ["complete evidence-grounded basis"],
                    "risks": ["remaining risk"],
                },
                "revision_reason": "string",
                "method_assessment": "string",
                "evidence_basis": ["object with statement, source title/url, and FACT|INFERENCE|ASSUMPTION"],
                "claim_evidence_map": [
                    {
                        "claim": "atomic claim or mechanism",
                        "evidence_id": "string from evidence_audit",
                        "stance": "support|contradict|context",
                        "relation": "DIRECT|INDIRECT|ANALOGY",
                        "strength": "high|medium|low",
                        "limitation": "string",
                    }
                ],
                "unsupported_claims": ["string"],
                "missing_claims": ["atomic motivation, mechanism, or gap claim that needs evidence"],
                "recommended_queries": ["candidate-specific academic search query"],
                "required_source_type": ["primary paper|benchmark paper|dataset documentation"],
                "why_needed": "why the missing evidence is required before experiment entry",
                "counter_evidence": ["object"],
                "required_evidence": ["string"],
                "support": ["object"],
                "warnings": ["string"],
            },
            instructions=instructions,
        )

    def review_result(
        self,
        hypothesis: dict,
        result: dict,
        *,
        plan: dict | None = None,
        analysis: dict | None = None,
        audit: dict | None = None,
        research_context: dict | None = None,
        instructions: str = "",
    ) -> dict:
        return self.llm_provider.generate_json(
            "critic.review_result",
            {
                "hypothesis": hypothesis,
                "plan": plan or {},
                "result": result,
                "analysis": analysis or result.get("analysis") or {},
                "audit": audit or result.get("audit") or {},
                **({"research_context": research_context} if research_context is not None else {}),
            },
            {
                "verdict": "supported|partial|failed",
                "decision": (
                    "REPORT|REVISE|PIVOT. Use REPORT when the current path should "
                    "stop and be reported, including an honest negative result. "
                    "Use REVISE or PIVOT only when a concrete legal follow-up "
                    "experiment exists within the frozen research constraints."
                ),
                "result_analysis": {
                    "measured_facts": ["string"],
                    "failed_criteria": ["string"],
                    "improved_metrics": ["string"],
                    "degraded_metrics": ["string"],
                    "uncertainties": ["string"],
                    "methodological_issues": ["string"],
                    "causal_hypotheses": ["string"],
                    "knowledge_gaps": ["string"],
                },
                "literature_queries": [{
                    "question": "user-facing scientific question",
                    "query": "concise English academic search query",
                    "trigger_metric": "metric name",
                    "observed_value": "number|string",
                    "reason": "why external evidence is needed",
                }],
                "supported_claims": ["string"],
                "unsupported_claims": ["string"],
                "revisions": ["string"],
                "next_action": "string",
                "evidence_links": [{
                    "claim": "string",
                    "metric": "string",
                    "value": "number|string",
                    "source": "result|analysis|audit",
                }],
                "feedback": "string",
                "required_revision": "string",
                "overclaim_risks": ["string"],
            },
            instructions=instructions,
        )

    def scientific_result_analysis(
        self,
        hypothesis: dict,
        result: dict,
        *,
        plan: dict,
        evidence: list[dict],
        provider_id: str,
        instructions: str = "",
    ) -> dict:
        """A bounded, structured interpretation; providers never see each other's reasoning."""
        task = "scientific.primary_result_analysis" if provider_id == "qwen" else "scientific.independent_result_review"
        generate_for_provider = getattr(self.llm_provider, "generate_json_for_provider", None)
        if not callable(generate_for_provider):
            if provider_id != "qwen":
                raise RuntimeError("SECONDARY_REVIEW_UNAVAILABLE:PROVIDER_ROUTING_UNAVAILABLE")
            generate_for_provider = lambda _provider, name, inputs, schema, instructions="": self.llm_provider.generate_json(name, inputs, schema, instructions)
        return generate_for_provider(
            provider_id,
            task,
            {"hypothesis": hypothesis, "plan": plan, "validated_result": result, "literature_evidence": evidence},
            {"hypothesis_status": "SUPPORTED|CONTRADICTED|INCONCLUSIVE|REFINEMENT_REQUIRED", "supported_findings": ["string grounded in result or literature"], "contradicting_findings": ["string grounded in result or literature"], "alternative_explanations": ["string"], "confounders": ["string"], "evidence_gaps": ["string"], "interpretation": "string", "recommended_action": "string", "proposed_hypothesis": "object|null", "confidence": "number 0..1"},
            instructions=instructions,
        )

    def select_iteration_direction(
        self,
        hypothesis: dict,
        plan: dict,
        result: dict,
        feedback: dict,
        iteration_evidence: dict,
        *,
        instructions: str = "",
    ) -> dict:
        return self.llm_provider.generate_json(
            "critic.select_iteration_direction",
            {
                "hypothesis": hypothesis,
                "plan": plan,
                "result": result,
                "feedback": feedback,
                "iteration_evidence": iteration_evidence,
            },
            {
                "decision": (
                    "REPORT|REVISE|PIVOT. Use REPORT when no executable, "
                    "evidence-grounded follow-up should be run."
                ),
                "evidence_sufficiency": "SUFFICIENT|EVIDENCE_INSUFFICIENT",
                "evidence_assessment": [{
                    "statement": "string",
                    "type": "FACT|INFERENCE|ASSUMPTION|CONTRADICTION",
                    "evidence_id": "string or empty",
                    "limitation": "string",
                }],
                "optimization_candidates": [{
                    "name": "string",
                    "problem_addressed": "string",
                    "result_basis": ["string"],
                    "evidence_basis": ["string"],
                    "changed_variable": "string",
                    "fixed_controls": ["string"],
                    "target_metrics": ["string"],
                    "possible_regressions": ["string"],
                    "information_gain": "high|medium|low",
                    "expected_benefit": "high|medium|low",
                    "evidence_confidence": "high|medium|low",
                    "compute_cost": "string",
                    "scientific_risk": "high|medium|low",
                    "success_rule": "string",
                    "failure_rule": "string",
                    "stop_rule": "string",
                }],
                "selected_direction": {
                    "name": "string",
                    "problem_addressed": "specific unresolved issue or improvement opportunity",
                    "result_basis": ["measured observation motivating the experiment"],
                    "source_result_ids": ["exact result artifact ID from research_context.history"],
                    "changed_variable": "string",
                    "fixed_controls": ["string"],
                    "target_metrics": ["string"],
                    "success_rule": "string",
                    "failure_rule": "string",
                    "stop_rule": "string",
                },
                "proposed_hypothesis": {"claim": "New bounded claim when decision is PIVOT; otherwise empty string"},
                "selection_reason": "string",
                "next_action": "string",
            },
            instructions=instructions,
        )
