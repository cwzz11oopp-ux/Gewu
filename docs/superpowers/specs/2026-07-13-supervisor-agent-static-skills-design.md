# Supervisor Agent and Static Skill Routing Design

## Goal

Introduce a first-class `SupervisorAgent` that owns deterministic Agent and Skill delegation while keeping `WorkflowEngine` responsible for execution, persistence, reruns, and artifact locks.

## Responsibilities

### SupervisorAgent

- Map every workflow step to exactly one Agent role.
- Map every workflow step to a fixed ordered Skill set.
- Load bounded Skill instructions before execution.
- Reject unknown steps and missing Skills before artifacts are created.
- Produce an auditable delegation record containing the Agent role, Skill IDs, truncation state, and instruction size.
- Validate that the Engine branch executing a step matches the assigned Agent role.

The Supervisor is deterministic and does not call the LLM. Scientific content remains the responsibility of the specialized Agents. This prevents model output from changing workflow state or bypassing competition guards.

### WorkflowEngine

- Fetch and persist `RunRecord` state.
- Ask `SupervisorAgent` for a delegation before each step.
- Execute the assigned specialized Agent or Provider operation.
- Store artifacts and events.
- Handle reruns, locked outputs, and user hypothesis selection.

## Static Assignments

| Step | Agent role | Skills |
| --- | --- | --- |
| `problem_understanding` | `research` | `idea-discovery`, `research-refine` |
| `knowledge_integration` | `research` | `research-lit` |
| `hypothesis_generation` | `hypothesis` | `idea-creator`, `novelty-check` |
| `evidence_reasoning` | `critic` | `research-review` |
| `research_plan` | `planning` | `experiment-plan`, `ablation-planner` |
| `experiment_task` | `experiment` | `experiment-bridge` |
| `experiment_run_analysis` | `experiment` | `run-experiment`, `monitor-experiment`, `analyze-results`, `experiment-audit` |
| `feedback_revision` | `critic` | `result-to-claim`, `research-review` |
| `report_export` | `writer` | `paper-plan`, `paper-writing`, `paper-claim-audit` |

Runtime tag scoring and context-dependent supplemental Skill selection are removed. Provider-specific execution behavior remains selected by explicit provider configuration, not Skill matching.

## Audit Contract

Every executed workflow event includes one delegation tool-call record:

```json
{
  "provider": "supervisor_agent",
  "method": "delegate",
  "routing_mode": "static",
  "agent_id": "planning",
  "agent": "Planning Skill",
  "skills": ["experiment-plan", "ablation-planner"],
  "truncated": [],
  "instruction_characters": 1234
}
```

## `.agents` Directory

The repository's `.agents` directory is currently empty and unreferenced by application code. It is reserved for external agent-tool configuration and is not used as the runtime source for backend Agent classes. Runtime Agent implementations remain under `backend/app/agents/`, while research Skill instructions remain under `skills/`.

## Compatibility

- Existing API routes continue to call `WorkflowEngine`.
- Existing workflow step IDs and artifacts remain unchanged.
- `SkillCatalog` may remain available for inspection, but it is not consulted during runtime delegation.
- Existing dirty experiment-provider changes are left untouched.

## Verification

- Unit tests cover every static step assignment.
- Unit tests prove context cannot alter selected Skills.
- Unit tests prove missing Skills fail before artifact creation.
- Workflow tests verify Supervisor delegation appears in event traces.
- Full backend tests guard API and artifact behavior.
