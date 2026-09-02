from backend.app.models.provider import EvidenceCard
from backend.app.providers.llm import LLMProvider
from backend.app.workflow.hypothesis_contract import (
    MAX_HYPOTHESIS_CANDIDATES,
    MIN_HYPOTHESIS_CANDIDATES,
)


class IdeaAgent:
    name = "Idea Agent"

    def __init__(self, llm_provider: LLMProvider) -> None:
        self.llm_provider = llm_provider

    def generate(
        self,
        problem: dict,
        evidence: list[EvidenceCard | dict],
        *,
        research_synthesis: dict | None = None,
        instructions: str = "",
    ) -> dict:
        return self.llm_provider.generate_json(
            "hypothesis.generate",
            {
                "problem": problem,
                "verified_evidence": [
                    card.model_dump() if isinstance(card, EvidenceCard) else card
                    for card in evidence
                    if not isinstance(card, EvidenceCard) or card.exportable
                ],
                "research_synthesis": research_synthesis or {},
            },
            {
                "candidate_count_range": (
                    f"必须生成 {MIN_HYPOTHESIS_CANDIDATES} 到 "
                    f"{MAX_HYPOTHESIS_CANDIDATES} 个技术路线不同的候选假设"
                ),
                "language": "简体中文",
                "presentation_rules": [
                    "只输出候选假设，不要推荐排序。",
                    "不要排序。",
                    "不要评分，不要排名，不要推荐指数。",
                    "不要输出时间顺序、生成顺序或 active hypothesis 字段。",
                    "method 必须写出可复现实验的具体算法或网络名称，不能使用‘A 或 B’、‘轻量级分类器’等未定方案；"
                    "算法只是验证假设的干预手段，不得把未经实验的性能提升写成既成事实。",
                ],
                "candidates": [
                    {
                        "candidate_id": "CAND-001",
                        "claim": "string",
                        "motivation": "what established evidence makes this worth testing",
                        "research_gap": "the unresolved, evidence-backed gap",
                        "novel_inference": "the new inference; explicitly not a literature fact",
                        "experimental_prediction": "falsifiable outcome and comparison",
                        "method": "string: proposed method or intervention used to test the claim",
                        "mechanism": "string: expected causal or technical mechanism",
                        "component_claims": ["component-level claim"],
                        "required_evidence": ["motivation|mechanism|gap evidence needed"],
                        "source_gap_ids": ["GAP-... from research_synthesis only"],
                        "source_paper_ids": ["PAPER-... derived from selected source_gap_ids"],
                        "source_claim_ids": ["CLAIM-... derived from selected source_gap_ids"],
                        "source_future_work_ids": ["FW-... derived from selected source_gap_ids"],
                        "source_code_evidence_ids": ["CODE-... from research_synthesis code_evidence only; [] if unavailable"],
                        "reasoning_summary": "why the cited research gaps motivate this candidate",
                        "supporting_evidence_ids": ["EVID-... only after evidence validation; do not use arXiv IDs"],
                        "contradicting_evidence_ids": ["EVID-... only after evidence validation; do not use arXiv IDs"],
                        "targeted_queries": ["candidate-specific academic query"],
                        "unverified_citations": ["citation proposed from memory and requiring verification"],
                        "status": "candidate",
                        "evidence_basis": [{
                            "statement": "string: fact or inference supporting the hypothesis",
                            "source_title": "string",
                            "source_url": "string",
                            "evidence_type": "FACT|INFERENCE|ASSUMPTION",
                        }],
                        "verifiability": "string",
                        "novelty_basis": ["string"],
                        "risks": ["string"],
                    }
                ],
            },
            instructions=instructions,
        )

    def analyze_user_hypothesis(
        self,
        user_hypothesis: str,
        problem: dict,
        evidence: list[EvidenceCard],
        *,
        instructions: str = "",
    ) -> dict:
        return self.llm_provider.generate_json(
            "hypothesis.analyze_user_hypothesis",
            {
                "user_hypothesis": user_hypothesis,
                "problem": problem,
                "verified_evidence": [card.model_dump() for card in evidence if card.exportable],
            },
            {
                "claim": "string",
                "method": "string: proposed method or intervention",
                "mechanism": "string: why the method may work",
                "evidence_basis": [{
                    "statement": "string",
                    "source_title": "string",
                    "source_url": "string",
                    "evidence_type": "FACT|INFERENCE|ASSUMPTION",
                }],
                "verifiability": "string",
                "novelty_basis": ["string"],
                "risks": ["string"],
                "analysis": "string",
                "source": "user",
            },
            instructions=instructions,
        )
