# Supervisor and SkillRuntime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the incomplete flat Skill routing with a deterministic Supervisor and auditable SkillRuntime that assigns the approved Agent, atomic Skills, and authorized tools to every workflow step.

**Architecture:** `SkillRegistry` owns immutable step assignments, `SkillLoader` parses complete Skill metadata, `SkillRuntime` computes tool authorization and produces an instruction/audit package, and `SupervisorAgent` validates outputs and controls revision budgets. `WorkflowEngine` consumes a single `Delegation` per step and records it in run events.

**Tech Stack:** Python 3.12, FastAPI, Pydantic 2, pytest, Markdown `SKILL.md` files, JSON event records.

## Global Constraints

- Deployment remains GitHub local deployment + local GPU + generic SSH remote GPU.
- Skill routing is deterministic; run context cannot silently replace mandatory Skills.
- Composite orchestration Skills are excluded from ordinary workflow steps.
- Supervisor must not receive literature-network, subprocess, SSH, or report-writing tools.
- Actual tools are the intersection of Skill declarations, Agent permissions, registered tools, and configured tools.
- Content revision is limited to 2 attempts; experiment diagnosis is limited to 3 attempts.
- Do not revert pre-existing user changes in the dirty worktree.

---

## File Structure

Create:

- `backend/app/workflow/skill_runtime.py`: tool authorization, instruction packages, and output contracts.
- `backend/app/agents/idea.py`: approved IdeaAgent name with compatibility export.
- `backend/app/agents/reviewer.py`: isolated semantic review with a fresh model context.
- `skills/ai-scientist-supervisor/SKILL.md`: Supervisor-only orchestration protocol.
- `skills/problem-framing/SKILL.md`: atomic problem-understanding protocol.
- `skills/experiment-implementation/SKILL.md`: atomic experiment-package construction protocol.
- `skills/competition-report/SKILL.md`: competition report protocol.
- `tests/backend/test_skill_runtime.py`: SkillRuntime unit tests.
- `tests/backend/test_reviewer_agent.py`: semantic review isolation and rubric tests.

Modify:

- `backend/app/workflow/skills.py`: approved static assignments and complete frontmatter parsing.
- `backend/app/agents/supervisor.py`: delegation, validation, and retry budgets.
- `backend/app/agents/hypothesis.py`: IdeaAgent compatibility.
- `backend/app/workflow/engine.py`: use Supervisor delegation consistently and record audit data.
- `backend/app/main.py`: construct one shared SkillRuntime/Supervisor graph.
- `tests/backend/test_supervisor_agent.py`: approved route and retry tests.
- `tests/backend/test_workflow_skills.py`: metadata and route tests.
- `tests/backend/test_workflow_engine.py`: delegation event and loader ownership tests.
- `.gitignore`: stop ignoring runtime Skills while retaining runtime data ignores.
- `docs/agent_architecture.md`: document Supervisor/SkillRuntime runtime behavior.

### Task 1: Establish the Approved Static Assignment Contract

**Files:**
- Modify: `tests/backend/test_supervisor_agent.py`
- Modify: `tests/backend/test_workflow_skills.py`
- Modify: `backend/app/workflow/skills.py`
- Modify: `backend/app/agents/supervisor.py`

**Interfaces:**
- Produces: `StepAssignment(agent_id: str, primary_skills: tuple[str, ...], capability_skills: tuple[str, ...])`.
- Produces: `SkillRegistry.assignment_for(step_id: str) -> StepAssignment`.
- Produces: `SkillRegistry.conditional_skills_for(step_id: str, state: Mapping[str, Any]) -> tuple[str, ...]`.
- Consumes: workflow step IDs from `backend/app/workflow/steps.py`.

- [ ] **Step 1: Replace obsolete route expectations with approved expectations**

