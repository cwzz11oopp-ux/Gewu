# Phase 1 Research Foundation Stability Report

Date: 2026-08-16

## Scope completed

- `/api/runs` remains the production workflow. `/api/v2/research/sessions` was not migrated or removed and remains unreferenced by the current frontend.
- Added versioned `research_constraints` input storage and a frozen `research_constraints` Artifact created before a production pipeline starts. Historic Runs remain valid because both fields have defaults and legacy `constraints` is retained in the frozen artifact.
- Added persisted, secret-free Run preflight (`POST /api/runs/{id}/preflight`). Pipeline start runs it and returns `preflight_failed` without starting workflow steps when blocking checks fail. In a real production provider configuration it sends each of Qwen and DeepSeek a minimal JSON-mode request, thereby checking URL, authentication, model resolution, timeout/transport and structured-response handling—not merely whether fields are populated. It also records dataset inspection, experiment environment and optional repository readability.
- Changed Qwen 4xx handling: 400/401/403/404 are no longer transient retry statuses. The thrown diagnostic includes provider, model, task, HTTP status, request ID and a sanitized response excerpt. 408/429/5xx remain transient retry candidates.
- Changed generic rerun cleanup to append-only retention. The old affected Artifact set is recorded as superseded in an Event and is not removed from the Run. The event is now persisted after branch state is saved, preventing a stale in-memory Run from overwriting it.
- The frontend performs preflight before starting a new Run and shows blocking component codes in the existing error banner. The API client and Run type support structured constraints/preflight.

## Explicitly not implemented

No Phase 2 functionality was added: fixed four Ideas, Idea v1-v3, result analyzer, baseline reproduction gate, small/full protocol, dynamic seeds, 3+3 recovery, PaperProfile/full text reading, task-Skill refactor, and report structure redesign are unchanged.

## Compatibility

Dataset inspection, Bundle/Harness, Local/SSH runners, Artifact/Event persistence, static Skill routing, report download, experiment termination and old Run loading are preserved. No historic run data was changed.

## Acceptance continuation (2026-08-16)

- Full backend regression: `python -m pytest tests/backend -q -m "not gpu"` → **549 passed, 2 skipped** in 106.83s.
- Focused Phase 1 backend: `test_phase1_foundation.py`, the append-only rerun regression, and `test_llm_provider.py` → **19 passed**. This covers provider and dataset preflight blocking, real sanitized HTTP 400 detail, structured provider admission, and append-only retention.
- Frontend Phase 1 contract checks: `pnpm --dir frontend exec node --test tests/phase1-foundation.test.mjs` → **4 passed**: blocking preflight prevents `startPipeline`, passed preflight starts it, provider codes/details reach the user-facing error path, and legacy Run fields remain optional/loadable.
- Frontend production build: `pnpm --dir frontend run build` → **passed**.
- Static propagation audit confirms one `{artifact_id, schema_version: 1}` reference is carried in the Plan and Plan Candidate, Experiment Task, repair candidate evidence, DeepSeek Plan Review/revision context, and result Review context. They all derive from `run.research_constraints_artifact_id`; no Repair or Review gap remains.

### Existing frontend UI-contract failures (13; no Phase 1-caused failure)

| Classification | Count | Tests |
| --- | ---: | --- |
| Phase 1 related | 0 | None. The dedicated Phase 1 contract suite passes 4/4. |
| Deprecated V2 / historic UI expectation | 3 | `frontend routes root and /v2 to V2 while preserving /legacy`; `V2 exposes Greenfield and Repository Research…`; `V2 Research Workspace…`. They require the removed `V2BetaPage.tsx` and V2 root routing, outside the production `/api/runs` path. |
| Other historic UI expectation | 10 | `create research resets…`; `edited topic…`; `run-changing entry points…`; `step API calls…`; `research and literature cards…`; `running-state banner…`; `stopped runs…`; `manual experiment and report controls…`; `pipeline is a full-width…`; `experiment settings label CUDA…`. These are brittle, exact source/layout/string assertions that predate this Phase 1 work (including the pre-existing GitHub draft field). |

The 13 failures are retained as historical test debt; they were neither masked nor changed during this acceptance pass.

## Verification

- `python -m compileall -q backend`: passed.
- `pnpm --dir frontend run build`: passed.
- Focused backend: `test_end_to_end_dev_mode.py` passed (2); `test_workflow_orchestrator.py` + `test_llm_provider.py` passed after updating the expected non-retry 400 behavior (25).
- The complete and focused results are recorded above; the existing static UI-contract suite remains at 22 passed / 13 failed, classified above.

## Safety confirmation

No real research Run, real model request, real literature request, training E2E, historic-data mutation, or Git commit was performed.
