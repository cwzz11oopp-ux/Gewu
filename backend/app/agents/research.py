from backend.app.providers.llm import LLMProvider


class ResearchAgent:
    name = "Research Skill"

    def __init__(self, llm_provider: LLMProvider) -> None:
        self.llm_provider = llm_provider

    def structure_problem(self, problem_input: str, *, instructions: str = "") -> dict:
        return self.llm_provider.generate_json(
            "research.structure_problem",
            {"problem_input": problem_input},
            {
                "problem_statement": "Chinese string",
                "research_intent": {
                    "kind": "verification|optimization. Classify the user's goal, not the number of experiments. Use optimization for an open goal to improve a measurable outcome; verification for testing a specified claim. Default to verification if ambiguous.",
                    "goal_quote": "Exact short quote from problem_input supporting this classification; never invent a goal.",
                    "reason": "Brief explanation in the user's language",
                },
                "constraints": ["Chinese string"],
                "knowledge_gaps": ["Chinese string"],
                "literature_queries": [
                    {
                        "query": "concise English academic search query for arXiv/Crossref",
                        "intent": (
                            "BASELINE|DIRECT_METHOD|MECHANISM|BENCHMARK|EVALUATION|"
                            "CONTRADICTORY_EVIDENCE|RELATED_APPLICATION"
                        ),
                        "target_gap": "the specific evidence gap this query should resolve",
                    }
                ],
            },
            instructions=instructions,
        )
