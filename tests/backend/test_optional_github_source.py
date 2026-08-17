from __future__ import annotations

import hashlib

from fastapi.testclient import TestClient

from backend.app.models.run import RunRecord
from backend.app.main import create_app
from backend.app.providers.experiment import MockExperimentProvider
from backend.app.providers.literature import MockLiteratureProvider
from backend.app.providers.llm import MockLLMProvider
from backend.app.storage.repository import Repository
from backend.app.workflow.engine import WorkflowEngine
from backend.app.workflow.github_source import GitHubSourceInspector
from backend.app.workflow.research_synthesis import (
    candidate_code_evidence_provenance_issues,
    normalize_candidate_code_evidence_provenance,
)


class FixtureGateway:
    def __init__(self, *, fail_read: bool = False) -> None:
        self.fail_read = fail_read
        self.calls: list[str] = []

    def get_json(self, url: str):
        self.calls.append(url)
        if "/commits/" in url:
            return {"sha": "abc123"}
        if "/git/trees/" in url:
            return {"tree": [
                {"type": "blob", "path": "README.md"},
                {"type": "blob", "path": "src/model.py"},
                {"type": "blob", "path": "config/train.yaml"},
                {"type": "blob", "path": "scripts/setup.sh"},
            ]}
        return {"default_branch": "main"}

    def get_text(self, url: str) -> str:
        self.calls.append(url)
        if self.fail_read:
            raise OSError("fixture read denied")
        if url.endswith("src/model.py"):
            return "class TinyNet:\n    def forward(self, x):\n        return x\n"
        if url.endswith("README.md"):
            return "# fixture\n"
        return "epochs: 3\n"


class CodeAwareMockLLM(MockLLMProvider):
    def generate_json(self, task, inputs, schema_hint, instructions=""):
        output = super().generate_json(task, inputs, schema_hint, instructions)
        if task == "hypothesis.generate":
            code_id = inputs["research_synthesis"]["code_evidence"][0]["code_evidence_id"]
            for candidate in output["candidates"]:
                candidate["source_code_evidence_ids"] = [code_id]
        return output


class CountingInspector:
    def __init__(self) -> None:
        self.calls = 0

    def inspect(self, _url):
        self.calls += 1
        raise AssertionError("a missing GitHub URL must not start source inspection")


def _engine(tmp_path, *, inspector, llm=None):
    repo = Repository(str(tmp_path))
    return repo, WorkflowEngine(
        repo,
        llm or MockLLMProvider(),
        MockLiteratureProvider(),
        MockExperimentProvider(),
        github_source_inspector=inspector,
    )


def _run_to_hypothesis(repo, engine, github_url=None):
    run = repo.create_run("Test optional source provenance", "fixture", github_repository_url=github_url)
    for step in ("problem_understanding", "knowledge_integration", "hypothesis_generation"):
        run = engine.run_step(run.id, step)
    return run


def test_optional_github_source_reads_actual_fixture_and_preserves_code_provenance(tmp_path):
    gateway = FixtureGateway()
    repo, engine = _engine(tmp_path, inspector=GitHubSourceInspector(gateway), llm=CodeAwareMockLLM())
    run = _run_to_hypothesis(repo, engine, "https://github.com/acme/sample")

    synthesis = [item.content for item in run.artifacts if item.type == "research_synthesis"][-1]
    code_artifact = [item.content for item in run.artifacts if item.type == "code_evidence"][-1]
    hypothesis = [item.content for item in run.artifacts if item.type == "hypothesis"][-1]
    evidence = synthesis["code_evidence"]
    assert len(evidence) == 2
    assert {item["source_file"] for item in evidence} == {"src/model.py"}
    assert {item["symbol"] for item in evidence} == {"TinyNet", "forward"}
    assert all(item["repository_commit"] == "abc123" for item in evidence)
    assert all(item["file_hash"] == hashlib.sha256(FixtureGateway().get_text("src/model.py").encode()).hexdigest() for item in evidence)
    assert code_artifact["items"] == evidence
    assert all(candidate["source_code_evidence_ids"] == [evidence[0]["code_evidence_id"]] for candidate in hypothesis["candidates"])
    assert not any("setup.sh" in url for url in gateway.calls)


def test_no_github_url_keeps_current_flow_and_does_not_inspect(tmp_path):
    inspector = CountingInspector()
    repo, engine = _engine(tmp_path, inspector=inspector)
    run = _run_to_hypothesis(repo, engine)
    hypothesis = [item.content for item in run.artifacts if item.type == "hypothesis"][-1]
    assert inspector.calls == 0
    assert not [item for item in run.artifacts if item.type in {"github_source", "code_evidence"}]
    assert all(candidate["source_code_evidence_ids"] == [] for candidate in hypothesis["candidates"])


def test_invalid_github_url_records_warning_but_research_continues(tmp_path):
    repo, engine = _engine(tmp_path, inspector=GitHubSourceInspector(FixtureGateway()))
    run = _run_to_hypothesis(repo, engine, "https://example.invalid/not-github")
    source = [item.content for item in run.artifacts if item.type == "github_source"][-1]
    assert source["github_source_status"] == "unavailable"
    assert source["warnings"] == ["GITHUB_SOURCE_INVALID_URL"]
    assert any(item.type == "hypothesis" for item in run.artifacts)


def test_read_failure_records_warning_but_research_continues(tmp_path):
    repo, engine = _engine(tmp_path, inspector=GitHubSourceInspector(FixtureGateway(fail_read=True)))
    run = _run_to_hypothesis(repo, engine, "https://github.com/acme/sample")
    source = [item.content for item in run.artifacts if item.type == "github_source"][-1]
    assert source["github_source_status"] == "unavailable"
    assert any(warning.startswith("GITHUB_SOURCE_FILE_READ_FAILED") for warning in source["warnings"])
    assert any(item.type == "hypothesis" for item in run.artifacts)


def test_legacy_checkpoint_without_github_field_still_validates():
    run = RunRecord.model_validate({"id": "run_legacy", "title": "legacy", "problem_input": "legacy problem"})
    assert run.github_repository_url is None


def test_create_run_api_persists_optional_github_url(tmp_path):
    client = TestClient(create_app(data_dir=str(tmp_path), env={
        "COMPETITION_MODE": "false", "LLM_PROVIDER": "mock",
        "EXPERIMENT_PROVIDER": "mock", "LITERATURE_PROVIDER": "mock",
    }))
    response = client.post("/api/runs", json={
        "title": "fixture", "problem_input": "problem",
        "github_repository_url": "https://github.com/acme/sample",
    })
    assert response.status_code == 200
    assert response.json()["github_repository_url"] == "https://github.com/acme/sample"


def test_fake_code_evidence_id_is_rejected_before_candidate_normalization():
    synthesis = {"code_evidence": [{"code_evidence_id": "CODE-real"}]}
    candidate = {"candidate_id": "CAND-1", "source_code_evidence_ids": ["CODE-invented"]}
    issues = candidate_code_evidence_provenance_issues([candidate], synthesis)
    assert issues == ["CANDIDATE_PROVENANCE_UNKNOWN_CODE_EVIDENCE:CAND-1:CODE-invented"]
    assert normalize_candidate_code_evidence_provenance(candidate, synthesis)["source_code_evidence_ids"] == []
