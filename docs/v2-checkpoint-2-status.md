# AI Scientist V2 Sprint Checkpoint 2

> Verified workspace snapshot: 2026-08-11. This checkpoint stops before official LangGraph migration and production hardening.

## Outcome

The V2 Scientific Core is callable through a versioned FastAPI ResearchSession lifecycle. The autonomous repository path starts from scientific intent, inspects repository sources, produces a schema-validated implementation plan, validates paths, edits an isolated Git worktree, and runs static, smoke, and formal experiment phases. A structured Critic now advises the Controller after experimental evidence, while the Controller remains the sole final action selector.

V1 `/api/runs`, `WorkflowEngine`, and the frontend were not modified.

## V2 API

Base path: `/api/v2/research/sessions`

- `POST /api/v2/research/sessions` — create a durable session;
- `POST /api/v2/research/sessions/{session_id}/start` — select the first lifecycle action;
- `POST /api/v2/research/sessions/{session_id}/continue` — accept either an audited baseline or an `ExperimentRecord`;
- `POST /api/v2/research/sessions/{session_id}/stop` — stop idempotently with a reason;
- `GET /api/v2/research/sessions/{session_id}/state`;
- `GET /api/v2/research/sessions/{session_id}/frontier`;
- `GET /api/v2/research/sessions/{session_id}/experiments`;
- `GET /api/v2/research/sessions/{session_id}/evidence`.

Starting without a baseline produces `REPRODUCE_BASELINE`. Continuing with a validated, audited baseline triggers literature intake and schema/falsifiability/minimal-experiment-gated multi-branch ideation. Missing Qwen credentials return an explicit `503 QWEN_API_KEY_MISSING`; no branch is fabricated.

## Qwen live status

The configured provider is Qwen (`qwen3.7-plus`), but `QWEN_API_KEY` is absent in the current environment. A live branch-generation request was therefore not sent. Provider wiring, structured schemas, API behavior, and credential-block behavior are covered by contract tests, but there is no real Qwen-generated branch to report from this machine.

## General Repository Planner trace

The integration test starts with a ResearchAction and hypothesis/mechanism only; it does not provide edited file contents.

```text
base commit
  -> enumerate 3 tracked files
  -> model inspection plan selects model.py
  -> read model.py at the exact base commit
  -> model implementation plan changes DEFAULT_THRESHOLD 0.5 -> 0.0
  -> validate tracked path, edit count, source size, edit size, and forbidden paths
  -> isolated worktree edit
  -> py_compile static validation
  -> pytest smoke test
  -> formal deterministic experiment with two protocol-locked seeds
  -> accuracy 0.8 -> 1.0
  -> audited ExperimentRecord and new code commit
```

The original pre-approved-content contract remains a low-level fixture/planner-output execution primitive. `GeneralRepositoryExperimentContract` is the caller-facing autonomous path and contains no implementation file contents.

## Critic-to-Controller decision trace

The Critic schema contains supported claims, unsupported claims, possible mechanism, alternative explanation, methodological issues, open information gaps, and recommended next actions.

In the integration test, compatible audited evidence raises accuracy from 0.5 to 0.75 and moves the branch to `PROMISING`. The Critic supports only the locked-protocol gain, marks the mechanism as unisolated, and recommends `[RUN_ABLATION, RUN_REPLICATION]`. `CriticDecisionService` validates those suggestions against branch state and consumes a model-call budget unit. The `ResearchController`, not the Critic, then selects `RUN_ABLATION` from the updated Frontier under the remaining budget.

## Verification

Executed with the repository virtual environment:

```text
.venv\Scripts\python.exe -m pytest tests/backend -q
445 passed, 3 skipped in 88.23s
```

The host still prints a non-failing Anaconda `RequestsDependencyWarning`; production environment hardening is intentionally outside this checkpoint.

```text
.venv\Scripts\python.exe -m pytest tests/backend -k "v2" -q
32 passed, 416 deselected in 14.80s

.venv\Scripts\python.exe -m pytest tests/backend/test_api.py tests/backend/test_workflow_engine.py tests/backend/test_workflow_orchestrator.py tests/backend/test_end_to_end_dev_mode.py -q
113 passed in 72.63s
```

## Remaining blockers and deferred work

- Live Qwen branch generation and live Qwen Critic/Planner proof require `QWEN_API_KEY`.
- Official LangGraph migration is deliberately not started.
- Transactional storage, cross-process concurrency, sandbox hardening, PostgreSQL, Docker, embeddings, additional model providers, and frontend migration are deliberately deferred.
