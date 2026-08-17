from backend.app.agents.diagnostic import ExperimentDiagnosticAgent


class RecordingLLM:
    def __init__(self):
        self.calls = []

    def generate_json(self, operation, payload, schema, instructions=""):
        self.calls.append((operation, payload, schema, instructions))
        return {
            "category": "configuration",
            "root_cause": "An unrecognized configuration failed.",
            "evidence": ["trace evidence"],
            "retryable": True,
            "auto_repairable": True,
            "repair_action": "retry_stage",
            "repair_scope": "everything",
            "user_message": "Unknown failure.",
            "next_action": "Inspect configuration.",
        }


def _diagnose(agent, error):
    return agent.diagnose(error, task={}, bundle={}, attempts=[])


def test_dataset_download_failure_uses_deterministic_safe_repair():
    llm = RecordingLLM()
    diagnosis = _diagnose(
        ExperimentDiagnosticAgent(llm),
        "EXPERIMENT_DATASET_DOWNLOAD_FAILED:cifar-10. File not found or corrupted.",
    )

    assert diagnosis["category"] == "dataset"
    assert diagnosis["auto_repairable"] is True
    assert diagnosis["repair_action"] == "quarantine_corrupt_dataset_download"
    assert diagnosis["retryable"] is True
    assert not llm.calls


def test_dependency_failure_is_advisory_and_never_auto_installs():
    diagnosis = _diagnose(
        ExperimentDiagnosticAgent(RecordingLLM()),
        "LOCAL_EXPERIMENT_DEPENDENCY_MISSING:torchvision",
    )

    assert diagnosis["category"] == "dependency"
    assert diagnosis["auto_repairable"] is False
    assert diagnosis["repair_action"] == "none"


def test_generated_code_failure_requests_bounded_source_repair():
    diagnosis = _diagnose(
        ExperimentDiagnosticAgent(RecordingLLM()),
        "LOCAL_EXPERIMENT_RUN_FAILED: AttributeError: module 'torch' has no attribute 'softplus'",
    )

    assert diagnosis["category"] == "generated_code"
    assert diagnosis["auto_repairable"] is True
    assert diagnosis["repair_action"] == "repair_experiment_code"
    assert diagnosis["repair_scope"] == "current experiment bundle only"


def test_unknown_model_diagnosis_cannot_grant_mutation_permission():
    llm = RecordingLLM()
    diagnosis = _diagnose(
        ExperimentDiagnosticAgent(llm),
        "UNRECOGNIZED_EXPERIMENT_FAILURE:details",
    )

    assert llm.calls[0][0] == "diagnostic.diagnose_experiment"
    assert diagnosis["root_cause"] == "An unrecognized configuration failed."
    assert diagnosis["auto_repairable"] is False
    assert diagnosis["repair_action"] == "none"
    assert diagnosis["repair_scope"] == "none"