```python
EXPECTED_ASSIGNMENTS = {
    "problem_understanding": ("research", ("problem-framing",)),
    "knowledge_integration": ("research", ("research-lit", "research-wiki")),
    "hypothesis_generation": ("idea", ("idea-creator",)),
    "evidence_reasoning": ("critic", ("novelty-check", "research-review")),
    "research_plan": ("planning", ("research-refine", "experiment-plan")),
    "experiment_task": ("experiment", ("experiment-implementation",)),
    "experiment_run_analysis": (
        "experiment",
        ("run-experiment", "analyze-results", "experiment-audit"),
    ),
    "feedback_revision": ("critic", ("result-to-claim",)),
    "report_export": ("writer", ("competition-report",)),
}
```

Add assertions that `experiment-bridge`, `paper-writing`, `ablation-planner`, and `monitor-experiment` are not unconditional primary Skills.

Add conditional-route tests:

```python
assert registry.conditional_skills_for(
    "feedback_revision", {"experiment_verdict": "partial"}
) == ("ablation-planner",)
assert registry.conditional_skills_for(
    "experiment_run_analysis", {"monitoring_enabled": True}
) == ("monitor-experiment",)
assert registry.conditional_skills_for(
    "feedback_revision", {"experiment_verdict": "supported"}
) == ()
```

- [ ] **Step 2: Run the route tests and verify red state**

Run: `.\.venv\Scripts\python.exe -m pytest tests/backend/test_supervisor_agent.py tests/backend/test_workflow_skills.py -q`

Expected: FAIL because `_ROUTES` and `_AGENT_ROUTES` still contain obsolete assignments.

- [ ] **Step 3: Implement immutable step assignments**

```python
@dataclass(frozen=True)
class StepAssignment:
    agent_id: str
    primary_skills: tuple[str, ...]
    capability_skills: tuple[str, ...] = ()

    @property
    def skill_ids(self) -> tuple[str, ...]:
        return self.primary_skills + self.capability_skills


_ASSIGNMENTS = {
    "problem_understanding": StepAssignment("research", ("problem-framing",)),
    "knowledge_integration": StepAssignment("research", ("research-lit",), ("research-wiki",)),
    "hypothesis_generation": StepAssignment("idea", ("idea-creator",)),
    "evidence_reasoning": StepAssignment("critic", ("novelty-check",), ("research-review",)),
    "research_plan": StepAssignment("planning", ("research-refine",), ("experiment-plan",)),
    "experiment_task": StepAssignment("experiment", ("experiment-implementation",)),
    "experiment_run_analysis": StepAssignment(
        "experiment", ("run-experiment",), ("analyze-results", "experiment-audit")
    ),
    "feedback_revision": StepAssignment("critic", ("result-to-claim",)),
    "report_export": StepAssignment("writer", ("competition-report",)),
}

_CONDITIONAL_SKILLS = {
    "feedback_revision": (
        ConditionalSkill("ablation-planner", field="experiment_verdict", values=("failed", "partial")),
    ),
    "experiment_run_analysis": (
        ConditionalSkill("monitor-experiment", field="monitoring_enabled", values=(True,)),
    ),
}
```

`skills_for()` returns `assignment_for(step_id).skill_ids`. `conditional_skills_for()` evaluates only the declarative rules above and never invokes a model. Unknown steps return no Skills from `skills_for()` but raise `UNKNOWN_WORKFLOW_STEP` from Supervisor delegation.

- [ ] **Step 4: Run route tests and verify green state**

Run: `.\.venv\Scripts\python.exe -m pytest tests/backend/test_supervisor_agent.py tests/backend/test_workflow_skills.py -q`

Expected: PASS for route assertions; loader tests for newly named Skills may remain red until Task 2.

- [ ] **Step 5: Commit the route contract**

```powershell
git add backend/app/workflow/skills.py backend/app/agents/supervisor.py tests/backend/test_supervisor_agent.py tests/backend/test_workflow_skills.py
git commit -m "fix: align supervisor with approved skill routes"
```

### Task 2: Add Atomic Runtime Skills and Track Skills in Git

