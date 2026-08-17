# Round 4 Architecture Audit

Scope: this audit examines only `D:\竞赛_clean_round4_ready`.  It does not start a
research run, change the API/data model/frontend, modify the original project, or
delete files.  Evidence below is from the production call path, not filename-based
inference.

## 1. Current Execution Path

```
POST /api/runs/{run_id}/start
  -> WorkflowOrchestrator.start() / _drive()
  -> WorkflowOrchestrator._next_step() selects the next durable step
  -> WorkflowEngine.run_step() owns lock and generic step-state transitions
  -> WorkflowEngine._run_step()
  -> SupervisorAgent.delegate() maps step -> agent identity + Skill IDs
  -> SkillRuntime.prepare() loads complete Skill bodies, renders one instruction
     bundle, intersects declared/agent/registered/configured tools, and records hashes
  -> typed Agent calls LLM/provider or a deterministic service
  -> WorkflowEngine validates, persists an immutable artifact/event, then returns
  -> WorkflowOrchestrator selects the following step from artifact lineage/state
```

Concrete entry points: `backend/app/api/runs.py:300-344`,
`backend/app/workflow/orchestrator.py:25-182`, and
`backend/app/workflow/engine.py:170-253`.  The automatic path is durable: the
orchestrator reads repository state and artifacts on each transition rather than
holding an in-memory research result as authority.

Skill timing and prompt provenance in the pre-change baseline:

1. `SupervisorAgent.delegate` reads mapped Skills via `SkillLoader.load_many`
   (`backend/app/agents/supervisor.py:110-142`).
2. `WorkflowEngine._run_step` immediately calls `SkillRuntime.prepare`
   (`backend/app/workflow/engine.py:245-252`).
3. `SkillRuntime.prepare` reads the same IDs with `load_complete`, renders them,
   authorizes tools, and supplies `package.instructions`
   (`backend/app/workflow/skill_runtime.py:175-234`).
4. The Engine passes only `package.instructions` to typed Agents; the earlier
   `Delegation.instructions` is not read by the production Engine.

Thus only the SkillRuntime bundle reaches the model in this path, but the
Supervisor used to perform a second, truncated load and render of the same content.

## 2. Engine Responsibilities

Actual generic execution responsibilities retained in `WorkflowEngine`:

- per-run locking, cancellation propagation, generic step `running/failed/completed`
  state transitions (`engine.py:166-213`);
- dependency injection/assembly of providers, typed Agents, tool registry, Skill
  Runtime, and Supervisor (`engine.py:101-164`);
- required-input gates, artifact persistence, event/audit traces, output validation,
  and bounded revision protocol (`engine.py:217-253`, `2955-3099`, `3351-3388`);
- runtime/tool enforcement through `RuntimePackage` and generic provider failure
  handling (`engine.py:2932-2954`);
- immutable dataset contract binding, experiment lineage, and report-readiness
  gates.  These are runtime/data-integrity policies, not an Agent's research method.

The Engine also contains step-specific adapters for historical artifact shapes,
candidate evidence checkpoints, dataset binding, and experiment repair.  These are
hidden coupling to the current research domain and should be separated only in a
future, tested extraction; they are not safely removable in this Round because they
also own persisted-artifact compatibility and recovery.

## 3. Skill Responsibilities

`SkillRegistry` is the authoritative static route from workflow step to Agent ID
and required/conditional Skill IDs (`backend/app/workflow/skills.py:25-82`).
`SkillLoader` parses `SKILL.md`; `SkillRuntime` loads complete bodies, produces the
model-visible bundle, and derives allowed tools from four-way intersection
(`skill_runtime.py:147-234`).

The principal Skills carry Agent-specific method and semantic behavior:

- `skills/problem-framing/SKILL.md`: bounded problem-framing method;
- `skills/idea-creator/SKILL.md` and `skills/hypothesis-evidence/SKILL.md`:
  hypothesis method and evidence requirements;
- `skills/evidence-recovery/SKILL.md`: claim-evidence policy and targeted-retrieval
  behavior;
- `skills/experiment-implementation/SKILL.md`: bundle-generation and harness
  behavior;
- `skills/experiment-diagnosis/SKILL.md`: bounded diagnostic/repair protocol.

Skill documents define behavioral/semantic rules.  They do not replace Pydantic or
normalization contracts, which remain the machine-verifiable structural boundary.

## 4. Agent Responsibilities

Typed Agent classes assemble an operation name, structured inputs, and a schema for
the LLM/provider.  Examples are `ResearchAgent.structure_problem`
(`agents/research.py:9-31`), `IdeaAgent.generate` (`agents/idea.py:11-66`),
`CriticAgent.evidence_reasoning` (`agents/critic.py:9-59`), and
`ExperimentAgent.generate_bundle` (`agents/experiment.py:101-241`).

