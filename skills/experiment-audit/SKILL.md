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

Verify that a recovered result belongs to the current immutable task and bundle rather than an earlier attempt. Treat missing lineage, overwritten files, or ambiguous recovery as an integrity failure.

Inspect metric code and tensor/array shapes for result-to-code consistency. Flag broadcasting errors, label/prediction shape mismatches, train/test leakage, inconsistent averaging, identical baseline and variant implementations, dataset substitutions, ignored seeds, and metrics that do not measure the declared quantity.

## Output Contract

Return `integrity_status`, `issues`, `verified_files`, `environment_summary`, and `is_real_experiment`. Any missing result, mismatched ID, non-finite metric, failed GPU requirement, or fabricated value makes `is_real_experiment` false.

Do not repair or reinterpret a failed audit. Record concrete file-, line-, attempt-, or field-level issues and the check that detected each issue so the next implementation can correct it without changing the scientific contract.
