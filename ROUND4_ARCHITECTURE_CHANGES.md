# Round 4 Architecture Changes

## Scope and non-goals

This is a minimal P1 responsibility refactor in the Round 4 clean baseline.  It
does not alter API contracts, frontend code, database/history, workflow ordering,
artifact schemas, provider selection, or experiment/recovery limits.  No original
project file, validation-history file, or existing ZIP was modified, and no Git
commit was created.

## Changed files

| File | Change | Problem resolved |
| --- | --- | --- |
| `backend/app/agents/supervisor.py` | Removed `SkillLoader` dependency and `Delegation.instructions`; `delegate` now returns only route identity, Skill IDs, and trace metadata identifying SkillRuntime as prompt source. | Eliminates the unused second Skill read/render and makes Supervisor a router/validator rather than a prompt builder. |
| `backend/app/workflow/engine.py` | Constructs Supervisor with `SkillRegistry` only. | Makes the production assembly match the new responsibility boundary. |
| `backend/app/main.py` | Updates app/reload Supervisor construction. | Keeps production dependency wiring compatible with the narrow Supervisor role. |
| `backend/app/agents/experiment.py` | Replaced the long Agent-local behavioral bundle prompt with a compact JSON transport/runtime-input instruction.  Dynamic locked-dataset facts remain appended at runtime. | Skill becomes the behavior source; Agent keeps operation schema, transport representation, and current runtime facts. |
| `skills/experiment-implementation/SKILL.md` | States the exact smallest-safe-source-change repair policy. | Moves repair behavior to the assigned Skill. |
| `tests/backend/test_supervisor_agent.py` | Updates construction and adds a no-loader/no-prompt-bundle routing test. | Proves Supervisor cannot become a second Skill prompt source. |
| `tests/backend/test_experiment_code.py` | Loads the assigned Skill in repair test and adds behavioral prompt-provenance coverage. | Proves Skill text reaches the Agent provider once. |
| `tests/backend/test_workflow_engine.py`, `tests/backend/test_research_wiki.py` | Updates legacy Supervisor construction. | Preserves existing observable behavior under the reduced dependency surface. |

## Logic removed

- `SupervisorAgent.delegate` no longer calls `SkillLoader.load_many`.
- It no longer joins `SKILL.md` text, emits truncated-Skill metadata, or carries an
  unused `Delegation.instructions` field.
- `ExperimentAgent.generate_bundle` no longer embeds the long domain execution,
  dataset, GPU, progress, smoke-test, and repair behavior prompt that duplicated
  `experiment-implementation/SKILL.md`.

## Logic moved to Skill

The behavioral rule "make the smallest safe source change" is now explicitly in
`skills/experiment-implementation/SKILL.md`.  The existing Skill already owns the
remainder of bundle behavior: provider-neutral implementation, dataset handling,
progress events, smoke protocol, validation response, and scientific-contract
preservation.

## Logic retained in Engine / Agent and why

- Engine retains generic lock/cancel/state transition, artifact/event persistence,
  validation/revision loop, tool authorization invocation, and exception handling.
- Engine retains dynamic dataset binding, evidence checkpoints, and recovery
  execution because these use live run state, provider calls, and durable artifact
  compatibility; they are not static Agent methodology.
- Workflow retains order, pause/stop/restart, retry routing, and termination.
- Agent retains LLM operation name, structured input, schema hint, JSON transport
  form, and live locked-dataset facts.  These are machine/runtime constraints,
  validated independently by contracts.
- Contract/Pydantic/normalizers remain unchanged; structural validation is not a
  duplicate behavioral prompt.

## Behavior change assessment

Expected behavior is unchanged except prompt provenance:

1. Each normal step now has one model-visible Skill bundle: `SkillRuntime.prepare`.
2. Trace metadata still records Supervisor routing and Runtime Skill invocation.
3. Experiment behavior comes from the same assigned Skill text, while the Agent
   still supplies dynamic `dataset_card` and locked `DATA_ROOT` facts.
4. State writes, retry limits, recovery routing, output schemas, and tool
   authorization are unchanged.

Deferred P2 items are recorded in `ROUND4_ARCHITECTURE_AUDIT.md`; none were changed
because they require an explicit behavior decision rather than simple deduplication.

