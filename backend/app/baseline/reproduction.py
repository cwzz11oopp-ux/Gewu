from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from backend.app.research.profiles import (
    BaselineProfile,
    BaselineReproductionStatus,
)
from backend.app.research.protocol import ExperimentProtocol
from backend.app.workspace.repository import RepositoryWorkspace


class BaselineReproductionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repository: str = Field(min_length=1)
    commit: str = Field(min_length=1)
    task: str = Field(min_length=1)
    entrypoint: str = Field(min_length=1)
    protocol: ExperimentProtocol
    command: list[str] = Field(min_length=1)
    result_path: str = Field(min_length=1)
    environment: dict[str, Any]
    reported_metrics: dict[str, float] = Field(default_factory=dict)
    tolerance: float = Field(default=1e-6, ge=0.0)
    timeout_seconds: float = Field(default=300, gt=0)


class BaselineReproducer:
    """Runs a repository-owned baseline command and validates its result JSON."""

    def __init__(self, workspace: RepositoryWorkspace) -> None:
        self.workspace = workspace

    def reproduce(self, request: BaselineReproductionRequest) -> BaselineProfile:
        inspection = self.workspace.inspect()
        common = {
            "repository": request.repository,
            "commit": request.commit,
            "task": request.task,
            "dataset": request.protocol.dataset,
            "entrypoint": request.entrypoint,
            "environment": request.environment,
            "protocol": request.protocol,
            "reported_metrics": request.reported_metrics,
        }
        if inspection.head != request.commit:
            return BaselineProfile(
                **common,
                reproduction_status=BaselineReproductionStatus.ENVIRONMENT_FAILED,
                validation_reason=(
                    f"Repository HEAD {inspection.head} differs from requested commit "
                    f"{request.commit}."
                ),
            )
        if inspection.dirty:
            return BaselineProfile(
                **common,
                reproduction_status=BaselineReproductionStatus.ENVIRONMENT_FAILED,
                validation_reason="Repository is dirty before baseline reproduction.",
            )
        try:
            result = self.workspace.command_runner.run(
                request.command,
                timeout_seconds=request.timeout_seconds,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return BaselineProfile(
                **common,
                reproduction_status=BaselineReproductionStatus.ENVIRONMENT_FAILED,
                validation_reason=f"Baseline command could not start: {type(exc).__name__}: {exc}",
            )
        if result.returncode != 0:
            return BaselineProfile(
                **common,
                reproduction_status=BaselineReproductionStatus.RUN_FAILED,
                validation_reason=(result.stderr.strip() or result.stdout.strip() or "Baseline command failed"),
            )
        try:
            payload = json.loads(self.workspace.read_text(request.result_path))
        except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            return BaselineProfile(
                **common,
                reproduction_status=BaselineReproductionStatus.RUN_FAILED,
                validation_reason=f"Baseline result is unavailable or invalid: {type(exc).__name__}",
            )
        if request.result_path in self.workspace.git.tracked_files():
            return BaselineProfile(
                **common,
                reproduction_status=BaselineReproductionStatus.RUN_FAILED,
                validation_reason="Baseline result path modifies a tracked repository file.",
            )
        unexpected_changes = set(self.workspace.git.changed_files()) - {
            request.result_path.replace("\\", "/")
        }
        if unexpected_changes:
            return BaselineProfile(
                **common,
                reproduction_status=BaselineReproductionStatus.RUN_FAILED,
                validation_reason=(
                    "Baseline command modified repository files: "
                    + ", ".join(sorted(unexpected_changes))
                ),
            )

        expected_fingerprint = request.protocol.fingerprint().value
        if payload.get("protocol_fingerprint") != expected_fingerprint:
            return BaselineProfile(
                **common,
                local_metrics=self._safe_metrics(payload.get("metrics")),
                seeds=self._safe_seeds(payload.get("seeds")),
                reproduction_status=BaselineReproductionStatus.PROTOCOL_MISMATCH,
                validation_reason="Baseline result protocol fingerprint does not match the request.",
            )
        metrics = self._safe_metrics(payload.get("metrics"))
        expected_names = {item.name for item in request.protocol.metrics}
        seeds = self._safe_seeds(payload.get("seeds"))
        if set(metrics) != expected_names or not set(seeds).issubset(
            set(request.protocol.seed_policy.seeds)
        ) or len(seeds) < request.protocol.seed_policy.minimum_repetitions:
            return BaselineProfile(
                **common,
                local_metrics=metrics,
                seeds=seeds,
                reproduction_status=BaselineReproductionStatus.PROTOCOL_MISMATCH,
                validation_reason="Baseline metrics or seed execution differs from the protocol.",
            )

        within = self._within_reported_tolerance(
            metrics, request.reported_metrics, request.tolerance
        )
        status = (
            BaselineReproductionStatus.REPRODUCED_WITHIN_TOLERANCE
            if within
            else BaselineReproductionStatus.REPRODUCED_BUT_REPORTED_MISMATCH
        )
        reason = (
            "Local baseline is protocol-valid and within reported tolerance."
            if within
            else "Local baseline is protocol-valid but differs from reported reference metrics."
        )
        return BaselineProfile(
            **common,
            local_metrics=metrics,
            seeds=seeds,
            reproduction_status=status,
            validation_reason=reason,
            audit_passed=True,
        )

    @staticmethod
    def validate(profile: BaselineProfile) -> BaselineProfile:
        if profile.reproduction_status not in {
            BaselineReproductionStatus.REPRODUCED_WITHIN_TOLERANCE,
            BaselineReproductionStatus.REPRODUCED_BUT_REPORTED_MISMATCH,
        }:
            return profile
        if not profile.audit_passed or not profile.local_metrics or not profile.seeds:
            return profile.model_copy(
                update={
                    "reproduction_status": BaselineReproductionStatus.RUN_FAILED,
                    "validation_reason": "Baseline validation lacks audited local authority.",
                }
            )
        return profile.model_copy(
            update={
                "reproduction_status": BaselineReproductionStatus.VALIDATED,
                "validation_reason": profile.validation_reason
                + " Local metrics are the comparison denominator.",
            }
        )

    def reproduce_and_validate(
        self, request: BaselineReproductionRequest
    ) -> BaselineProfile:
        return self.validate(self.reproduce(request))

    @staticmethod
    def _safe_metrics(value: object) -> dict[str, float]:
        if not isinstance(value, dict):
            return {}
        metrics: dict[str, float] = {}
        for name, metric in value.items():
            if isinstance(metric, bool) or not isinstance(metric, (int, float)):
                return {}
            number = float(metric)
            if not math.isfinite(number):
                return {}
            metrics[str(name)] = number
        return metrics

    @staticmethod
    def _safe_seeds(value: object) -> list[int]:
        if not isinstance(value, list) or any(
            isinstance(item, bool) or not isinstance(item, int) for item in value
        ):
            return []
        return list(dict.fromkeys(value))

    @staticmethod
    def _within_reported_tolerance(
        local: dict[str, float], reported: dict[str, float], tolerance: float
    ) -> bool:
        if not reported:
            return True
        if set(local) != set(reported):
            return False
        return all(abs(local[name] - reported[name]) <= tolerance for name in local)
