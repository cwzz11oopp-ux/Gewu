from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app.literature import SprintLiteratureService
from backend.app.main import create_app
from backend.app.models.gateway import LegacyQwenAdapter
from backend.app.models.v2_session import ResearchEventKind, ResearchSessionEvent
from backend.app.research import (
    BaselineProfile,
    BaselineReproductionStatus,
    BudgetState,
    DatasetIdentity,
    ExperimentProtocol,
    ExperimentRecord,
    ExperimentResultStatus,
    MetricDefinition,
    MetricDirection,
    ProblemProfile,
    ProtocolCompatibilityGate,
    SeedPolicy,
    TrainingBudget,
)
from backend.app.research.ideator import BranchConstructor
from backend.app.services.v2_sessions import ResearchSessionService
from backend.app.services.v2_critic import ScientificCritic
from backend.app.storage.v2 import V2Stores


class NoPapers:
    def search(self, query, limit):
        return []


class BranchProvider:
    fallback = False

    def __init__(self) -> None:
        self.calls = []

    def generate_json(self, task, inputs, schema_hint, instructions=""):
        self.calls.append((task, inputs, schema_hint, instructions))
        if task == "v2.critic.review_experiment":
            return {
                "supported_claims": ["Accuracy improved under the locked fixture protocol."],
                "unsupported_claims": ["The threshold mechanism has not been isolated."],
                "possible_mechanisms": ["Reduced threshold bias may explain the gain."],
                "alternative_explanations": ["The result may be fixture-specific."],
                "methodological_issues": ["Only two deterministic seeds were evaluated."],
                "open_information_gaps": ["Does removing threshold calibration erase the gain?"],
                "recommended_actions": ["RUN_ABLATION", "RUN_REPLICATION"],
            }
        assert task == "v2.ideator.construct_branches"
        common = {
            "mechanism": "A smaller error threshold reduces systematic false positives.",
            "expected_observation": "Accuracy rises under the locked protocol.",
            "falsification_condition": "Neither locked seed improves accuracy.",
            "minimal_experiment": "Change one threshold and run two locked seeds.",
            "closest_prior_work": ["context-paper"],
            "novelty_risk": "Threshold tuning is established; mechanism evidence is limited.",
            "information_gain": "high",
            "scientific_potential": "medium",
            "estimated_compute_minutes": 1,
            "risk": "low",
            "initially_runnable": True,
            "required_prior_evidence": [],
        }
        return {
            "proposals": [
                {
                    **common,
                    "research_gap": "The current decision threshold is not calibrated.",
                    "hypothesis": "A calibrated threshold improves held-out accuracy.",
                    "proposed_change": "Calibrate the threshold on training data only.",
                },
                {
                    **common,
                    "research_gap": "Prediction margin is not represented.",
                    "hypothesis": "A normalized margin improves robustness.",
                    "proposed_change": "Add a normalized prediction-margin feature.",
                },
                {
                    **common,
                    "research_gap": "Boundary sensitivity remains unmeasured.",
                    "hypothesis": "A robust training-only threshold improves accuracy.",
                    "proposed_change": "Estimate one threshold from training values.",
                },
            ]
        }


def scientific_inputs():
    dataset = DatasetIdentity(
        name="api-fixture", version="1", source="test", fingerprint="api-v1"
    )
    protocol = ExperimentProtocol(
        task="classification",
        dataset=dataset,
        split={"train": "train", "test": "test"},
        preprocessing={"scale": "unit"},
        metrics=[
            MetricDefinition(
                name="accuracy",
                direction=MetricDirection.MAXIMIZE,
                definition="correct / total",
                aggregation="mean",
            )
        ],
        training_budget=TrainingBudget(epochs=1),
        evaluation_protocol={"checkpoint": "last"},
        seed_policy=SeedPolicy(seeds=[1, 2], aggregation="mean", minimum_repetitions=2),
        training_controls={"optimizer": "none"},
    )
    problem = ProblemProfile(
        question="Can calibrated decision logic improve robust classification?",
        task=protocol.task,
        repository="fixture-repository",
        dataset=dataset,
        success_criteria=["audited accuracy improvement"],
    )
    baseline = BaselineProfile(
        repository=problem.repository,
        commit="base",
        task=problem.task,
        dataset=dataset,
        entrypoint="train.py",
        environment={"python": "test"},
        protocol=protocol,
        local_metrics={"accuracy": 0.5},
        seeds=[1, 2],
        reproduction_status=BaselineReproductionStatus.VALIDATED,
        validation_reason="fixture audit",
        audit_passed=True,
    )
    budget = BudgetState(
        experiment_limit=4, compute_minutes_limit=10, model_call_limit=4
    )
    return problem, baseline, budget


