---
name: run-experiment
description: Safely execute or recover an accepted experiment bundle on the configured local GPU or SSH target. Use for experiment_run_analysis after bundle validation, including restart-safe monitoring and result recovery.
allowed-tools: read_run, read_artifact, local_process_run, ssh_run, read_experiment_result
---

# Run Experiment

Select exactly the runtime configured for the Run: backend-machine local GPU or generic SSH. Deploy the bundle under the configured root using `run_id/experiment_id`, verify dependencies and GPU readiness, execute the manifest command, and persist environment and logs.

Before launching, recover a completed result only when its identifiers, manifest lineage, hashes, and output schema match the current task. Otherwise create a new append-only attempt directory. Persist attempt state before process launch and update heartbeat/progress records during execution so a backend restart can resume observation without duplicating a completed run.

Never install dependencies automatically. Never infer metrics from stdout. A run is real only when the process succeeds, the declared result file exists, identifiers match the manifest, expected metrics are finite, and a required GPU probe succeeds.

Before launching a long experiment, pass syntax, dependency, framework-API, dataset-contract, argument, and CUDA preflight gates. A preflight failure is a generated-code failure and must return to the bounded repair loop without consuming a full training attempt.

Treat newline-delimited JSON progress events on stdout as the live execution record. Preserve them in the attempt log so monitoring can report the active variant, seed, epoch, total epochs, and loss without reading process memory or guessing from elapsed time.

Do not treat a quiet but live process as failed solely because it is long-running. Enforce a timeout only when the configured value is positive; preserve logs and a resumable terminal state on timeout or interruption.
