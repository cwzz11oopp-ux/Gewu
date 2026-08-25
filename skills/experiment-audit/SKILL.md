---
name: experiment-audit
description: Independently audit experiment provenance, execution integrity, and result-to-code consistency. Use after every real experiment attempt and before its metrics can affect claims, feedback, or reports.
allowed-tools: read_run, read_artifact, read_experiment_result, audit_result
---

# Experiment Audit

## Inputs

Read the accepted plan, manifest, complete generated source files with hashes, runtime result, environment, logs, and attempt record.

## Protocol

Verify manifest and source hashes, Run and experiment identifiers, command arguments, environment, device record, dependency preflight, exit status, logs, result schema, expected metrics, and parent Artifact relationship.

When a runtime contract is present, it is authoritative for the current execution stage, budget, and seed set. The manifest seed list may be a larger planned pool; do not flag its difference from the runtime-contract seed subset as a mismatch. Flag a seed mismatch only when the result disagrees with the runtime contract actually executed.

The Harness owns aggregation across runtime-contract seeds. A train.py implementation may correctly emit one `{seed, metrics}` result per invocation while the Harness produces the aggregate `seed_results`, `metric_summary`, and top-level metrics. Do not flag that intentional boundary as a result-schema mismatch.

The Harness also appends its authoritative `--seed` at runtime. A manifest may intentionally omit `--seed` from its static `python_args`, while train.py correctly requires it; do not flag that as an argument mismatch when the harness/runtime contract is present.

Finite expected metrics are integrity-valid regardless of their direction or magnitude. A zero, negative, or unfavorable effect is a scientific outcome to report as unsupported or inconclusive, never an audit defect or a reason to repair code.

Verify that a recovered result belongs to the current immutable task and bundle rather than an earlier attempt. Treat missing lineage, overwritten files, or ambiguous recovery as an integrity failure.

Inspect metric code and tensor/array shapes for result-to-code consistency. Flag broadcasting errors, label/prediction shape mismatches, train/test leakage, inconsistent averaging, identical baseline and variant implementations, dataset substitutions, ignored seeds, and metrics that do not measure the declared quantity.

## Output Contract

Return `integrity_status`, `issues`, `verified_files`, `environment_summary`, and `is_real_experiment`. Any missing result, mismatched ID, non-finite metric, failed GPU requirement, or fabricated value makes `is_real_experiment` false.

Do not repair or reinterpret a failed audit. Record concrete file-, line-, attempt-, or field-level issues and the check that detected each issue so the next implementation can correct it without changing the scientific contract.
