# Qwen Skill Catalog Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route each workflow step through deterministic, relevant Skills discovered from Qwen-compatible top-level `skills/` folders.

**Architecture:** `SkillCatalog` discovers only direct `skills/<id>/SKILL.md` entries and excludes cross-model subtrees. `SkillRegistry` combines fixed mandatory Skills with a score-based supplemental selection calculated from catalog tokens and run/artifact context; `WorkflowEngine` records the complete decision in the existing `skill_router` trace record.

**Tech Stack:** Python 3.12+, FastAPI, pytest, existing workflow/LLM stack.

## Global Constraints

- Scan only direct children of `skills/`; exclude `skills-codex`, `skills-codex-claude-review`, `skills-codex-gemini-review`, and `shared-references`.
- Directory name is the stable Qwen Skill ID; no LLM chooses Skills.
- Keep mandatory Skills first and never load more than four Skills per workflow step.
- Preserve `SkillLoader` path checks, UTF-8 loading, and current prompt size limits.
- Do not execute `allowed-tools`, shell commands, or permissions mentioned by a Skill.

---

### Task 1: Discover the Qwen-only Skill Catalog

**Files:**
- Modify: `backend/app/workflow/skills.py`
- Modify: `tests/backend/test_workflow_skills.py`

**Interfaces:**
- `CatalogSkill(id: str, name: str, description: str, tokens: tuple[str, ...>)`.
- `SkillCatalog(loader: SkillLoader)` with `skills() -> tuple[CatalogSkill, ...>`.
- `SkillLoader.catalog_skill(skill_id: str) -> CatalogSkill` reuses existing frontmatter parsing.

- [ ] **Step 1: Write failing discovery tests**

```python
def test_catalog_discovers_only_direct_qwen_skill_directories(tmp_path):
    _write_skill(tmp_path, "experiment-plan", "experiment roadmap")
    _write_skill(tmp_path, "skills-codex/experiment-plan", "wrong variant")
    _write_skill(tmp_path, "skills-codex-claude-review/research-review", "wrong variant")
    _write_skill(tmp_path, "skills-codex-gemini-review/research-review", "wrong variant")
    _write_skill(tmp_path, "shared-references", "not a skill")

    catalog = SkillCatalog(SkillLoader(tmp_path))

    assert [skill.id for skill in catalog.skills()] == ["experiment-plan"]
    assert catalog.skills()[0].tokens == ("experiment", "plan", "roadmap")
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/backend/test_workflow_skills.py::test_catalog_discovers_only_direct_qwen_skill_directories -v`

Expected: import error for `SkillCatalog`.

- [ ] **Step 3: Implement CatalogSkill and SkillCatalog**

```python
_EXCLUDED_CATALOG_DIRECTORIES = {
    "skills-codex", "skills-codex-claude-review", "skills-codex-gemini-review", "shared-references",
}

class SkillCatalog:
    def __init__(self, loader: SkillLoader) -> None:
        self.loader = loader

    def skills(self) -> tuple[CatalogSkill, ...]:
        return tuple(
            self.loader.catalog_skill(path.name)
            for path in sorted(self.loader.skills_root.iterdir(), key=lambda item: item.name)
            if path.is_dir() and path.name not in _EXCLUDED_CATALOG_DIRECTORIES and (path / "SKILL.md").is_file()
        )
```

Build tokens from the kebab-case ID plus ASCII words in `name` and `description`, lowercase them, remove duplicates while retaining first occurrence, and do not inspect nested folders.

- [ ] **Step 4: Verify GREEN**

Run: `python -m pytest tests/backend/test_workflow_skills.py -v`

Expected: catalog discovery and existing safe-loader tests pass.

- [ ] **Step 5: Commit**

Run: `git add backend/app/workflow/skills.py tests/backend/test_workflow_skills.py`

Run: `git commit -m "feat: discover qwen skill catalog"`

### Task 2: Deterministically select mandatory and supplemental Skills

**Files:**
- Modify: `backend/app/workflow/skills.py`
- Modify: `tests/backend/test_workflow_skills.py`

