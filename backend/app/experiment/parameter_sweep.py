from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.app.experiment.workspace_adapter import (
    RepositoryExperimentContract,
    WorkspaceExperimentAdapter,
)
from backend.app.research.evidence import (
    EvidenceRelation,
    EvidenceSourceType,
    EvidenceUnit,
)
from backend.app.research.experiment import ExperimentRecord, ExperimentResultStatus
from backend.app.research.protocol import MetricDirection


class ParameterSweepRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parameter_name: str = Field(min_length=1)
    parameter_value: float
    contract: RepositoryExperimentContract

    @model_validator(mode="after")
    def config_declares_parameter(self):
        if self.contract.config.get(self.parameter_name) != self.parameter_value:
            raise ValueError("PARAMETER_SWEEP_CONFIG_VALUE_MISMATCH")
        return self


class ParameterResponsePoint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    parameter_value: float
    experiment_id: str
    metric_name: str
    metric_value: float | None
    baseline_value: float
    baseline_delta: float | None
    protocol_compatible: bool
    audit_passed: bool
    result_status: ExperimentResultStatus
    improved: bool


class ParameterResponseEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parameter_name: str
    metric_name: str
    protocol_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    points: list[ParameterResponsePoint] = Field(min_length=1)
    stable_improvement_intervals: list[tuple[float, float]] = Field(default_factory=list)
    records: list[ExperimentRecord] = Field(min_length=1)

    def evidence_units(self, *, branch_id: str) -> list[EvidenceUnit]:
        values: list[EvidenceUnit] = []
        for point in self.points:
            relation = (
                EvidenceRelation.SUPPORT
                if point.improved and point.audit_passed and point.protocol_compatible
                else EvidenceRelation.CONTRADICT
            )
            values.append(
                EvidenceUnit(
                    source_type=EvidenceSourceType.EXPERIMENT,
                    experiment_id=point.experiment_id,
                    claim=(
                        f"At {self.parameter_name}={point.parameter_value:g}, "
                        f"{self.metric_name}={point.metric_value} versus baseline "
                        f"{point.baseline_value}; delta={point.baseline_delta}."
                    ),
                    relation=relation,
                    strength=(
                        0.95
                        if point.audit_passed and point.protocol_compatible
                        else 0.2
                    ),
                    verified=point.audit_passed and point.protocol_compatible,
                    access_level="runtime",
                    provenance={
                        "branch_id": branch_id,
                        "parameter_name": self.parameter_name,
                        "parameter_value": point.parameter_value,
                        "metric_name": self.metric_name,
                        "metric_value": point.metric_value,
                        "baseline_value": point.baseline_value,
                        "baseline_delta": point.baseline_delta,
                        "protocol_fingerprint": self.protocol_fingerprint,
                        "protocol_compatible": point.protocol_compatible,
                        "audit_passed": point.audit_passed,
                    },
                )
            )
        return values


class ParameterSweepRunner:
    """Execute every declared value under one locked scientific protocol."""

    def __init__(self, adapter: WorkspaceExperimentAdapter) -> None:
        self.adapter = adapter

    def run(self, runs: list[ParameterSweepRun]) -> ParameterResponseEvidence:
        if not runs:
            raise ValueError("PARAMETER_SWEEP_RUNS_REQUIRED")
        parameter_name = runs[0].parameter_name
        protocol = runs[0].contract.protocol
        fingerprint = protocol.fingerprint()
        metric = protocol.metrics[0]
        baseline_value = runs[0].contract.baseline_metrics[metric.name]
        seen: set[float] = set()
        for run in runs:
            if run.parameter_name != parameter_name:
                raise ValueError("PARAMETER_SWEEP_PARAMETER_NAME_MISMATCH")
            if run.parameter_value in seen:
                raise ValueError("PARAMETER_SWEEP_DUPLICATE_VALUE")
            seen.add(run.parameter_value)
            contract = run.contract
            if contract.protocol.fingerprint() != fingerprint:
                raise ValueError("PARAMETER_SWEEP_PROTOCOL_DRIFT")
            if contract.baseline_protocol.fingerprint() != fingerprint:
                raise ValueError("PARAMETER_SWEEP_BASELINE_PROTOCOL_DRIFT")
            if contract.baseline_metrics != runs[0].contract.baseline_metrics:
                raise ValueError("PARAMETER_SWEEP_BASELINE_METRICS_DRIFT")
            if list(contract.protocol.seed_policy.seeds) != list(
                runs[0].contract.protocol.seed_policy.seeds
            ):
                raise ValueError("PARAMETER_SWEEP_SEED_DRIFT")

        records = [self.adapter.execute(run.contract) for run in runs]
        points: list[ParameterResponsePoint] = []
        for run, record in zip(runs, records, strict=True):
            value = record.metrics.get(metric.name)
            delta = value - baseline_value if value is not None else None
            improved = False
            if value is not None and record.improvement_claim_allowed:
                improved = (
                    value > baseline_value
                    if metric.direction == MetricDirection.MAXIMIZE
                    else value < baseline_value
                )
            points.append(
                ParameterResponsePoint(
                    parameter_value=run.parameter_value,
                    experiment_id=record.experiment_id,
                    metric_name=metric.name,
                    metric_value=value,
                    baseline_value=baseline_value,
                    baseline_delta=delta,
                    protocol_compatible=record.comparison.compatible,
                    audit_passed=record.audit_passed,
                    result_status=record.result_status,
                    improved=improved,
                )
            )
        return ParameterResponseEvidence(
            parameter_name=parameter_name,
            metric_name=metric.name,
            protocol_fingerprint=fingerprint.value,
            points=points,
            stable_improvement_intervals=self._stable_intervals(points),
            records=records,
        )

    @staticmethod
    def _stable_intervals(
        points: list[ParameterResponsePoint],
    ) -> list[tuple[float, float]]:
        ordered = sorted(points, key=lambda point: point.parameter_value)
        groups: list[list[float]] = []
        current: list[float] = []
        for point in ordered:
            if point.improved:
                current.append(point.parameter_value)
            elif current:
                groups.append(current)
                current = []
        if current:
            groups.append(current)
        return [(group[0], group[-1]) for group in groups if len(group) >= 2]
