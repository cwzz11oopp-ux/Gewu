# Sprint Checkpoint 3 — Live AI Scientist E2E

Status: **validated on 2026-08-11**. This checkpoint stops before official LangGraph migration.

## Live capability probe

The probe exercised `ModelGateway -> LegacyQwenAdapter -> configured Qwen model -> strict structured output`.

| Check | Result |
| --- | --- |
| Provider | `qwen` |
| Configured / actual model | `qwen-max` / `qwen-max` |
| HTTP request | HTTP 200 |
| Structured output | `{"probe_status":"QWEN_LIVE_PROBE_OK","schema_echo":7}` |
| Schema validation | Passed |
| Model metadata | task `v2.capability_probe`, route `general`, model `qwen-max` |
| Latency | request 1.066 s; end-to-end 1.072 s |
| Retry / fallback | none / false |
| Credential exposure | no credential value in logs, API responses, or checkpoint artifacts |

## Live two-iteration run

- Session: `research_936ac26929a4`
- Research question: Can a minimal decision-rule calibration improve accuracy on the locked threshold dataset, and can an ablation distinguish calibration from protocol drift?
- Ideation: live Qwen generated three accepted branches; no branch identifier or proposal was pre-seeded by the test.
- Selected branch: `branch_5b982128ecb1`, proposing a global threshold change from 0.5 to 0.2.
- First Controller decision: `RUN_EXPERIMENT` (best-first score 0.583).
- First Planner trace: inspected `test_model.py` and `model.py`, edited only `model.py`, changed the threshold from 0.5 to 0.2, and committed `52cdf4fb8fb9e5d5056f506f9f42e333bb8ba4b8`.
- First result: accuracy 0.8 -> 1.0; static validation, smoke, formal experiment, audit, and protocol compatibility passed.
- Critic effect: it supported the measured gain but rejected a fully isolated mechanism claim and requested `RUN_ABLATION` / `RUN_REPLICATION`.
- Second Controller decision: `RUN_ABLATION` (best-first score 0.656), selected by Controller from Frontier, Belief, Budget, and Critic advice.
- Second Planner trace: inspected the same two source files, reverted the threshold mechanism in `model.py`, and committed `e29a83465a99752d5c6cebf4ee26ef0069eabc3e`.
- Second result: accuracy returned from 1.0 to 0.8 under the same protocol fingerprint, supporting a mechanism contribution while not proving that 0.2 is uniquely optimal.

The sanitized runtime report is stored locally at `backend/data/checkpoint3-live/20260811T085142Z/live-e2e-report.json`. Runtime data is ignored by Git.

## V2 Beta UI

The independent `/v2` entry point exposes:

- research question, repository, dataset, and budget setup;
- create/start, refresh, and stop lifecycle controls backed by versioned V2 APIs;
- current session state and best audited result;
- ResearchFrontier branch cards;
- Controller decision and expected information gain;
- repository experiment timeline with base/code commit trace;
- supported and unsupported findings;
- collapsed persisted scientific events for developers.

The existing V1 UI and `/api/runs` semantics are unchanged.

## Verification

- Backend: `447 passed, 3 skipped`.
- Frontend contract: `33 passed`.
- Frontend production build: passed.
- Browser QA: desktop 1280x720 and mobile 390x844; no horizontal overflow and no console warnings/errors.

## Remaining limits

- The live fixture has only five deterministic examples, so it establishes wiring and scientific control flow, not external validity.
- The ablation is a full revert; a partial threshold sweep is still needed to test whether 0.2 is uniquely meaningful.
- Worktree cleanup recorded a recoverable warning after successful experiments because result files remained in the temporary worktree. Metrics, audits, and commits were preserved.
- No official LangGraph migration, production hardening, PostgreSQL, Docker, embeddings, DeepSeek, or GPT work is included.
