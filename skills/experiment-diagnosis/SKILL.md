---
name: experiment-diagnosis
description: Diagnose experiment preparation, dataset, environment, generated-code, execution, analysis, and audit failures; drive a bounded diagnose-repair-validate-smoke-test-retry loop while preserving the accepted scientific contract.
allowed-tools: read_run, read_artifact, read_experiment_result, audit_result, repair_dataset_cache, retry_experiment, build_experiment_bundle
---

# Experiment Diagnosis

## Protocol

Read the exception code and message, attempt record, accepted plan, manifest, generated source, environment, log tail, result, and audit when available.

Classify the failure as `dataset`, `dependency`, `gpu`, `generated_code`, `timeout`, `analysis`, `audit`, `configuration`, or `unknown`. Return the direct root cause rather than repeating “experiment failed”. Cite concrete evidence such as an error code, file path, checksum mismatch, missing module, failed command, or source line.

## Output Contract

Return `category`, `error_code`, `root_cause`, `evidence`, `retryable`, `auto_repairable`, `repair_action`, `repair_scope`, `user_message`, and `next_action`.

Allowed repair actions are:

- `quarantine_corrupt_dataset_download`: move only a known incomplete dataset download inside the configured dataset cache to a quarantine directory, then retry provisioning.
- `retry_stage`: retry a transient dataset, provider, or model operation without changing scientific inputs.
- `repair_experiment_code`: repair only the current Bundle source and dependency declarations from concrete traceback evidence. Preserve dataset, parameters, seeds, metrics, identifiers, GPU requirement, and all other accepted scientific inputs; validate the repaired Bundle before retrying.
- `regenerate_experiment_bundle`: use only when the source is structurally unusable and a local code repair is impossible. Freeze the accepted scientific contract while regenerating implementation files.
- `none`: report the blocker without mutation.

For generated-code failures, prefer the smallest source correction that resolves the cited traceback. Feed syntax, contract, API-preflight, or smoke-test rejection details back into the repair operation and request another repair candidate without rerunning the full experiment. Run the full experiment only after all fast validation gates pass.

## Safety

Use at most two runtime repair cycles after the initial attempt and at most three candidate revisions inside one code-repair cycle. Never install dependencies, change CUDA or drivers, edit credentials, alter the accepted hypothesis or plan, run arbitrary shell commands, delete an unknown path, or weaken validation. Prefer recoverable quarantine moves over deletion. Resolve and verify every repair target remains inside the configured experiment or dataset root.

Mark dependency, credential, hardware, and unknown failures as non-auto-repairable unless a deterministic registered repair exists. A Reviewer judgment is not part of this Skill.
