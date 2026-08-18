from backend.app.providers.experiment import MockExperimentProvider
from backend.app.providers.literature import MockLiteratureProvider
from backend.app.providers.llm import MockLLMProvider, ModelRoleRouter
from backend.app.storage.repository import Repository
from backend.app.workflow.engine import WorkflowEngine
from backend.app.main import create_app
from fastapi.testclient import TestClient


class ReviewingLLM(MockLLMProvider):
    fallback = False
    mode = "fixture"

    def __init__(self, reviews):
        self.reviews = list(reviews)
        self.calls = []
        self.revision_count = 0

    def generate_json(self, task, inputs, schema_hint, instructions=""):
        self.calls.append((task, inputs))
        if task == "planning.review_plan":
            result = self.reviews.pop(0)
            if result["verdict"] == "ACCEPT" and inputs.get("previous_issue_ledger"):
                prior = dict(inputs["previous_issue_ledger"][0])
                prior.update(
                    status="CLOSED",
                    resolution="The revised candidate defines the comparator.",
                    evidence=["The current candidate contains the required comparator."],
                    evidence_artifact_ids=[inputs["current_candidate_plan_id"]],
                )
                result = {**result, "issues": [prior]}
            return result
        if task == "planning.revise_from_review":
            self.revision_count += 1
            return dict(
                inputs["current_plan"],
                additional_sections={"deepseek_revision": f"applied-{self.revision_count}"},
                fix_map={"PRI-control": ["additional_sections"]},
            )
        return super().generate_json(task, inputs, schema_hint, instructions)


def _review(verdict="ACCEPT"):
    return {
        "verdict": verdict,
        "issues": [] if verdict == "ACCEPT" else [{
            "issue_id": "PRI-control",
            "blocker_class": "MISSING_EXECUTABLE_COMPARATOR",
            "severity": "BLOCKER",
            "title": "Comparator is not executable",
            "contract_fields": ["additional_sections"],
            "evidence": ["The current candidate does not define the required comparator."],
            "reason": "The planned comparison cannot identify the intervention.",
            "required_fix": "Define one executable comparator.",
            "status": "OPEN",
        }],
        "closed_issue_ids": ["PRI-control"] if verdict == "ACCEPT" else [],
        "required_changes": [] if verdict == "ACCEPT" else ["Use residual blocks or remove DropPath"],
        "suggested_fixes": [] if verdict == "ACCEPT" else [{"problem": "plain CNN + DropPath", "recommended_fix": "Use residual blocks", "alternative_fix": "Remove DropPath", "reason": "Preserves the regularization hypothesis"}],
        "revised_plan_guidance": [] if verdict == "ACCEPT" else ["Return a complete executable plan"],
        "experiment_feasibility": "FEASIBLE" if verdict == "ACCEPT" else "FEASIBLE_AFTER_REVISION",
    }


def _ready_engine(tmp_path, llm):
    repo = Repository(str(tmp_path))
    engine = WorkflowEngine(repo, llm, MockLiteratureProvider(), MockExperimentProvider())
    run = repo.create_run("Test compact CNN regularization", "Plan review")
    for step in ["problem_understanding", "knowledge_integration", "hypothesis_generation", "evidence_reasoning"]:
        run = engine.run_step(run.id, step)
    run = engine.select_hypothesis(run.id, 0)
    return engine, run


def test_plan_review_context_is_compact_and_complete(tmp_path):
    llm = ReviewingLLM([_review()])
    engine, run = _ready_engine(tmp_path, llm)
    engine.run_step(run.id, "research_plan")
    context = next(inputs for task, inputs in llm.calls if task == "planning.review_plan")
    assert {"research_problem_summary", "research_profile", "selected_hypothesis", "evidence_literature_compact_summary", "current_research_plan", "experiment_capability_constraints", "authoritative_plan_contract", "available_split_information"} <= set(context)
    assert {"dataset", "split_contract", "evaluations", "comparisons", "procedure"} <= set(
        context["authoritative_plan_contract"]
    )
    assert context["context_policy"] == "compact_summaries_only_no_unbounded_artifact_injection"


def test_revise_round_trips_qwen_deepseek_qwen(tmp_path):
    llm = ReviewingLLM([_review("REVISE"), _review("ACCEPT")])
    engine, run = _ready_engine(tmp_path, llm)
    run = engine.run_step(run.id, "research_plan")
    tasks = [task for task, _ in llm.calls]
    assert tasks.count("planning.review_plan") == 2
    assert "planning.revise_from_review" in tasks
    assert [artifact.type for artifact in run.artifacts].count("plan_review") == 2
    assert next(artifact for artifact in reversed(run.artifacts) if artifact.type == "plan").content["additional_sections"]["deepseek_revision"] == "applied-1"


def test_role_router_uses_configured_review_provider():
    class Provider:
        fallback = False
        def __init__(self, mode): self.mode, self.tasks = mode, []
        def generate_json(self, task, inputs, schema_hint, instructions=""): self.tasks.append(task); return {}
    from backend.app.config import Settings
    settings = Settings.from_env({})
    object.__setattr__(settings, "model_role_assignments", {"RESEARCH_PLAN_REVIEW": {"provider_id": "deepseek", "model": "deepseek-chat"}})
    qwen, deepseek = Provider("qwen"), Provider("deepseek")
    ModelRoleRouter(settings, qwen, deepseek).generate_json("planning.review_plan", {}, {})
    assert deepseek.tasks == ["planning.review_plan"]
    assert qwen.tasks == []


def test_provider_settings_mask_keys_and_persist_role_assignment(tmp_path):
    client = TestClient(create_app(data_dir=str(tmp_path), env={"COMPETITION_MODE": "false", "LLM_PROVIDER": "mock"}))
    response = client.put("/api/settings/providers/deepseek", json={
        "provider_id": "deepseek", "display_name": "DeepSeek", "base_url": "https://api.deepseek.com/v1",
        "api_key": "secret-value", "models": ["deepseek-chat"], "enabled": True,
    })
    assert response.status_code == 200
    assert response.json()["api_key"] == "********"
    listed = client.get("/api/settings/providers").json()["model_providers"]
    assert next(item for item in listed if item["provider_id"] == "deepseek")["api_key"] == "********"
    assert "secret-value" not in str(listed)
    assigned = client.put("/api/settings/model-roles/RESEARCH_PLAN_REVIEW", json={
        "provider_id": "deepseek", "model": "deepseek-chat",
    })
    assert assigned.status_code == 200
    assert client.get("/api/settings/model-roles").json()["RESEARCH_PLAN_REVIEW"]["provider_id"] == "deepseek"
