from backend.app.providers.llm import LLMProvider


class IdeaSelectionAgent:
    name = "Idea Selection Agent"

    def __init__(self, llm_provider: LLMProvider) -> None:
        self.llm_provider = llm_provider

    def review(
        self,
        problem: dict,
        constraints: str,
        evidence: list[dict],
        candidates: list[dict],
        evidence_audit: dict | None = None,
        *,
        instructions: str = "",
    ) -> dict:
        return self.llm_provider.generate_json(
            "idea_selection.review",
            {
                "problem": problem,
                "constraints": constraints,
                "evidence": evidence,
                "candidates": candidates,
                "evidence_audit": evidence_audit or {},
            },
            {
                "evaluation_count": "exactly one evaluation per candidate",
                "evaluations": [
                    {
                        "candidate_index": "integer",
                        "idea_card": "object",
                        "evidence_ledger": ["object"],
                        "claim_evidence_map": [
                            {
                                "claim": "string",
                                "evidence_ids": ["string from evidence_audit.registry"],
                                "stance": "support|contradict|context",
                                "relation": "DIRECT|INDIRECT|ANALOGY",
                                "limitations": ["string"],
                            }
                        ],
                        "closest_prior_work": ["object"],
                        "gates": "object",
                        "scores": {
                            "novelty": "number 0..5",
                            "scientific_soundness": "number 0..5",
                            "impact": "number 0..5",
                            "testability": "number 0..5",
                            "execution_feasibility": "number 0..5",
                            "reproducibility_compliance": "number 0..5",
                        },
                        "mde": "object",
                        "risks": ["string"],
                        "decision": "GO|REVISE|PIVOT|STOP|EVIDENCE_INSUFFICIENT",
                        "confidence": "low|medium|high",
                        "unknowns": ["string"],
                    }
                ]
            },
            instructions=instructions,
        )