def test_versioned_api_exposes_durable_session_and_model_generated_frontier(tmp_path):
    provider = BranchProvider()
    gateway = LegacyQwenAdapter(provider)
    service = ResearchSessionService(
        V2Stores(str(tmp_path / "v2")),
        BranchConstructor(gateway),
        SprintLiteratureService(NoPapers()),
        model_ready=True,
        critic=ScientificCritic(gateway),
    )
    app = create_app(
        data_dir=str(tmp_path / "app"),
        env={"COMPETITION_MODE": "false", "LLM_PROVIDER": "mock", "LITERATURE_PROVIDER": "mock", "EXPERIMENT_PROVIDER": "mock"},
        v2_session_service_override=service,
    )
    problem, baseline, budget = scientific_inputs()
    client = TestClient(app)
    created = client.post(
        "/api/v2/research/sessions",
        json={
            "problem": problem.model_dump(mode="json"),
            "baseline": baseline.model_dump(mode="json"),
            "budget": budget.model_dump(mode="json"),
        },
    )
    assert created.status_code == 201
    session_id = created.json()["session_id"]

    started = client.post(f"/api/v2/research/sessions/{session_id}/start")
    assert started.status_code == 200
    assert started.json()["action"]["operator"] == "RUN_EXPERIMENT"
    frontier = client.get(f"/api/v2/research/sessions/{session_id}/frontier")
    assert len(frontier.json()["branches"]) == 3
    assert all(item["falsification_condition"] for item in frontier.json()["branches"])
    assert client.get(f"/api/v2/research/sessions/{session_id}/experiments").json() == []
    assert isinstance(client.get(f"/api/v2/research/sessions/{session_id}/evidence").json(), list)

    active_branch = started.json()["action"]["branch_id"]
    experiment = ExperimentRecord(
        experiment_id="api_exp_1",
        branch_id=active_branch,
        purpose="Test threshold mechanism",
        repository=problem.repository,
        base_commit="base",
        code_commit="variant",
        protocol=baseline.protocol,
        protocol_fingerprint=baseline.protocol.fingerprint(),
        config={"threshold": 0.0},
        seeds=[1, 2],
        metrics={"accuracy": 0.75},
        baseline_metrics={"accuracy": 0.5},
        comparison=ProtocolCompatibilityGate.evaluate(
            baseline.protocol, baseline.protocol, audit_passed=True
        ),
        audit_passed=True,
        environment={"python": "test"},
        result_status=ExperimentResultStatus.SUCCEEDED,
    )
    continued = client.post(
        f"/api/v2/research/sessions/{session_id}/continue",
        json={"experiment": experiment.model_dump(mode="json")},
    )
    assert continued.status_code == 200
    assert continued.json()["critique"]["recommended_actions"][0] == "RUN_ABLATION"
    assert continued.json()["action"]["operator"] == "RUN_ABLATION"
    critic_call = next(call for call in provider.calls if call[0] == "v2.critic.review_experiment")
    critic_prompt = critic_call[1]["messages"][0]["content"]
    assert "Simplified Chinese" in critic_prompt
    assert "Keep JSON keys" in critic_prompt
    assert len(client.get(f"/api/v2/research/sessions/{session_id}/experiments").json()) == 1
    assert any(
        "removing threshold calibration" in gap.lower()
        for gap in continued.json()["state"]["open_questions"]
    )
    summary = client.get(f"/api/v2/research/sessions/{session_id}/summary").json()
    assert summary["best_result"]["value"] == 0.75
    assert summary["current_decision"]["operator"] == "RUN_ABLATION"
    findings = client.get(f"/api/v2/research/sessions/{session_id}/findings").json()
    assert findings["recommended_actions"][0] == "RUN_ABLATION"
    events = client.get(f"/api/v2/research/sessions/{session_id}/events").json()
    assert [event["kind"] for event in events] == [
        "SESSION_CREATED",
        "BRANCH_GATE",
        "ACTION_SELECTED",
        "EXPERIMENT_RECORDED",
        "CRITIQUE_RECORDED",
        "ACTION_SELECTED",
    ]

    service.stores.events.append(
        ResearchSessionEvent(
            session_id=session_id,
            kind=ResearchEventKind.PARAMETER_SWEEP_RECORDED,
            iteration=2,
            payload={
                "points": [
                    {
                        "experiment_id": "api_sweep_01",
                        "parameter_value": 0.1,
                        "metric_value": 0.75,
                    }
                ],
                "stable_improvement_intervals": [],
            },
        )
    )
    service.stores.events.append(
        ResearchSessionEvent(
            session_id=session_id,
            kind=ResearchEventKind.REPORT_EXPORTED,
            iteration=2,
            payload={
                "docx": "report.docx",
                "final_conclusion": {"supported": ["Locked accuracy improved."]},
            },
        )
    )
    service.stores.events.append(
        ResearchSessionEvent(
            session_id=session_id,
            kind=ResearchEventKind.CLAIM_GRAPH_UPDATED,
            iteration=2,
            payload={
                "claims": [
                    {
                        "id": "claim_api",
                        "statement": "Locked accuracy improved.",
                        "status": "SUPPORTED",
                        "evidence_strength": "strong",
                    }
                ],
                "audit": {"exportable": True},
            },
        )
    )
    claims = client.get(f"/api/v2/research/sessions/{session_id}/claims").json()
    assert claims["claims"][0]["status"] == "SUPPORTED"
    sweep = client.get(f"/api/v2/research/sessions/{session_id}/parameter-sweep").json()
    assert sweep["points"][0]["parameter_value"] == 0.1
    trajectory = client.get(f"/api/v2/research/sessions/{session_id}/trajectory").json()
    stages = [item["stage"] for item in trajectory]
    assert stages[0] == "Research Question"
    assert {
        "Branch Proposal",
        "Controller Decision",
        "Experiment",
        "Result",
        "Critic",
        "Follow-up Action",
        "Evidence Update",
        "Final Conclusion",
    } <= set(stages)

    stopped = client.post(
        f"/api/v2/research/sessions/{session_id}/stop", json={"reason": "checkpoint"}
    )
    assert stopped.json()["stopped"] is True
    assert client.get(f"/api/v2/research/sessions/{session_id}/state").json()["stop_reason"] == "checkpoint"


