# Workflow Skill Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `WorkflowEngine` select, safely load, inject, and trace repository Skills for every workflow step.

**Architecture:** A static `SkillRegistry` maps `step_id` to ordered Skill IDs. A confined `SkillLoader` returns bounded `SkillContext` instances from the repository's `skills/` directory. The engine resolves the contexts before dispatch, passes the joined instructions only to the responsible Agent, and records the router output with every event.

**Tech Stack:** Python 3.12+, FastAPI, pytest, existing Qwen-compatible HTTP provider.

## Global Constraints

- Load only `skills/<skill_id>/SKILL.md` under the repository root; reject absolute paths and traversal.
- Use registry-selected Skills only; no model-directed directory search.
- Cap one Skill body at 12,000 Unicode characters and joined step instructions at 32,000.
- Missing or empty routed files raise `ValueError("SKILL_NOT_FOUND:<skill_id>")` before artifact creation.
- Do not execute or interpret `allowed-tools` or any other permission declaration in Skill text.
- Preserve existing dirty files and stage only the files listed by each task.

---

## Files

- Create `backend/app/workflow/skills.py`: `SkillContext`, `SkillLoader`, and `SkillRegistry`.
- Modify `backend/app/providers/llm.py`: add optional `instructions` to `generate_json`.
- Modify `backend/app/agents/{research,hypothesis,planner,critic,writer}.py`: forward optional instructions to their LLM calls.
- Modify `backend/app/workflow/engine.py` and `backend/app/main.py`: resolve, inject, and trace Skills.
- Create `tests/backend/test_workflow_skills.py`: loader, registry, injection, missing-file, and trace tests.
- Modify `tests/backend/test_agents_use_llm.py` and `tests/backend/test_workflow_engine.py` for forwarding and trace assertions.
- Modify `README.md` and `docs/agent_architecture.md` to document the runtime boundary.

### Task 1: Safe Skill loader and static registry

**Files:**
- Create: `backend/app/workflow/skills.py`
- Create: `tests/backend/test_workflow_skills.py`

**Interfaces:**
- `SkillContext(id: str, name: str, description: str, instructions: str, truncated: bool)`.
- `SkillLoader(root: Path, per_skill_limit: int = 12000, total_limit: int = 32000)`.
- `load(skill_id: str) -> SkillContext`; `load_many(skill_ids: list[str]) -> list[SkillContext]`.
- `SkillRegistry.skills_for(step_id: str) -> tuple[str, ...>`.

- [ ] **Step 1: Write the failing tests**

```python
def test_loader_parses_frontmatter_and_truncates_body(tmp_path):
    target = tmp_path / "skills" / "demo"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text("---\nname: demo\ndescription: test\n---\n" + "x" * 20, encoding="utf-8")
    loaded = SkillLoader(tmp_path, per_skill_limit=10).load("demo")
    assert (loaded.id, loaded.name, loaded.description) == ("demo", "demo", "test")
    assert loaded.instructions == "x" * 10
    assert loaded.truncated is True


@pytest.mark.parametrize("skill_id", ["../secret", "C:/secret", "/secret"])
def test_loader_rejects_unsafe_paths(tmp_path, skill_id):
    with pytest.raises(ValueError, match="SKILL_NOT_FOUND"):
        SkillLoader(tmp_path).load(skill_id)


def test_missing_skill_and_plan_route_are_stable(tmp_path):
    with pytest.raises(ValueError, match="SKILL_NOT_FOUND:experiment-plan"):
        SkillLoader(tmp_path).load("experiment-plan")
    assert SkillRegistry().skills_for("research_plan") == ("experiment-plan", "ablation-planner")
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/backend/test_workflow_skills.py -v`

Expected: collection fails with `ModuleNotFoundError` for `backend.app.workflow.skills`.

- [ ] **Step 3: Implement the smallest loader and registry**

