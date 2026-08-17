# Round 5 Issue Log

## R5-P0-001

### Severity

P0

### Stage

Runtime / Workflow preflight

### Symptom

The frozen Round 4 snapshot cannot start a real Research → Hypothesis → Experiment
→ Critic → Writer execution because the configured real LLM and experiment runtime
are not ready.

### Expected Behavior

Round 5 must invoke a configured real model, retrieve traceable evidence, generate
and execute actual experiment code, and preserve the resulting artifacts.  Mock or
fallback output must not be presented as this real acceptance run.

### Actual Behavior

The non-secret configuration preflight reported:

```text
llm: qwen, ready=false, code=QWEN_API_KEY_MISSING
literature: arxiv_semantic_scholar, ready=true
experiment: remote_gpu, ready=false,
  missing=REMOTE_GPU_HOST, REMOTE_GPU_USER, REMOTE_GPU_PROJECT_DIR,
  code=REMOTE_GPU_CONFIG_MISSING
```

Starting the pipeline would therefore either fail before a real experiment or enter
a development fallback path.  Neither outcome satisfies the Round 5 requirement.

### Evidence

```powershell
D:\竞赛\.venv\Scripts\python.exe -B -c "from backend.app.config import Settings; print(Settings.from_env().provider_status())"
```

Executed at `2026-08-14T15:26:28+08:00`; the command printed the status above and
did not expose a credential value.

### Run ID

Planned external acceptance identifier: `round5_ising_e2e_001`.

No durable repository `run_*` was created: preflight blocked before a valid real
workflow start, intentionally avoiding a misleading fallback/mock run.

### Agent

Not started.

### Skill ID

Not loaded into a model context.  Loading a Skill without a configured real model
would not prove Agent behavior for this acceptance run.

### State Before

Frozen Round 4 source snapshot; no Round 5 repository state, experiment artifacts,
or generated Ising results.

### State After

Unchanged runtime research state.  Only this issue record, timeline, and validation
report were added to the source documentation.

### Tool / Command

The non-mutating `Settings.provider_status()` command shown above.

### Artifact

No research artifact.  This issue log is the sole Round 5 preflight artifact.

### Root Cause

Known configuration absence: no Qwen API key is available to this process, and the
selected remote-GPU experiment provider lacks required host, user, and project-dir
configuration.

### Recovery Attempt

Not attempted.  The only available fallback would be mock/development behavior,
which is expressly disallowed for this real acceptance test.  No credentials or
remote configuration can be invented by the workflow.

### Recovery Result

Blocked pending externally supplied real-provider configuration.

### Recommended Fix

Provide, through the normal secure runtime configuration mechanism (not in source
control), a valid `QWEN_API_KEY` and either:

- a reachable configured remote GPU (`REMOTE_GPU_HOST`, `REMOTE_GPU_USER`,
  `REMOTE_GPU_PROJECT_DIR`, plus any required SSH setup), or
- an explicitly enabled local real execution environment with a writable workdir
  and the required Python dependencies.

Then rerun the exact Ising prompt as `round5_ising_e2e_001` from a fresh data/runtime
directory and preserve all events, attempts, results, and failure artifacts.

### Does it block Round 5?

Yes.

