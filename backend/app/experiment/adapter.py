from __future__ import annotations

from backend.app.agents.experiment import ExperimentAgent
from backend.app.experiment.contract import ExperimentContract
from backend.app.providers.experiment import ExperimentProvider
from backend.app.providers.llm import LLMProvider
from backend.app.research.experiment import ExperimentRecord, ExperimentResultStatus
from backend.app.research.protocol import ProtocolCompatibilityGate


class ExperimentExecutionAdapter:
    """Maps a V2 contract onto the audited V1 Local/SSH experiment boundary."""

    def __init__(
        self,
        provider: ExperimentProvider,
        llm_provider: LLMProvider | None = None,
    ) -> None:
        self.provider = provider
        self.agent = ExperimentAgent(provider, llm_provider)

    def execute(self, contract: ExperimentContract) -> ExperimentRecord:
        task = {
            "experiment_id": contract.bundle.manifest.experiment_id,
            "manifest": contract.bundle.manifest.model_dump(),
            "purpose": contract.purpose,
            "branch_id": contract.branch_id,
        }
        result = self.agent.run(task, contract.bundle)
        audit = self.agent.audit_result(contract.bundle, result)
        audit_passed = bool(
            audit.get("integrity_status") == "passed"
            and audit.get("is_real_experiment") is True
        )
        comparison = ProtocolCompatibilityGate.evaluate(
            contract.baseline_protocol,
            contract.protocol,
            audit_passed=audit_passed,
        )
        analysis = self.agent.analyze_result({}, task, result)
        attempts = list(result.get("attempts") or [])
        logs = [
            str(item.get("log_path"))
            for item in attempts
            if str(item.get("log_path") or "").strip()
        ]
        changed_files = [item.path for item in contract.bundle.files]
        return ExperimentRecord(
            experiment_id=contract.bundle.manifest.experiment_id,
            branch_id=contract.branch_id,
            purpose=contract.purpose,
            repository=contract.repository,
            base_commit=contract.base_commit,
            code_commit=contract.code_commit,
            changed_files=changed_files,
            diff_summary=(
                "Standalone ExperimentBundle executed through the legacy provider; "
                "no repository diff is attributed."
            ),
            protocol=contract.protocol,
            protocol_fingerprint=contract.protocol.fingerprint(),
            config=contract.config,
            seeds=list(contract.bundle.manifest.seeds),
            metrics=dict(result.get("metrics") or {}),
            baseline_metrics=contract.baseline_metrics,
            comparison=comparison,
            audit_passed=audit_passed,
            environment=dict(result.get("environment") or {}),
            logs=logs,
            result_status=ExperimentResultStatus.SUCCEEDED,
            analysis=str(analysis.get("observations") or analysis.get("verdict") or ""),
        )