```python
_ROUTES = {
    "problem_understanding": ("idea-discovery",), "knowledge_integration": ("research-lit",),
    "hypothesis_generation": ("idea-discovery",), "evidence_reasoning": ("research-review",),
    "research_plan": ("experiment-plan", "ablation-planner"),
    "experiment_task": ("experiment-bridge", "run-experiment"),
    "experiment_run_analysis": ("monitor-experiment", "analyze-results"),
    "feedback_revision": ("experiment-audit", "research-review"), "report_export": ("paper-writing",),
}

@dataclass(frozen=True)
class SkillContext:
    id: str; name: str; description: str; instructions: str; truncated: bool

class SkillRegistry:
    def skills_for(self, step_id: str) -> tuple[str, ...]: return _ROUTES.get(step_id, ())

class SkillLoader:
    def load(self, skill_id: str) -> SkillContext:
        if Path(skill_id).is_absolute() or ".." in Path(skill_id).parts: raise ValueError(f"SKILL_NOT_FOUND:{skill_id}")
        target = (self.skills_root / skill_id / "SKILL.md").resolve()
        if self.skills_root not in target.parents or not target.is_file(): raise ValueError(f"SKILL_NOT_FOUND:{skill_id}")
        # Parse only frontmatter name/description; retain body as bounded text.
```

`load_many` keeps registry order and truncates each context to the remaining total character budget. It must set `truncated=True` when either limit removes text.

- [ ] **Step 4: Verify GREEN**

Run: `python -m pytest tests/backend/test_workflow_skills.py -v`

Expected: loader and registry tests pass.

- [ ] **Step 5: Commit**

Run: `git add backend/app/workflow/skills.py tests/backend/test_workflow_skills.py`

Run: `git commit -m "feat: add workflow skill loader"`

### Task 2: Forward routed instructions to LLM-backed Agents

**Files:**
- Modify: `backend/app/providers/llm.py`
- Modify: `backend/app/agents/research.py`, `hypothesis.py`, `planner.py`, `critic.py`, `writer.py`
- Modify: `tests/backend/test_agents_use_llm.py`

**Interfaces:**
- `LLMProvider.generate_json(task, inputs, schema_hint, instructions: str = "") -> dict`.
- Existing Agent public methods gain keyword-only `instructions: str = ""`.
- Qwen adds non-empty instructions as a system message; Mock accepts but does not use them.

- [ ] **Step 1: Add a failing Agent forwarding test**

```python
def test_planning_agent_forwards_skill_instructions_to_llm():
    llm = RecordingLLM()
    PlanningAgent(llm).build_plan({"selected": []}, instructions="Use the experiment-plan protocol.")
    assert llm.calls[-1]["instructions"] == "Use the experiment-plan protocol."
```

Extend the test's `RecordingLLM.generate_json` signature with `instructions: str = ""` and record it. Do not edit Agent production code yet.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/backend/test_agents_use_llm.py::test_planning_agent_forwards_skill_instructions_to_llm -v`

Expected: `TypeError` because `PlanningAgent.build_plan` lacks `instructions`.

- [ ] **Step 3: Implement forwarding**

```python
def build_plan(self, hypothesis: dict, *, instructions: str = "") -> dict:
    return self.llm_provider.generate_json("planning.build_plan", {"active_hypothesis": hypothesis}, SCHEMA_HINT, instructions=instructions)
```

Apply the same keyword-only forwarding to `ResearchAgent.structure_problem`, both Hypothesis methods, both Critic methods, and `WriterAgent.write_report`. Add the optional argument to both concrete LLM providers. The Qwen payload preserves all current data and adds a `{"role": "system", "content": instructions}` message only when non-empty.

- [ ] **Step 4: Verify GREEN**

Run: `python -m pytest tests/backend/test_agents_use_llm.py tests/backend/test_llm_provider.py -v`

Expected: selected tests pass, including the new forwarding assertion.

- [ ] **Step 5: Commit**

Run: `git add backend/app/providers/llm.py backend/app/agents/research.py backend/app/agents/hypothesis.py backend/app/agents/planner.py backend/app/agents/critic.py backend/app/agents/writer.py tests/backend/test_agents_use_llm.py`

Run: `git commit -m "feat: pass skill instructions to agents"`

### Task 3: Route and trace Skills from WorkflowEngine

**Files:**
- Modify: `backend/app/workflow/engine.py`
- Modify: `backend/app/main.py`
- Modify: `tests/backend/test_workflow_engine.py`
- Modify: `tests/backend/test_workflow_skills.py`

**Interfaces:**
- `WorkflowEngine(..., skill_loader: SkillLoader, skill_registry: SkillRegistry)`.
- `_skill_context(step_id: str) -> tuple[str, list[dict]]` returns instructions plus one event-ready `skill_router` record.
- `Dependencies` retains the loader and registry so `reload()` constructs an engine with the same dependencies.

- [ ] **Step 1: Add failing trace and missing-file tests**