This is appropriate for stable operation identity and machine output shape.  The
audit found that `ExperimentAgent.generate_bundle` also repeats a large portion of
the experiment-implementation behavior in its local `output_contract` string
(`agents/experiment.py:122-171`), which overlaps `skills/experiment-implementation/
SKILL.md`.  The same method must still add dynamic, runtime-owned facts such as the
selected dataset contract and `DATA_ROOT`; those facts cannot live statically in a
Skill.

## 5. Workflow/Supervisor Responsibilities

`WorkflowOrchestrator` owns sequencing, pause/stop, restart recovery and completion
from artifact lineage (`orchestrator.py:25-182`).  It does not choose a research
method or overwrite an Agent's substantive output.

`SupervisorAgent` owns route identity checks, structural acceptance/revision limits,
and optional independent semantic review (`agents/supervisor.py:102-237`).  Its
`_REQUIRED_FIELDS` and reviewer rubrics are contracts/quality gates, not a second
research-method prompt.  The Supervisor should route only; it should not separately
load or render Skill text.

## 6. Prompt Sources

| Source | Reaches model? | Proper ownership |
| --- | --- | --- |
| `SKILL.md` rendered by `SkillRuntime.prepare` | Yes | Agent behavior/method/tool declaration |
| Typed Agent schema hint | Yes | machine-readable output structure |
| Dynamic Engine context (dataset binding, revision issues, current traceback) | Yes | runtime facts and generic recovery feedback |
| `SupervisorAgent.delegate` pre-change instruction bundle | No | redundant; P1 removal target |
| Agent-local behavioral `output_contract` | Yes | partially redundant with Skill; P1 reduction target |

## 7. Duplicate Logic Findings

| Priority | Finding | Evidence | Effect | Recommended action |
| --- | --- | --- | --- | --- |
| P1 | Skill content is loaded/rendered twice for each normal delegation. | `supervisor.py:110-142`; `engine.py:245-252`; `skill_runtime.py:175-234` | Two different budgets/load modes can drift; Supervisor bundle is unused by the model. | Make Supervisor route IDs only; SkillRuntime remains the only prompt assembler. |
| P1 | Experiment bundle behavioral protocol exists both in Skill and a long Agent-local prompt. | `skills/experiment-implementation/SKILL.md`; `agents/experiment.py:122-171` | A Skill edit can silently disagree with runtime prompt text. | Keep only transport/schema and dynamic runtime facts in Agent; retain behavior in Skill. |
| P2 | Dataset-selection method appears in planner local `_DATASET_CONTRACT` as well as plan/experiment Skills. | `agents/planner.py:63-86`; `skills/experiment-plan/SKILL.md` | Current behavior is stable, but ownership is mixed. | Defer: it combines live dataset options with behavior and needs a dedicated dynamic-context abstraction. |
| P2 | Engine has extensive evidence/recovery rules alongside evidence-recovery Skill. | `engine.py:2314-2469`; `skills/evidence-recovery/SKILL.md` | Coupling is high, but Engine portion persists recovery state and calls real providers. | Keep in this round; document Engine as recovery executor and Skill as semantic decision policy. |

No P0 conflicting runtime behavior was established: the pre-change Supervisor
instructions do not reach Agents, and the complete SkillRuntime package is the one
recorded in the trace.  The P1s are definition/provenance drift risks rather than a
known inconsistent output today.

## 8. Conflicting Logic Findings

No P0 found.  The likely conflict surface is future drift between
`ExperimentAgent.output_contract` and `experiment-implementation/SKILL.md`, or
between Supervisor's truncated `load_many` bundle and SkillRuntime's complete bundle.
The minimal refactor removes both duplicate behavioral sources rather than choosing
between competing outputs at runtime.

## 9. Hidden Coupling

- The Engine reaches typed Agents directly for every step; route identity is checked
  twice (`require_agent` and `SkillRuntime.prepare`).  The checks are complementary:
  one detects wrong branch selection and one protects runtime authorization.
- `SkillRegistry` supplies conditional Skills from `state`, but
  `WorkflowEngine._skill_state` presently returns `{}` (`engine.py:2927-2930`).
  As a result conditions such as `monitoring_enabled` and
  `plan_refinement_enabled` are not activated on the normal Engine path.  This is a
  P2 routing gap, not modified here because defining a new state projection changes
  workflow behavior.
- Evidence recovery in Engine persists checkpoints and retrieval artifacts while the
  Skill specifies the critic's semantic rule.  This must remain coordinated through
  existing artifact contracts.