**Files:**
- Create: `skills/ai-scientist-supervisor/SKILL.md`
- Create: `skills/problem-framing/SKILL.md`
- Create: `skills/experiment-implementation/SKILL.md`
- Create: `skills/competition-report/SKILL.md`
- Modify: `skills/research-lit/SKILL.md`
- Modify: `skills/research-wiki/SKILL.md`
- Modify: `skills/idea-creator/SKILL.md`
- Modify: `skills/novelty-check/SKILL.md`
- Modify: `skills/research-review/SKILL.md`
- Modify: `skills/research-refine/SKILL.md`
- Modify: `skills/experiment-plan/SKILL.md`
- Modify: `skills/run-experiment/SKILL.md`
- Modify: `skills/analyze-results/SKILL.md`
- Modify: `skills/experiment-audit/SKILL.md`
- Modify: `skills/monitor-experiment/SKILL.md`
- Modify: `skills/result-to-claim/SKILL.md`
- Modify: `skills/ablation-planner/SKILL.md`
- Modify: `.gitignore`
- Test: `tests/backend/test_workflow_skills.py`

**Interfaces:**
- Consumes: Skill IDs from Task 1.
- Produces: valid frontmatter fields `name`, `description`, and `allowed-tools` for every new Skill.
- Produces: runtime Skill prompts executable by the configured Qwen Provider and application tools, with no Codex/Claude-specific orchestration.

- [ ] **Step 1: Add failing tests for required atomic Skill files**

```python
@pytest.mark.parametrize(
    "skill_id",
    ["ai-scientist-supervisor", "problem-framing", "experiment-implementation", "competition-report"],
)
def test_required_atomic_skill_is_loadable(skill_id):
    root = Path(__file__).resolve().parents[2]
    context = SkillLoader(root).load(skill_id)
    assert context.id == skill_id
    assert context.instructions
```

Add a parameterized contract for every fixed and conditional Skill. Each must explicitly declare the application tool names assigned to it. For example, `research-lit` declares `literature_search` and `search_local_literature`, `research-wiki` declares `query_wiki`, `run-experiment` declares `local_process_run` and `ssh_run`, and `competition-report` declares `render_report`.

Add prompt-portability assertions for every routed Skill. Reject `mcp__codex`, `codex-reply`, `CLAUDE.md`, `~/.claude`, `Executor — Claude`, `Vast.ai`, `Modal`, and `serverless-modal`. Also reject legacy execution names such as `Bash(*)`, `WebSearch`, `WebFetch`, and `Agent` in routed `allowed-tools`.

- [ ] **Step 2: Verify the tests fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/backend/test_workflow_skills.py::test_required_atomic_skill_is_loadable -q`

Expected: FAIL with `SKILL_NOT_FOUND` for new atomic Skills and portability/tool-contract failures for existing routed Skills.

- [ ] **Step 3: Create and migrate the routed runtime Skills**

Use this exact frontmatter pattern and write step-specific contracts in each body:

```markdown
---
name: problem-framing
description: Convert a research request into a bounded, testable problem contract.
allowed-tools: read_run, read_artifact
---

# Problem Framing

Return a structured problem with `problem_statement`, `constraints`,
`knowledge_gaps`, and `literature_queries`. Do not search literature or design
experiments in this Skill.
```

The Supervisor Skill declares only the nine app-level orchestration tools from the TDD. `experiment-implementation` declares `build_experiment_bundle` but not subprocess/SSH tools. `competition-report` declares artifact readers and `render_report` only.

Rewrite the thirteen existing routed Skills as atomic application prompts while preserving their useful research checklists and required outputs. Domain work is executed by the configured Qwen Provider through the owning Agent; isolated judgment is requested through `ReviewerAgent`, also backed by the configured Qwen Provider. Replace `CLAUDE.md`/home-directory assumptions with Run state and project settings, replace direct Web/Codex MCP calls with registered application tools, and reduce `run-experiment`/`monitor-experiment` to local GPU plus generic SSH. Do not perform blind name substitution: Qwen receives instructions, while the backend remains responsible for actual tool calls.

Routed `allowed-tools` contain only exact registered application names. Non-routed vendored Skills may retain their original provider-specific metadata because SkillRegistry cannot select them.

- [ ] **Step 4: Make runtime Skills version-controlled**

Remove the broad `skills/` ignore rule. Add explicit ignores for runtime-only nested metadata:

```gitignore
skills/.git/
skills/.agents/
```

Do not stage `backend/data`, `.venv`, uploaded papers, experiment outputs, or secrets.

- [ ] **Step 5: Run Skill file tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/backend/test_workflow_skills.py -q`