def test_start_reports_missing_qwen_instead_of_fabricating_branches(tmp_path):
    problem, baseline, budget = scientific_inputs()
    app = create_app(
        data_dir=str(tmp_path),
        env={
            "COMPETITION_MODE": "false",
            "LLM_PROVIDER": "qwen",
            "QWEN_API_KEY": "",
            "LITERATURE_PROVIDER": "mock",
            "EXPERIMENT_PROVIDER": "mock",
        },
    )
    client = TestClient(app)
    session_id = client.post(
        "/api/v2/research/sessions",
        json={
            "problem": problem.model_dump(mode="json"),
            "baseline": baseline.model_dump(mode="json"),
            "budget": budget.model_dump(mode="json"),
        },
    ).json()["session_id"]
    response = client.post(f"/api/v2/research/sessions/{session_id}/start")
    assert response.status_code == 503
    assert response.json()["detail"] == "QWEN_API_KEY_MISSING"


def test_session_continues_from_baseline_reproduction_into_model_ideation(tmp_path):
    gateway = LegacyQwenAdapter(BranchProvider())
    service = ResearchSessionService(
        V2Stores(str(tmp_path / "v2")),
        BranchConstructor(gateway),
        SprintLiteratureService(NoPapers()),
        model_ready=True,
        critic=ScientificCritic(gateway),
    )
    app = create_app(
        data_dir=str(tmp_path / "app"),
        env={"COMPETITION_MODE": "false", "LLM_PROVIDER": "mock", "LITERATURE_PROVIDER": "mock", "EXPERIMENT_PROVIDER": "mock"},
        v2_session_service_override=service,
    )
    problem, baseline, budget = scientific_inputs()
    client = TestClient(app)
    session_id = client.post(
        "/api/v2/research/sessions",
        json={
            "problem": problem.model_dump(mode="json"),
            "budget": budget.model_dump(mode="json"),
        },
    ).json()["session_id"]
    first = client.post(f"/api/v2/research/sessions/{session_id}/start")
    assert first.json()["action"]["operator"] == "REPRODUCE_BASELINE"
    second = client.post(
        f"/api/v2/research/sessions/{session_id}/continue",
        json={"baseline": baseline.model_dump(mode="json")},
    )
    assert second.status_code == 200
    assert second.json()["action"]["operator"] == "RUN_EXPERIMENT"
    assert len(second.json()["state"]["frontier"]["branches"]) == 3
