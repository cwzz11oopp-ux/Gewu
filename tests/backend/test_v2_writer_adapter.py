from __future__ import annotations

from io import BytesIO

from docx import Document

from backend.app.experiment import ParameterSweepRunner, WorkspaceExperimentAdapter
from backend.app.research import (
    BaselineProfile,
    BaselineReproductionStatus,
    BudgetState,
    ClaimEvidenceGraph,
    ProblemProfile,
    ResearchFrontier,
)
from backend.app.services.v2_writer import V2WriterAdapter
from backend.app.state import ResearchState
from backend.app.workspace import WorktreeManager
from test_v2_parameter_sweep import make_repo, protocol, sweep_run
from test_v2_repository_research_e2e import branch


class ExistingWriterStub:
    def __init__(self) -> None:
        self.artifacts = []
        self.instructions = ""

    def build_report(self, artifacts, *, instructions=""):
        self.artifacts = artifacts
        self.instructions = instructions
        return {
            "Report Title": "Audited threshold calibration study",
            "Paper Title": "Audited threshold calibration study",
            "Paper Abstract": "This report is generated through the existing writer and fact-audit contract.",
            "Narrative Sections": [],
            "Report Status": {
                "complete": True,
                "grounded_in_artifacts": True,
                "real_experiment": True,
            },
        }


def test_v2_writer_adapter_exports_all_scientific_sections_from_records(tmp_path):
    root, base_commit = make_repo(tmp_path)
    active = protocol()
    sweep = ParameterSweepRunner(
        WorkspaceExperimentAdapter(WorktreeManager(root, tmp_path / "worktrees"))
    ).run(
        [
            sweep_run(
                root=root,
                base_commit=base_commit,
                active=active,
                threshold=value,
            )
            for value in [0.1, 0.2, 0.3, 0.4, 0.5]
        ]
    )
    candidate = branch("threshold_branch", 0.9)
    baseline = BaselineProfile(
        repository=str(root),
        commit=base_commit,
        task=active.task,
        dataset=active.dataset,
        entrypoint="train.py",
        environment={"python": "test", "device": "cpu"},
        protocol=active,
        reported_metrics={"accuracy": 0.8},
        local_metrics={"accuracy": 0.8},
        seeds=[11, 22],
        reproduction_status=BaselineReproductionStatus.VALIDATED,
        validation_reason="test baseline",
        audit_passed=True,
    )
    evidence = sweep.evidence_units(branch_id=candidate.id)
    state = ResearchState(
        problem=ProblemProfile(
            question="Does threshold calibration improve the locked fixture?",
            task=active.task,
            repository=str(root),
            dataset=active.dataset,
            success_criteria=["audited parameter response"],
        ),
        baseline=baseline,
        frontier=ResearchFrontier(branches=[candidate]),
        best_branch_id=candidate.id,
        budget=BudgetState(
            experiment_limit=10,
            compute_minutes_limit=10,
            model_call_limit=2,
        ),
        experiments=sweep.records,
        evidence=evidence,
        open_questions=["Does the result generalize beyond the locked fixture?"],
    )
    graph = ClaimEvidenceGraph.from_parameter_sweep(
        state, sweep, branch_id=candidate.id
    )
    writer = ExistingWriterStub()
    adapter = V2WriterAdapter(writer)
    report = adapter.build_report(state, graph, parameter_sweep=sweep)

    expected_sections = {
        "Research Problem",
        "Baseline",
        "Hypotheses / Research Frontier",
        "Selected Research Direction",
        "Method Modification",
        "Experiment Protocol",
        "Main Experiment",
        "Ablation",
        "Parameter Sweep",
        "Results",
        "Supported Claims",
        "Unsupported / Partial Claims",
        "Limitations",
        "Conclusion",
    }
    assert expected_sections.issubset(report)
    assert len(report["Results"]) == 5
    assert [row["value"] for row in report["Results"]] == [1.0, 1.0, 0.8, 0.8, 0.8]
    assert report["Report Status"]["numeric_source"] == "ExperimentRecord"
    assert report["Report Status"]["claim_graph_exportable"] is True
    assert {item.type for item in writer.artifacts} >= {
        "problem", "hypothesis", "plan", "experiment_result", "revision"
    }
    assert "Do not upgrade a finite parameter sweep" in writer.instructions
    assert "Simplified Chinese" in writer.instructions
    assert "Keep JSON keys" in writer.instructions

    html = adapter.html_bytes(
        report, session_id=state.session_id, title=report["Report Title"]
    ).decode("utf-8")
    assert "Parameter Sweep" in html
    assert "Unsupported / Partial Claims" in html
    document = Document(
        BytesIO(
            adapter.docx_bytes(
                report, session_id=state.session_id, title=report["Report Title"]
            )
        )
    )
    headings = [paragraph.text for paragraph in document.paragraphs]
    assert "Research Problem" in headings
    assert "Parameter Sweep" in headings
    assert "Conclusion" in headings