## 10. State Ownership

| State/artifact | Owner that writes it | Read/coordination owner |
| --- | --- | --- |
| workflow status/current step/stop flag | `Repository` through Orchestrator/Engine | Orchestrator |
| problem, evidence, hypothesis, reasoning, plan, task, result, revision, report | Engine via `Repository.add_artifact` | Orchestrator lineage + typed Agents |
| candidate/retrieval/recovery checkpoints | Engine | Engine recovery path |
| runtime/provider metadata | provider/runtime + Repository event traces | Engine/API |
| v2 research profile/session state | separate `v2_*` service path | v2 session service |

No normal Agent mutates repository state directly.  Agents return candidate values;
Engine validates and writes one durable artifact.  This is the intended single
writer boundary.

## 11. Retry / Recovery Ownership

| Failure scope | Current owner | Bound |
| --- | --- | --- |
| provider model call | LLM provider | provider configuration |
| candidate structural/semantic revision | Engine + Supervisor | `revision_limit` |
| invalid idea-review JSON | Engine format retry | 3 format attempts |
| targeted retrieval | Engine executes; evidence Skill supplies decision semantics | 2 rounds |
| experiment diagnosis/repair | Engine executes; diagnostic Skill constrains repair method | bounded repair cycles/revisions |
| pipeline interruption/restart | Orchestrator + Repository | durable state reconciliation |

The boundaries are functional but the Engine is the central coordinator for several
domain recoveries.  Removing that code would alter durable recovery behavior, so it
is not a P0/P1 deletion candidate.

## 12. Recommended Single Source of Truth

- Skill behavioral rules: `skills/<id>/SKILL.md`, loaded once by `SkillRuntime`.
- Step-to-Agent/Skill route: `SkillRegistry`.
- Agent operation identity/input/schema: typed Agent class.
- Tool authorization: `SkillRuntime` plus `AGENT_TOOL_POLICY` and registered tools.
- State, sequence, and durable recovery: Repository + Orchestrator + generic Engine
  execution protocol.
- Machine data shape: Pydantic/normalizers/contracts.

## 13. Minimal Refactor Plan

1. Remove Skill loading and instruction rendering from `SupervisorAgent.delegate`;
   retain its route assertion and trace metadata.
2. Make `SkillRuntime.prepare` the only source of model-visible Skill instructions.
3. Reduce `ExperimentAgent` local instruction text to dynamic runtime facts and
   machine transport requirements; preserve behavior in its assigned Skill.
4. Add observable tests proving: one Skill prompt source, no duplicate render, the
   Experiment Skill governs the behavioral instruction, and state/recovery behavior
   remains unchanged.
5. Run baseline imports, focused tests, full backend tests, Skill loading/runtime
   checks, and frontend build.

## 14. Risk Assessment

Low-to-medium.  The first P1 removes an unused bundle and has an explicit trace
contract.  The second changes prompt provenance, so tests must assert the complete
Skill text continues to reach the provider and dynamic dataset facts remain present.
No API, database, frontend, workflow order, artifact schema, or historical run is
to be changed.  P2 conditional state routing and broad Engine extraction are
explicitly deferred.

## Implementation Update

The P1 plan above was applied after this pre-change audit.  Supervisor now routes
without a loader or instruction bundle; SkillRuntime is the sole model-visible Skill
assembler.  ExperimentAgent now retains only JSON transport and live runtime facts,
while the assigned experiment-implementation Skill owns behavioral rules.  See
`ROUND4_ARCHITECTURE_CHANGES.md` and `ROUND4_VALIDATION_REPORT.md` for exact edits
and verification results.

## Duplicate Logic Matrix

| Rule / Responsibility | Engine | Agent | Skill | Workflow | Contract | Assessment |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| Agent execution/lifecycle | yes |  |  | yes |  | separated; Engine executes and Workflow sequences |
| Step-to-Agent/Skill routing | yes (consumes) |  | yes (registry) | yes (step selection) |  | review boundary, no conflict |
| Research methodology | context only | operation adapter | yes |  |  | Skill source is correct |
| Experiment implementation behavior | dynamic facts | yes | yes |  | yes | P1 duplicate; reduce Agent prompt |
| Output JSON shape |  | schema hint | behavior formatting |  | yes | acceptable layered constraint |
| Skill instruction rendering | yes (consumes) |  | yes | yes (Supervisor pre-change) |  | P1 duplicate; remove Supervisor rendering |
| Retry/recovery | yes |  | semantic constraints | yes |  | split by failure scope; P2 coupling only |
| Durable state writes | yes |  |  | yes | yes | Engine/Repository single-writer boundary |