```python
def test_research_plan_trace_records_routed_skills(engine, run):
    run = prepare_selected_hypothesis(engine, run)
    run = engine.run_step(run.id, "research_plan")
    call = next(item for item in run.events[-1].tool_calls if item["provider"] == "skill_router")
    assert call["skills"] == ["experiment-plan", "ablation-planner"]

def test_missing_skill_stops_before_plan_artifact(tmp_path, repository, llm, literature, experiment):
    engine = WorkflowEngine(repository, llm, literature, experiment, SkillLoader(tmp_path), SkillRegistry())
    run = prepare_selected_hypothesis(engine, repository.create_run("title", "problem"))
    with pytest.raises(ValueError, match="SKILL_NOT_FOUND:experiment-plan"):
        engine.run_step(run.id, "research_plan")
    assert "plan" not in {item.type for item in repository.get_run(run.id).artifacts}
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/backend/test_workflow_engine.py::test_research_plan_trace_records_routed_skills tests/backend/test_workflow_skills.py::test_missing_skill_stops_before_plan_artifact -v`

Expected: constructor/signature failure and no `skill_router` record.

- [ ] **Step 3: Implement resolution, injection, and trace metadata**

```python
def _skill_context(self, step_id: str) -> tuple[str, list[dict]]:
    contexts = self.skill_loader.load_many(list(self.skill_registry.skills_for(step_id)))
    instructions = "\n\n".join(f"## Skill: {item.id}\n{item.description}\n\n{item.instructions}" for item in contexts)
    return instructions, [{"provider": "skill_router", "method": "load", "skills": [item.id for item in contexts], "truncated": [item.id for item in contexts if item.truncated], "instruction_characters": len(instructions)}]
```

Resolve it after the locked-artifact guard and before any Agent/provider call. Pass `instructions=instructions` to LLM-backed Agent calls. Extend `_trace` with `skill_calls: list[dict] | None = None` and append it to existing tool calls. Experimental provider steps still receive trace metadata even though they do not consume prompt text. In `main.py`, derive the repository root with `Path(__file__).resolve().parents[2]` and inject loader and registry both at initial app creation and reload.

- [ ] **Step 4: Verify GREEN**

Run: `python -m pytest tests/backend/test_workflow_engine.py tests/backend/test_api.py tests/backend/test_workflow_skills.py -v`

Expected: selected tests pass and the trace lists routed IDs.

- [ ] **Step 5: Commit**

Run: `git add backend/app/workflow/engine.py backend/app/main.py tests/backend/test_workflow_engine.py tests/backend/test_workflow_skills.py`

Run: `git commit -m "feat: route workflow steps through skills"`

### Task 4: Document the actual runtime boundary and verify the project

**Files:**
- Modify: `README.md`
- Modify: `docs/agent_architecture.md`
- Modify: `tests/backend/test_workflow_skills.py`

- [ ] **Step 1: Add a failing documentation contract test**

```python
def test_architecture_docs_describe_runtime_skill_routing():
    text = Path("docs/agent_architecture.md").read_text(encoding="utf-8")
    assert "SkillRegistry" in text
    assert "SkillLoader" in text
    assert "skill_router" in text
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/backend/test_workflow_skills.py::test_architecture_docs_describe_runtime_skill_routing -v`

Expected: assertion failure for `SkillRegistry`.

- [ ] **Step 3: Update documentation**

Add a `Skill routing at runtime` subsection after the Supervisor section in `docs/agent_architecture.md`. It names `SkillRegistry`, `SkillLoader`, `skill_router`, the Agent/Skill/engine boundaries, and that Skill permission declarations are documentation only. Add one README paragraph linking to the architecture section and stating that Skills are now loaded at runtime. State explicitly that executable experiment manifests are a follow-up feature.

- [ ] **Step 4: Run full verification**

Run: `python -m pytest tests/backend -v`

Expected: backend suite passes.

Run: `cd frontend; pnpm run build`

Expected: Vite exits 0 without TypeScript errors.

- [ ] **Step 5: Commit**

Run: `git add README.md docs/agent_architecture.md tests/backend/test_workflow_skills.py`

Run: `git commit -m "docs: explain runtime skill routing"`

## Plan self-review

- Tasks 1 and 3 cover the registry, safe loading, size limits, missing-file behavior, and audit trace required by the design.
- Task 2 covers the LLM instruction boundary without changing existing callers.
- Task 4 covers the permission and future-manifest boundary plus full verification.
- The plan introduces `SkillContext`, `SkillLoader`, `SkillRegistry`, and `instructions` before later tasks consume them.
