---
name: monitor-experiment
description: Observe an explicitly enabled long-running local or SSH experiment without changing its configuration.
allowed-tools: read_run, read_artifact, local_process_run, ssh_run, read_experiment_result
---

# Monitor Experiment

Use only when `monitoring_enabled` is true. Read process status, bounded log tails, output timestamps, and result-file availability from the configured local or SSH runtime.

Return `status`, `last_update`, `progress`, `warnings`, and `result_available`. Do not restart processes, edit code, install dependencies, or treat partial logs as final metrics.