**Interfaces:**
- `RouteDecision(mandatory_skills: tuple[str, ...>, selected_skills: tuple[str, ...>, candidate_scores: tuple[dict, ...>)`.
- `SkillRegistry.select(step_id: str, context: str, catalog: SkillCatalog) -> RouteDecision`.

- [ ] **Step 1: Add failing selection tests**

```python
def test_registry_selects_mandatory_then_relevant_supplemental_skills(tmp_path):
    for skill_id, description in {
        "experiment-plan": "experiment planning",
        "experiment-bridge": "experiment execution bridge",
        "training-check": "training validation",
        "vast-gpu": "gpu training setup",
        "paper-writing": "paper writing",
    }.items():
        _write_skill(tmp_path, skill_id, description)
    decision = SkillRegistry().select(
        "experiment_task", "CNN training needs GPU execution", SkillCatalog(SkillLoader(tmp_path))
    )
    assert decision.mandatory_skills == ("experiment-bridge",)
    assert decision.selected_skills == ("experiment-bridge", "training-check", "vast-gpu")
    assert [item["id"] for item in decision.candidate_scores] == ["training-check", "vast-gpu"]

def test_registry_selection_is_stable_and_never_exceeds_four(tmp_path):
    for skill_id in ["experiment-bridge", "run-experiment", "training-check", "vast-gpu", "system-profile"]:
        _write_skill(tmp_path, skill_id, "gpu training experiment")
    registry = SkillRegistry()
    catalog = SkillCatalog(SkillLoader(tmp_path))
    first = registry.select("experiment_task", "gpu training experiment", catalog)
    second = registry.select("experiment_task", "gpu training experiment", catalog)
    assert first == second
    assert len(first.selected_skills) <= 4
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/backend/test_workflow_skills.py::test_registry_selects_mandatory_then_relevant_supplemental_skills tests/backend/test_workflow_skills.py::test_registry_selection_is_stable_and_never_exceeds_four -v`

Expected: `AttributeError` because `SkillRegistry.select` does not exist.

- [ ] **Step 3: Implement fixed tag profiles and scoring**

```python
def select(self, step_id: str, context: str, catalog: SkillCatalog) -> RouteDecision:
    mandatory = self.skills_for(step_id)
    profile = _SUPPLEMENTAL_TAGS.get(step_id, ())
    context_tokens = _tokens(context)
    candidates = []
    for skill in catalog.skills():
        if skill.id in mandatory:
            continue
        tag_hits = tuple(token for token in skill.tokens if token in profile)
        if not tag_hits:
            continue
        context_hits = tuple(token for token in skill.tokens if token in context_tokens)
        score = 4 * len(tag_hits) + 2 * len(context_hits) + int(skill.id in context.lower())
        if score:
            candidates.append((score, skill.id, tag_hits, context_hits))
    candidates.sort(key=lambda item: (-item[0], item[1]))
    selected = tuple(mandatory) + tuple(item[1] for item in candidates[:max(0, 4 - len(mandatory))])
    return RouteDecision(tuple(mandatory), selected, tuple(_trace_score(item) for item in candidates))
```

Define `_SUPPLEMENTAL_TAGS` exactly as the approved design table. Candidate score records include `id`, `score`, `tag_hits`, `context_hits`, and `selected`; candidates beyond capacity remain in the record with `selected=False`.

- [ ] **Step 4: Verify GREEN**

Run: `python -m pytest tests/backend/test_workflow_skills.py -v`

Expected: deterministic selection, exclusion, capacity, and existing loader tests pass.

- [ ] **Step 5: Commit**

Run: `git add backend/app/workflow/skills.py tests/backend/test_workflow_skills.py`

Run: `git commit -m "feat: select skills from qwen catalog"`

### Task 3: Supply run context and trace the full routing decision

**Files:**
- Modify: `backend/app/workflow/engine.py`
- Modify: `backend/app/main.py`
- Modify: `tests/backend/test_workflow_engine.py`

**Interfaces:**
- `WorkflowEngine` receives `SkillCatalog` in addition to its loader and registry.
- `_skill_context(step_id: str, run: RunRecord) -> tuple[str, list[dict]]` calls `registry.select` and loads `decision.selected_skills`.
- Router trace contains `mandatory_skills`, `candidate_scores`, `selected_skills`, `excluded_directories`, `truncated`, and `instruction_characters`.

