from __future__ import annotations

from typing import Protocol

from backend.app.models.artifact import Artifact
from backend.app.research.claims import ClaimEvidenceGraph, ClaimStatus
from backend.app.research.experiment import ExperimentRecord
from backend.app.research.protocol import MetricDirection
from backend.app.experiment.parameter_sweep import ParameterResponseEvidence
from backend.app.reporting import build_report_docx, render_report_html
from backend.app.state.research import ResearchState


class ExistingWriter(Protocol):
    def build_report(self, artifacts: list[Artifact], *, instructions: str = "") -> dict: ...


class V2WriterAdapter:
    """Translate authoritative V2 state into the existing Writer and export stack."""

    def __init__(self, writer: ExistingWriter) -> None:
        self.writer = writer

    def build_report(
        self,
        state: ResearchState,
        claim_graph: ClaimEvidenceGraph,
        *,
        parameter_sweep: ParameterResponseEvidence | None = None,
        instructions: str = "",
    ) -> dict:
        graph_audit = claim_graph.audit()
        if not graph_audit.exportable:
            raise ValueError(
                "V2_REPORT_CLAIM_GRAPH_NOT_EXPORTABLE:"
                + ",".join(graph_audit.blocked_claim_ids)
            )
        artifacts = self._artifacts(state, claim_graph, parameter_sweep)
        report = self.writer.build_report(
            artifacts,
            instructions=(
                "Write report narrative and every human-readable prose value in Simplified Chinese by "
                "default. Keep JSON keys, schema identifiers, enum values, code, file paths, Git commits, "
                "model IDs, and metric keys unchanged. "
                "Use only the supplied ExperimentRecord-derived values. Distinguish supported, "
                "partially supported, and not supported claims. Do not upgrade a finite parameter "
                "sweep into uniqueness or external-validity evidence.\n" + instructions
            ).strip(),
        )
        sections = self._canonical_sections(state, claim_graph, parameter_sweep)
        report.update({section["title"]: section["content"] for section in sections})
        report["V2 Sections"] = sections
        status = dict(report.get("Report Status") or {})
        status.update(
            {
                "v2_adapter": True,
                "claim_graph_exportable": True,
                "supported_major_claims": graph_audit.verified_major_claims,
                "experiment_record_count": len(state.experiments),
                "parameter_sweep_included": parameter_sweep is not None,
                "numeric_source": "ExperimentRecord",
            }
        )
        report["Report Status"] = status
        self._audit_numeric_grounding(report, state, parameter_sweep)
        return report

    @staticmethod
    def html_bytes(report: dict, *, session_id: str, title: str) -> bytes:
        return render_report_html(
            report, run_id=session_id, run_title=title
        ).encode("utf-8")

    @staticmethod
    def docx_bytes(report: dict, *, session_id: str, title: str) -> bytes:
        return build_report_docx(report, run_id=session_id, run_title=title)

    @classmethod
    def _artifacts(
        cls,
        state: ResearchState,
        claim_graph: ClaimEvidenceGraph,
        sweep: ParameterResponseEvidence | None,
    ) -> list[Artifact]:
        run_id = state.session_id
        active = cls._active_branch(state)
        result_records = [cls._record_row(record) for record in state.experiments]
        best = cls._best_record(state.experiments)
        protocol = best.protocol if best else (state.baseline.protocol if state.baseline else None)
        artifacts = [
            cls._artifact(
                run_id,
                "problem",
                {
                    "problem_statement": state.problem.question,
                    "task": state.problem.task,
                    "repository": state.problem.repository,
                    "dataset": state.problem.dataset.model_dump(mode="json"),
                },
            ),
            cls._artifact(
                run_id,
                "hypothesis",
                {
                    "claim": active.hypothesis if active else "No active branch",
                    "mechanism": active.mechanism if active else "",
                    "falsification_condition": active.falsification_condition if active else "",
                    "claim_evidence_graph": claim_graph.model_dump(mode="json"),
                },
            ),
            cls._artifact(
                run_id,
                "plan",
                {
                    "objective": state.problem.question,
                    "dataset": state.problem.dataset.model_dump(mode="json"),
                    "method": {"components": ["baseline", "main experiment", "ablation", "parameter sweep"]},
                    "comparisons": ["variant versus locked baseline"],
                    "evaluations": [item.name for item in protocol.metrics] if protocol else [],
                    "parameters": {"sweep": [point.parameter_value for point in sweep.points]} if sweep else {},
                    "seeds": list(protocol.seed_policy.seeds) if protocol else [],
                    "procedure": {"steps": ["static validation", "smoke", "formal experiment", "fact audit"]},
                },
            ),
            cls._artifact(
                run_id,
                "experiment_result",
                {
                    "run_id": run_id,
                    "experiment_id": best.experiment_id if best else "",
                    "is_real_experiment": bool(best),
                    "provider": (best.environment.get("provider") or best.environment.get("device") or "local") if best else "",
                    "metrics": best.metrics if best else {},
                    "baseline_metrics": best.baseline_metrics if best else {},
                    "seeds": best.seeds if best else [],
                    "environment": best.environment if best else {},
                    "records": result_records,
                    "audit": {"passed": all(record.audit_passed for record in state.experiments)},
                },
            ),
            cls._artifact(
                run_id,
                "revision",
                {
                    "iteration": state.iteration,
                    "verdict": "scientifically_bounded",
                    "requires_follow_up": any(
                        claim.status != ClaimStatus.SUPPORTED for claim in claim_graph.claims
                    ),
                    "supported_claims": [
                        claim.statement
                        for claim in claim_graph.claims
                        if claim.status == ClaimStatus.SUPPORTED
                    ],
                    "unsupported_claims": [
                        claim.statement
                        for claim in claim_graph.claims
                        if claim.status != ClaimStatus.SUPPORTED
                    ],
                },
            ),
        ]
        literature = [
            item.provenance
            for item in state.evidence
            if item.source_type == "literature" and item.verified
        ]
        if literature:
            artifacts.append(cls._artifact(run_id, "evidence", {"references": literature}))
        return artifacts

    @classmethod
    def _canonical_sections(
        cls,
        state: ResearchState,
        graph: ClaimEvidenceGraph,
        sweep: ParameterResponseEvidence | None,
    ) -> list[dict]:
        active = cls._active_branch(state)
        main = next(
            (record for record in state.experiments if record.config.get("operator") == "RUN_EXPERIMENT"),
            state.experiments[0] if state.experiments else None,
        )
        ablation = next(
            (record for record in state.experiments if record.config.get("operator") == "RUN_ABLATION"),
            None,
        )
        protocol = main.protocol if main else (state.baseline.protocol if state.baseline else None)
        supported = [
            claim.model_dump(mode="json")
            for claim in graph.claims
            if claim.status == ClaimStatus.SUPPORTED
        ]
        bounded = [
            claim.model_dump(mode="json")
            for claim in graph.claims
            if claim.status != ClaimStatus.SUPPORTED
        ]
        sections = [
            {"title": "Research Problem", "content": state.problem.model_dump(mode="json")},
            {"title": "Baseline", "content": state.baseline.model_dump(mode="json") if state.baseline else {}},
            {"title": "Hypotheses / Research Frontier", "content": state.frontier.model_dump(mode="json")},
            {"title": "Selected Research Direction", "content": active.model_dump(mode="json") if active else {}},
            {
                "title": "Method Modification",
                "content": [
                    {
                        "experiment_id": record.experiment_id,
                        "changed_files": record.changed_files,
                        "code_commit": record.code_commit,
                        "config": record.config,
                    }
                    for record in state.experiments
                ],
            },
            {"title": "Experiment Protocol", "content": protocol.model_dump(mode="json") if protocol else {}},
            {"title": "Main Experiment", "content": cls._record_row(main) if main else {}},
            {"title": "Ablation", "content": cls._record_row(ablation) if ablation else {}},
            {
                "title": "Parameter Sweep",
                "content": sweep.model_dump(mode="json", exclude={"records"}) if sweep else {},
            },
            {"title": "Results", "content": [cls._record_row(record) for record in state.experiments]},
            {"title": "Supported Claims", "content": supported},
            {"title": "Unsupported / Partial Claims", "content": bounded},
            {
                "title": "Limitations",
                "content": [
                    *state.open_questions,
                    "Claims are bounded to the recorded repository, dataset, split, preprocessing, seeds, and evaluation protocol.",
                ],
            },
            {
                "title": "Conclusion",
                "content": {
                    "supported": [item["statement"] for item in supported],
                    "not_established": [item["statement"] for item in bounded],
                },
            },
        ]
        return sections

    @staticmethod
    def _artifact(run_id: str, artifact_type: str, content: dict) -> Artifact:
        return Artifact(
            run_id=run_id,
            type=artifact_type,
            version=1,
            title=artifact_type,
            content=content,
            source_step="v2_writer_adapter",
            created_by="V2WriterAdapter",
        )

    @staticmethod
    def _active_branch(state: ResearchState):
        branch_id = (
            state.best_branch_id
            or (state.current_action.branch_id if state.current_action else None)
            or (state.frontier.branches[0].id if state.frontier.branches else None)
        )
        return state.frontier.get(branch_id) if branch_id else None

    @staticmethod
    def _record_row(record: ExperimentRecord | None) -> dict:
        if record is None:
            return {}
        metric = record.protocol.metrics[0].name
        value = record.metrics.get(metric)
        baseline = record.baseline_metrics.get(metric)
        return {
            "experiment_id": record.experiment_id,
            "purpose": record.purpose,
            "config": record.config,
            "metric": metric,
            "value": value,
            "baseline": baseline,
            "baseline_delta": value - baseline if value is not None and baseline is not None else None,
            "protocol_fingerprint": record.protocol_fingerprint.value,
            "protocol_compatible": record.comparison.compatible,
            "audit_passed": record.audit_passed,
            "result_status": record.result_status,
            "base_commit": record.base_commit,
            "code_commit": record.code_commit,
            "changed_files": record.changed_files,
        }

    @staticmethod
    def _best_record(records: list[ExperimentRecord]) -> ExperimentRecord | None:
        candidates = [
            record
            for record in records
            if record.improvement_claim_allowed
            and record.protocol.metrics[0].name in record.metrics
        ]
        if not candidates:
            return None
        metric = candidates[0].protocol.metrics[0]
        return sorted(
            candidates,
            key=lambda record: record.metrics[metric.name],
            reverse=metric.direction == MetricDirection.MAXIMIZE,
        )[0]

    @classmethod
    def _audit_numeric_grounding(
        cls,
        report: dict,
        state: ResearchState,
        sweep: ParameterResponseEvidence | None,
    ) -> None:
        expected = {
            record.experiment_id: cls._record_row(record) for record in state.experiments
        }
        actual = {
            row.get("experiment_id"): row for row in report.get("Results", [])
        }
        if actual != expected:
            raise ValueError("V2_REPORT_EXPERIMENT_RECORD_GROUNDING_FAILED")
        if sweep is not None:
            reported = report.get("Parameter Sweep") or {}
            if reported.get("points") != [
                point.model_dump(mode="json") for point in sweep.points
            ]:
                raise ValueError("V2_REPORT_PARAMETER_SWEEP_GROUNDING_FAILED")
