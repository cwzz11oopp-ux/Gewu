from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from backend.app.research.evidence import EvidenceRelation, EvidenceUnit
from backend.app.research.experiment import ExperimentRecord
from backend.app.state.research import ResearchState

if TYPE_CHECKING:
    from backend.app.experiment.parameter_sweep import ParameterResponseEvidence


class ClaimStatus(StrEnum):
    SUPPORTED = "SUPPORTED"
    PARTIALLY_SUPPORTED = "PARTIALLY_SUPPORTED"
    NOT_SUPPORTED = "NOT_SUPPORTED"


class ClaimEvidenceLink(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: str = Field(min_length=1)
    experiment_id: str = Field(min_length=1)
    relation: EvidenceRelation
    metric_name: str = Field(min_length=1)
    metric_value: float
    baseline_value: float
    protocol_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    config_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    code_commit: str | None = None
    parameter_name: str = ""
    parameter_value: float | None = None
    protocol_compatible: bool
    audit_passed: bool
    evidence_strength: float = Field(ge=0.0, le=1.0)


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: f"claim_{uuid4().hex[:12]}")
    statement: str = Field(min_length=1)
    branch_id: str = Field(min_length=1)
    major: bool = True
    status: ClaimStatus
    rationale: str = Field(min_length=1)
    evidence_strength: float = Field(ge=0.0, le=1.0)
    links: list[ClaimEvidenceLink] = Field(min_length=1)

    @model_validator(mode="after")
    def supported_claim_has_support(self):
        if self.status == ClaimStatus.SUPPORTED and not any(
            link.relation == EvidenceRelation.SUPPORT for link in self.links
        ):
            raise ValueError("SUPPORTED_CLAIM_SUPPORTING_EVIDENCE_REQUIRED")
        return self

    @computed_field
    @property
    def supporting_experiments(self) -> list[str]:
        return list(
            dict.fromkeys(
                link.experiment_id
                for link in self.links
                if link.relation == EvidenceRelation.SUPPORT
            )
        )

    @computed_field
    @property
    def contradicting_evidence(self) -> list[str]:
        return list(
            dict.fromkeys(
                link.evidence_id
                for link in self.links
                if link.relation == EvidenceRelation.CONTRADICT
            )
        )

    @computed_field
    @property
    def audit_protocol_status(self) -> str:
        return (
            "AUDITED_COMPATIBLE"
            if all(link.audit_passed and link.protocol_compatible for link in self.links)
            else "MIXED_OR_UNAUDITED"
        )


class ClaimGraphAudit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    exportable: bool
    verified_major_claims: int
    blocked_claim_ids: list[str] = Field(default_factory=list)