- [ ] **Step 1: Add failing workflow trace coverage**

```python
def test_experiment_task_trace_records_catalog_selection(tmp_path):
    repository, engine, run = prepared_plan_run(tmp_path)
    run = engine.run_step(run.id, "experiment_task")
    route = next(item for item in run.events[-1].tool_calls if item["provider"] == "skill_router")
    assert route["mandatory_skills"] == ["experiment-bridge"]
    assert route["selected_skills"][0] == "experiment-bridge"
    assert "candidate_scores" in route
    assert route["excluded_directories"] == [
        "shared-references", "skills-codex", "skills-codex-claude-review", "skills-codex-gemini-review",
    ]
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/backend/test_workflow_engine.py::test_experiment_task_trace_records_catalog_selection -v`

Expected: assertion failure because current trace only has `skills`.

- [ ] **Step 3: Connect catalog selection to the engine**

```python
def _skill_context(self, step_id: str, run) -> tuple[str, list[dict]]:
    context = _routing_context(run)
    decision = self.skill_registry.select(step_id, context, self.skill_catalog)
    contexts = self.skill_loader.load_many(list(decision.selected_skills))
    # Join the contexts in decision.selected_skills order and expose every decision field in skill_router.
```

`_routing_context` joins the run's `domain`, `problem_input`, `constraints` and each current artifact's type, title, and JSON content using `json.dumps(..., ensure_ascii=False, sort_keys=True)`. Update every `run_step` and `add_user_hypothesis` call site to pass the current run. In `main.py`, construct one `SkillCatalog(skill_loader)` and retain it across `Dependencies.reload`.

- [ ] **Step 4: Verify GREEN**

Run: `python -m pytest tests/backend/test_workflow_engine.py tests/backend/test_api.py tests/backend/test_workflow_skills.py -v`

Expected: all selected tests pass and each router trace contains the complete decision.

- [ ] **Step 5: Commit**

Run: `git add backend/app/workflow/engine.py backend/app/main.py tests/backend/test_workflow_engine.py tests/backend/test_workflow_skills.py`

Run: `git commit -m "feat: trace qwen skill catalog routes"`

### Task 4: Document the Qwen-only catalog boundary and verify the project

**Files:**
- Modify: `README.md`
- Modify: `docs/agent_architecture.md`
- Modify: `tests/backend/test_workflow_skills.py`

- [ ] **Step 1: Add a failing documentation contract test**

```python
def test_architecture_docs_exclude_cross_model_skill_variants():
    text = Path("docs/agent_architecture.md").read_text(encoding="utf-8")
    assert "skills-codex-claude-review" in text
    assert "skills-codex-gemini-review" in text
    assert "一级" in text
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/backend/test_workflow_skills.py::test_architecture_docs_exclude_cross_model_skill_variants -v`

Expected: assertion failure for the excluded directories.

- [ ] **Step 3: Document runtime catalog selection**

Add a `Qwen-only Skill Catalog` subsection that explains direct-directory discovery, exclusions, mandatory-plus-supplemental scoring, four-Skill maximum, and trace fields. Update README to state that Qwen is the only LLM backend used for these instructions and that Codex/Claude/Gemini variants are excluded.

- [ ] **Step 4: Run full verification**

Run: `python -m pytest tests/backend -v`

Expected: all backend tests pass.

Run: `cd frontend; pnpm run build`

Expected: Vite build exits 0 without TypeScript errors.

- [ ] **Step 5: Commit**

Run: `git add README.md docs/agent_architecture.md tests/backend/test_workflow_skills.py docs/superpowers/plans/2026-07-12-qwen-skill-catalog-routing.md`

Run: `git commit -m "docs: describe qwen skill catalog routing"`

## Plan self-review

- Tasks 1–2 cover the Qwen-only direct-directory Catalog, exclusions, token extraction, fixed profiles, scoring, capacity, and deterministic output.
- Task 3 covers run/artifact context and the required trace fields without changing Agents, providers, or step order.
- Task 4 covers the catalog boundary and project-wide verification.