Expected: PASS.

- [ ] **Step 6: Commit atomic Skills and ignore rules**

```powershell
git add .gitignore skills tests/backend/test_workflow_skills.py
git commit -m "feat: vendor runtime skills for local clones"
```

### Task 3: Parse Skill Tool Declarations and Instruction Hashes

**Files:**
- Modify: `backend/app/workflow/skills.py`
- Modify: `tests/backend/test_workflow_skills.py`

**Interfaces:**
- Produces: `SkillContext.allowed_tools: tuple[str, ...]`.
- Produces: `SkillContext.instruction_sha256: str`.
- Produces: `_parse_skill(raw: str) -> ParsedSkill`.

- [ ] **Step 1: Write metadata parsing tests**

```python
def test_loader_parses_allowed_tools_and_instruction_hash(tmp_path):
    _write_skill(
        tmp_path,
        "demo",
        "Return a result.",
        frontmatter="allowed-tools: read_run, literature_search",
    )
    context = SkillLoader(tmp_path).load("demo")
    assert context.allowed_tools == ("read_run", "literature_search")
    assert len(context.instruction_sha256) == 64
```

Also assert identical text gives identical hashes and edited text changes the hash.

- [ ] **Step 2: Verify red state**

Run: `.\.venv\Scripts\python.exe -m pytest tests/backend/test_workflow_skills.py -q`

Expected: FAIL because `SkillContext` lacks both fields.

- [ ] **Step 3: Implement structured metadata**

```python
@dataclass(frozen=True)
class ParsedSkill:
    name: str
    description: str
    allowed_tools: tuple[str, ...]
    body: str


def _tool_names(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())
```

Compute `hashlib.sha256(body.encode("utf-8")).hexdigest()` before any instruction budgeting. Keep the current path traversal protection.

- [ ] **Step 4: Run metadata tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/backend/test_workflow_skills.py -q`

Expected: PASS.

- [ ] **Step 5: Commit Skill metadata support**

```powershell
git add backend/app/workflow/skills.py tests/backend/test_workflow_skills.py
git commit -m "feat: parse skill tools and instruction hashes"
```

### Task 4: Implement SkillRuntime Authorization and Audit Packages

**Files:**
- Create: `backend/app/workflow/skill_runtime.py`
- Create: `tests/backend/test_skill_runtime.py`

**Interfaces:**
- Produces: `ToolRegistry.register(name: str, handler: Callable[..., Any]) -> None`.
- Produces: `SkillRuntime.prepare(step_id: str, agent_id: str, configured_tools: set[str], state: Mapping[str, Any] | None = None) -> RuntimePackage`.
- Produces: `RuntimePackage.instructions`, `skill_ids`, `authorized_tools`, `omitted_sections`, and `audit`.

- [ ] **Step 1: Write failing authorization tests**

```python
def test_runtime_authorizes_only_four_way_tool_intersection(runtime):
    package = runtime.prepare(
        "knowledge_integration",
        "research",
        configured_tools={"read_run", "literature_search", "ssh_run"},
    )
    assert package.authorized_tools == ("read_run", "literature_search")
    assert "ssh_run" not in package.authorized_tools
```

Add tests that Supervisor never receives `literature_search`, `local_process_run`, or `ssh_run`, and that unregistered declared tools appear in `audit["denied_tools"]`.

Add tests that `state={"experiment_verdict": "partial"}` loads `ablation-planner`, while a supported verdict does not. Add an instruction-budget test using two Markdown headings: the retained text must end at a heading-section boundary, `omitted_sections` must name the excluded `skill_id#heading`, and no line may be cut in the middle.

- [ ] **Step 2: Verify red state**