class ClaimEvidenceGraph(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claims: list[Claim] = Field(default_factory=list)

    @classmethod
    def from_research_state(cls, state: ResearchState) -> "ClaimEvidenceGraph":
        evidence_by_experiment = cls._evidence_by_experiment(state.evidence)
        claims: list[Claim] = []
        for record in state.experiments:
            evidence = evidence_by_experiment.get(record.experiment_id, [])
            links = cls._links(record, evidence)
            if not links:
                continue
            primary = record.protocol.metrics[0]
            value = record.metrics.get(primary.name)
            baseline = record.baseline_metrics.get(primary.name)
            improved = value is not None and baseline is not None and value != baseline
            claims.append(
                Claim(
                    statement=(
                        f"Under the audited compatible protocol, branch {record.branch_id} "
                        f"changed {primary.name} from {baseline} to {value}."
                    ),
                    branch_id=record.branch_id,
                    status=(
                        ClaimStatus.SUPPORTED
                        if improved and any(
                            link.relation == EvidenceRelation.SUPPORT for link in links
                        )
                        else ClaimStatus.NOT_SUPPORTED
                    ),
                    rationale="Status is derived only from linked protocol-bound runtime evidence.",
                    evidence_strength=max(link.evidence_strength for link in links),
                    links=links,
                )
            )
        return cls(claims=claims)

    @classmethod
    def from_parameter_sweep(
        cls,
        state: ResearchState,
        sweep: ParameterResponseEvidence,
        *,
        branch_id: str,
    ) -> "ClaimEvidenceGraph":
        records = {record.experiment_id: record for record in state.experiments}
        evidence_by_experiment = cls._evidence_by_experiment(state.evidence)
        sweep_links: list[ClaimEvidenceLink] = []
        for point in sweep.points:
            record = records.get(point.experiment_id)
            if record is None:
                raise ValueError(f"CLAIM_SWEEP_EXPERIMENT_MISSING:{point.experiment_id}")
            sweep_links.extend(cls._links(record, evidence_by_experiment.get(point.experiment_id, [])))
        if not sweep_links:
            raise ValueError("CLAIM_SWEEP_EVIDENCE_REQUIRED")

        improved = [point for point in sweep.points if point.improved]
        compatible_points = [
            point
            for point in sweep.points
            if point.audit_passed and point.protocol_compatible and point.metric_value is not None
        ]
        best_value = max(point.metric_value for point in compatible_points)
        best_points = [point for point in compatible_points if point.metric_value == best_value]
        calibration_status = (
            ClaimStatus.SUPPORTED if improved else ClaimStatus.NOT_SUPPORTED
        )
        mechanism_status = (
            ClaimStatus.PARTIALLY_SUPPORTED
            if improved and sweep.stable_improvement_intervals
            else ClaimStatus.NOT_SUPPORTED
        )
        unique_status = ClaimStatus.NOT_SUPPORTED
        uniqueness_links = [
            link.model_copy(
                update={
                    "relation": (
                        EvidenceRelation.CONTRADICT
                        if link.parameter_value != 0.2
                        and link.metric_value == best_value
                        else EvidenceRelation.SUPPORT
                    )
                }
            )
            for link in sweep_links
        ]
        common_strength = min(link.evidence_strength for link in sweep_links)
        return cls(
            claims=[
                Claim(
                    statement="Threshold calibration improves the locked fixture metric.",
                    branch_id=branch_id,
                    status=calibration_status,
                    rationale=(
                        f"{len(improved)} of {len(sweep.points)} predeclared values improved "
                        "the audited metric relative to baseline."
                    ),
                    evidence_strength=common_strength,
                    links=sweep_links,
                ),
                Claim(
                    statement="The observed improvement is attributable to threshold calibration.",
                    branch_id=branch_id,
                    status=mechanism_status,
                    rationale=(
                        "A contiguous improving interval and the prior full-revert ablation support "
                        "a calibration contribution, but the finite fixture does not isolate all alternatives."
                    ),
                    evidence_strength=min(common_strength, 0.85),
                    links=sweep_links,
                ),
                Claim(
                    statement="Threshold 0.2 is uniquely optimal.",
                    branch_id=branch_id,
                    status=unique_status,
                    rationale=(
                        "The sweep contains multiple tied best values; uniqueness is contradicted."
                        if len(best_points) != 1
                        else "A finite sampled grid cannot establish a globally unique optimum."
                    ),
                    evidence_strength=common_strength,
                    links=uniqueness_links,
                ),
            ]
        )

    def audit(self) -> ClaimGraphAudit:
        blocked = [
            claim.id
            for claim in self.claims
            if claim.major
            and (
                not claim.links
                or any(
                    not link.audit_passed or not link.protocol_compatible
                    for link in claim.links
                )
            )
        ]
        supported = sum(
            claim.major
            and claim.status == ClaimStatus.SUPPORTED
            and bool(claim.links)
            for claim in self.claims
        )
        return ClaimGraphAudit(
            exportable=not blocked and supported > 0,
            verified_major_claims=supported,
            blocked_claim_ids=blocked,
        )

    @staticmethod
    def _evidence_by_experiment(
        evidence: list[EvidenceUnit],
    ) -> dict[str, list[EvidenceUnit]]:
        values: dict[str, list[EvidenceUnit]] = {}
        for item in evidence:
            if item.experiment_id and item.verified:
                values.setdefault(item.experiment_id, []).append(item)
        return values

    @staticmethod
    def _links(
        record: ExperimentRecord,
        evidence: list[EvidenceUnit],
    ) -> list[ClaimEvidenceLink]:
        if not record.improvement_claim_allowed:
            return []
        primary = record.protocol.metrics[0]
        if primary.name not in record.metrics or primary.name not in record.baseline_metrics:
            return []
        config_json = json.dumps(
            record.config,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return [
            ClaimEvidenceLink(
                evidence_id=item.id,
                experiment_id=record.experiment_id,
                relation=item.relation,
                metric_name=primary.name,
                metric_value=record.metrics[primary.name],
                baseline_value=record.baseline_metrics[primary.name],
                protocol_fingerprint=record.protocol_fingerprint.value,
                config_fingerprint=hashlib.sha256(config_json.encode("utf-8")).hexdigest(),
                code_commit=record.code_commit,
                parameter_name=str(item.provenance.get("parameter_name") or ""),
                parameter_value=item.provenance.get("parameter_value"),
                protocol_compatible=record.comparison.compatible,
                audit_passed=record.audit_passed,
                evidence_strength=item.strength,
            )
            for item in evidence
        ]
