from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.app.research.experiment import ExperimentRecord, ExperimentResultStatus
from backend.app.research.protocol import ExperimentProtocol, ProtocolCompatibilityGate
from backend.app.workspace.worktree import WorktreeManager


class RepositoryExperimentContract(BaseModel):
    """Low-level fixture/planner-output contract; autonomous callers use the general planner."""

    model_config = ConfigDict(extra="forbid")

    experiment_id: str = Field(min_length=1)
    branch_id: str = Field(min_length=1)
    worktree_branch: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    repository: str = Field(min_length=1)
    base_commit: str = Field(min_length=1)
    protocol: ExperimentProtocol
    baseline_protocol: ExperimentProtocol
    baseline_metrics: dict[str, float]
    config: dict[str, Any]
    implementation_files: dict[str, str] = Field(default_factory=dict)
    static_commands: list[list[str]] = Field(default_factory=list)
    smoke_commands: list[list[str]] = Field(default_factory=list)
    formal_command: list[str] = Field(min_length=1)
    result_path: str = Field(min_length=1)
    environment: dict[str, Any]
    commit_message: str = Field(min_length=1)
    cleanup_worktree: bool = True

    @model_validator(mode="after")
    def baseline_metrics_match_protocol(self):
        expected = {item.name for item in self.protocol.metrics}
        if set(self.baseline_metrics) != expected:
            raise ValueError("REPOSITORY_EXPERIMENT_BASELINE_METRICS_MISMATCH")
        return self


class WorkspaceExperimentAdapter:
    """Executes one approved repository experiment in an isolated Git worktree."""

    def __init__(self, manager: WorktreeManager) -> None:
        self.manager = manager

    def execute(self, contract: RepositoryExperimentContract) -> ExperimentRecord:
        comparison = ProtocolCompatibilityGate.evaluate(
            contract.baseline_protocol,
            contract.protocol,
            audit_passed=False,
        )
        logs: list[str] = []
        worktree = None
        changed_files: list[str] = []
        diff_summary = ""
        code_commit: str | None = None
        metrics: dict[str, float] = {}
        seeds: list[int] = list(contract.protocol.seed_policy.seeds)
        audit_passed = False
        failure = ""
        try:
            worktree = self.manager.create(
                branch=contract.worktree_branch,
                base_commit=contract.base_commit,
                directory_name=contract.experiment_id,
            )
            allowed = {
                Path(command[0]).name
                for command in [
                    *contract.static_commands,
                    *contract.smoke_commands,
                    contract.formal_command,
                ]
                if command
            }
            allowed.add("git")
            workspace = worktree.workspace(allowed_executables=allowed)
            for relative_path, content in contract.implementation_files.items():
                workspace.write_text(relative_path, content)
            changed_files = workspace.git.changed_files()
            if set(changed_files) != set(contract.implementation_files):
                raise ValueError("REPOSITORY_EXPERIMENT_IMPLEMENTATION_DIFF_MISMATCH")
            diff_summary = workspace.git.diff()

            for phase, commands in (
                ("static", contract.static_commands),
                ("smoke", contract.smoke_commands),
                ("formal", [contract.formal_command]),
            ):
                for command in commands:
                    result = workspace.command_runner.run(command)
                    logs.append(
                        f"{phase} rc={result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
                    )
                    if result.returncode != 0:
                        raise RuntimeError(f"{phase.upper()}_COMMAND_FAILED")

            if contract.result_path in workspace.git.tracked_files():
                raise ValueError("REPOSITORY_EXPERIMENT_RESULT_PATH_TRACKED")
            unexpected_changes = set(workspace.git.changed_files()) - set(changed_files) - {
                contract.result_path.replace("\\", "/")
            }
            if unexpected_changes:
                raise ValueError(
                    "REPOSITORY_EXPERIMENT_UNEXPECTED_CHANGES:"
                    + ",".join(sorted(unexpected_changes))
                )
            payload = json.loads(workspace.read_text(contract.result_path))
            if payload.get("protocol_fingerprint") != contract.protocol.fingerprint().value:
                raise ValueError("REPOSITORY_EXPERIMENT_PROTOCOL_FINGERPRINT_MISMATCH")
            metrics = self._metrics(payload.get("metrics"), contract.protocol)
            seeds = self._seeds(payload.get("seeds"), contract.protocol)
            audit_passed = True
            if changed_files:
                code_commit = workspace.commit_validated(
                    contract.commit_message,
                    changed_files,
                )
            else:
                code_commit = workspace.git.head()
        except Exception as exc:  # failure is evidence and must be returned, not hidden
            failure = f"{type(exc).__name__}: {exc}"
            logs.append(f"failure: {failure}")
        finally:
            if worktree is not None and contract.cleanup_worktree:
                try:
                    self.manager.remove(worktree, force=not audit_passed)
                except Exception as cleanup_exc:
                    logs.append(
                        f"cleanup_failure: {type(cleanup_exc).__name__}: {cleanup_exc}"
                    )

        comparison = ProtocolCompatibilityGate.evaluate(
            contract.baseline_protocol,
            contract.protocol,
            audit_passed=audit_passed,
        )
        return ExperimentRecord(
            experiment_id=contract.experiment_id,
            branch_id=contract.branch_id,
            purpose=contract.purpose,
            repository=contract.repository,
            base_commit=contract.base_commit,
            code_commit=code_commit,
            changed_files=changed_files,
            diff_summary=diff_summary,
            protocol=contract.protocol,
            protocol_fingerprint=contract.protocol.fingerprint(),
            config=contract.config,
            seeds=seeds,
            metrics=metrics,
            baseline_metrics=contract.baseline_metrics,
            comparison=comparison,
            audit_passed=audit_passed,
            environment=contract.environment,
            logs=logs,
            result_status=(
                ExperimentResultStatus.SUCCEEDED
                if audit_passed
                else ExperimentResultStatus.FAILED
            ),
            analysis=(
                "Repository experiment completed with authoritative protocol-bound metrics."
                if audit_passed
                else failure
            ),
        )

    @staticmethod
    def _metrics(value: object, protocol: ExperimentProtocol) -> dict[str, float]:
        if not isinstance(value, dict):
            raise ValueError("REPOSITORY_EXPERIMENT_METRICS_INVALID")
        expected = {item.name for item in protocol.metrics}
        if set(value) != expected:
            raise ValueError("REPOSITORY_EXPERIMENT_METRICS_MISMATCH")
        metrics: dict[str, float] = {}
        for name, raw in value.items():
            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                raise ValueError(f"REPOSITORY_EXPERIMENT_METRIC_NOT_NUMERIC:{name}")
            number = float(raw)
            if not math.isfinite(number):
                raise ValueError(f"REPOSITORY_EXPERIMENT_METRIC_NOT_FINITE:{name}")
            metrics[str(name)] = number
        return metrics

    @staticmethod
    def _seeds(value: object, protocol: ExperimentProtocol) -> list[int]:
        if not isinstance(value, list) or any(
            isinstance(item, bool) or not isinstance(item, int) for item in value
        ):
            raise ValueError("REPOSITORY_EXPERIMENT_SEEDS_INVALID")
        seeds = list(dict.fromkeys(value))
        if not set(seeds).issubset(set(protocol.seed_policy.seeds)):
            raise ValueError("REPOSITORY_EXPERIMENT_SEEDS_PROTOCOL_MISMATCH")
        if len(seeds) < protocol.seed_policy.minimum_repetitions:
            raise ValueError("REPOSITORY_EXPERIMENT_REPLICATIONS_INSUFFICIENT")
        return seeds