Run: `.\.venv\Scripts\python.exe -m pytest tests/backend/test_skill_runtime.py -q`

Expected: FAIL with module import error.

- [ ] **Step 3: Implement ToolRegistry and RuntimePackage**

Define the Agent policy as exact application tool names:

```python
_AGENT_TOOLS = {
    "supervisor": {
        "read_run", "read_artifact", "load_skill", "dispatch_agent",
        "validate_artifact", "request_revision", "update_step", "append_event",
        "commit_wiki_changes",
    },
    "research": {
        "read_run", "read_artifact", "query_wiki", "search_local_literature",
        "literature_search", "propose_wiki_changes",
    },
    "idea": {"read_run", "read_artifact", "read_wiki_query_pack"},
    "critic": {"read_run", "read_artifact", "literature_search", "audit_evidence", "audit_result"},
    "planning": {"read_run", "read_artifact"},
    "experiment": {
        "read_run", "read_artifact", "build_experiment_bundle",
        "local_process_run", "ssh_run", "read_experiment_result",
    },
    "writer": {"read_run", "read_artifact", "render_report"},
}
```

Register adapters for these names around existing repository, literature, Wiki, experiment, and report services. Legacy names such as `Bash(*)`, `Read`, and `WebSearch` are retained as metadata but are not registered aliases and therefore cannot pass the intersection.

```python
@dataclass(frozen=True)
class RuntimePackage:
    step_id: str
    agent_id: str
    skill_ids: tuple[str, ...]
    instructions: str
    authorized_tools: tuple[str, ...]
    omitted_sections: tuple[str, ...]
    audit: dict[str, Any]


class SkillRuntime:
    def prepare(
        self,
        step_id: str,
        agent_id: str,
        configured_tools: set[str],
        state: Mapping[str, Any] | None = None,
    ) -> RuntimePackage:
        assignment = self.registry.assignment_for(step_id)
        if assignment.agent_id != agent_id:
            raise ValueError(f"SUPERVISOR_AGENT_MISMATCH:{step_id}")
        skill_ids = assignment.skill_ids + self.registry.conditional_skills_for(step_id, state or {})
        contexts = self.loader.load_many(list(skill_ids))
        declared = {tool for context in contexts for tool in context.allowed_tools}
        authorized = declared & self.agent_tools[agent_id] & self.tools.names() & configured_tools
        instruction_bundle = self.instruction_budget.render(contexts)
        return RuntimePackage(
            step_id=step_id,
            agent_id=agent_id,
            skill_ids=skill_ids,
            instructions=instruction_bundle.text,
            authorized_tools=tuple(sorted(authorized)),
            omitted_sections=instruction_bundle.omitted_sections,
            audit=_audit(contexts, declared, authorized, instruction_bundle.omitted_sections),
        )
```

`InstructionBudget.render()` splits each Skill body on Markdown headings and adds only complete sections that fit the configured budget. It records each excluded section as `skill_id#heading`; it never slices a section or line silently. The audit retains the SHA-256 hash of each complete, unbudgeted Skill body.

- [ ] **Step 4: Run authorization tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/backend/test_skill_runtime.py -q`

Expected: PASS.

- [ ] **Step 5: Commit SkillRuntime**

```powershell
git add backend/app/workflow/skill_runtime.py tests/backend/test_skill_runtime.py
git commit -m "feat: authorize tools through skill runtime"
```

### Task 5: Add Supervisor Output Contracts and Revision Budgets

**Files:**
- Modify: `backend/app/agents/supervisor.py`
- Create: `backend/app/agents/reviewer.py`
- Modify: `tests/backend/test_supervisor_agent.py`
- Create: `tests/backend/test_reviewer_agent.py`

**Interfaces:**
- Produces: `ValidationDecision(accepted: bool, issues: tuple[str, ...])`.
- Produces: `SupervisorAgent.validate(step_id: str, content: dict) -> ValidationDecision`.
- Produces: `SupervisorAgent.require_revision(step_id: str, attempt: int, issues: tuple[str, ...]) -> dict`.
- Produces: `ReviewerAgent.review(step_id: str, artifact_path: Path, wiki_paths: tuple[Path, ...], rubric: ReviewRubric) -> ValidationDecision`.

- [ ] **Step 1: Write failing contract and budget tests**

```python
def test_supervisor_rejects_evidence_without_reference_list(supervisor):
    decision = supervisor.validate("knowledge_integration", {"summary": "missing"})
    assert decision.accepted is False
    assert decision.issues == ("references must be a list",)


def test_supervisor_stops_content_revision_after_two_attempts(supervisor):
    with pytest.raises(ValueError, match="SUPERVISOR_REVISION_LIMIT"):
        supervisor.require_revision("knowledge_integration", 3, ("bad evidence",))
```

Add experiment-diagnosis limit coverage for attempt 4.

Add isolated Reviewer tests that create a staged Artifact JSON file and assert the model request contains the raw Artifact, optional raw Wiki file contents, and the step rubric, but no Supervisor-generated summary. Cover semantic rejection for `evidence_reasoning`, `research_plan`, `feedback_revision`, and `report_export`.

- [ ] **Step 2: Verify red state**

Run: `.\.venv\Scripts\python.exe -m pytest tests/backend/test_supervisor_agent.py tests/backend/test_reviewer_agent.py -q`

Expected: FAIL because validation APIs do not exist.

- [ ] **Step 3: Implement deterministic contracts**

Use required top-level fields:

```python
_REQUIRED_FIELDS = {
    "problem_understanding": ("problem_statement", "constraints", "knowledge_gaps", "literature_queries"),
    "knowledge_integration": ("references",),
    "hypothesis_generation": ("candidates",),
    "evidence_reasoning": ("active_hypothesis",),
    "research_plan": ("objective", "procedure"),
    "experiment_task": ("experiment_id", "manifest"),
    "experiment_run_analysis": ("experiment_id", "result_id", "metrics"),
    "feedback_revision": ("verdict",),
    "report_export": ("title", "sections", "references"),
}
```

Return targeted issue strings. Do not call an LLM from deterministic validation.

- [ ] **Step 4: Implement isolated semantic review**

`ReviewerAgent` opens only the staged Artifact path and explicitly allowed Wiki paths, then creates a fresh LLM request from their raw contents plus a versioned rubric. It must not receive conversation history, a Supervisor summary, or write-capable tools. Invoke it only after deterministic validation succeeds for `evidence_reasoning`, `research_plan`, `feedback_revision`, and `report_export`; convert its structured issues into the same revision path.

The engine writes candidate content to a Run-scoped staging file before review and deletes or marks the staging record rejected after a failed review. Only accepted content becomes the next Artifact version.

- [ ] **Step 5: Run Supervisor and Reviewer tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/backend/test_supervisor_agent.py tests/backend/test_reviewer_agent.py -q`

Expected: PASS.

- [ ] **Step 6: Commit validation behavior**

```powershell
git add backend/app/agents/supervisor.py backend/app/agents/reviewer.py tests/backend/test_supervisor_agent.py tests/backend/test_reviewer_agent.py
git commit -m "feat: validate supervisor outputs and retries"
```

### Task 6: Integrate One Shared Runtime into WorkflowEngine

**Files:**
- Modify: `backend/app/main.py`
- Modify: `backend/app/workflow/engine.py`
- Create: `backend/app/agents/idea.py`
- Modify: `backend/app/agents/hypothesis.py`
- Modify: `tests/backend/test_workflow_engine.py`
- Modify: `tests/backend/test_agents_use_llm.py`

**Interfaces:**
- Consumes: `SkillRuntime.prepare()` and `SupervisorAgent.delegate()`.
- Produces: each workflow event includes one `supervisor_agent.delegate` call and one `skill_runtime.prepare` audit call.

- [ ] **Step 1: Add failing engine integration tests**

```python
def test_engine_records_supervisor_and_skill_runtime_calls(selected_run):
    run = selected_run.engine.run_step(selected_run.id, "research_plan")
    providers = [call["provider"] for call in run.events[-1].tool_calls]
    assert "supervisor_agent" in providers
    assert "skill_runtime" in providers
```

Update the missing-Skill test so replacing `engine.skill_loader` also rebuilds or injects the shared runtime, rather than leaving Supervisor with a stale loader.

- [ ] **Step 2: Verify the current three workflow failures remain reproducible**

Run: `.\.venv\Scripts\python.exe -m pytest tests/backend/test_workflow_engine.py -q`

Expected: FAIL in the existing routed-Skill tests and the new runtime audit test.

- [ ] **Step 3: Build dependencies once and inject them**

`create_app()` constructs `SkillLoader`, `SkillRegistry`, `ToolRegistry`, `SkillRuntime`, `ReviewerAgent`, and `SupervisorAgent` once, then passes the Supervisor to `WorkflowEngine`. Domain Agents and ReviewerAgent receive the configured Qwen Provider through dependency injection. Remove engine-local duplicate route selection and every direct Codex/Claude tool assumption.

Add `IdeaAgent` as the preferred class name and retain this compatibility export:

```python
HypothesisAgent = IdeaAgent
```

Update the engine branch to require agent ID `idea`.

- [ ] **Step 4: Record complete runtime audits**

Add a call shaped as:

```python
{
    "provider": "skill_runtime",
    "method": "prepare",
    "step_id": package.step_id,
    "agent_id": package.agent_id,
    "skills": list(package.skill_ids),
    "authorized_tools": list(package.authorized_tools),
    "instruction_sha256": package.audit["instruction_sha256"],
}
```

- [ ] **Step 5: Run all Supervisor/Skill/engine tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/backend/test_supervisor_agent.py tests/backend/test_skill_runtime.py tests/backend/test_workflow_skills.py tests/backend/test_workflow_engine.py tests/backend/test_agents_use_llm.py -q`

Expected: PASS, including the three previously failing workflow tests.

- [ ] **Step 6: Commit engine integration**

```powershell
git add backend/app/main.py backend/app/workflow/engine.py backend/app/agents/idea.py backend/app/agents/hypothesis.py tests/backend/test_workflow_engine.py tests/backend/test_agents_use_llm.py
git commit -m "feat: run workflow through supervisor skill runtime"
```

### Task 7: Update Architecture Documentation and Run Regression Tests

**Files:**
- Modify: `docs/agent_architecture.md`
- Modify: `README.md`
- Test: all backend tests and frontend static contract tests.

**Interfaces:**
- Consumes: final runtime API from Tasks 1-6.
- Produces: repository documentation matching the implementation.

- [ ] **Step 1: Update documentation assertions first**

Change the architecture test to require `SupervisorAgent`, `SkillRuntime`, `authorized_tools`, and the approved static assignment table.

- [ ] **Step 2: Verify the documentation test fails**

Run: `.\.venv\Scripts\python.exe -m pytest tests/backend/test_workflow_skills.py::test_architecture_docs_describe_runtime_skill_routing -q`

Expected: FAIL until docs are updated.

- [ ] **Step 3: Update docs without claiming arbitrary model tool-calling**

Document that Provider calls remain application-controlled and audited by SkillRuntime. State that Skill `allowed-tools` now constrains registered application tools; it does not grant OS permissions. Clarify that runtime Agent classes live under `backend/app/agents`; top-level `.agents` is not part of application execution and is reserved only for possible future external Codex configuration.

- [ ] **Step 4: Run regression suites**

Run: `.\.venv\Scripts\python.exe -m pytest tests/backend -q`

Expected: all backend tests PASS.

Run from `frontend/`: `node --test tests/ui-contract.test.mjs`

Expected: UI contract tests PASS.

Run from `frontend/`: `node --test tests/presentation.test.ts`

Expected: presentation tests PASS.

Run from `frontend/`: `pnpm run build`

Expected: TypeScript and Vite build PASS.

- [ ] **Step 5: Commit documentation**

```powershell
git add docs/agent_architecture.md README.md tests/backend/test_workflow_skills.py
git commit -m "docs: describe supervisor skill runtime"
```
