# Literature Summary and Automatic Idea Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrate automatic `idea-selection` into `master`, display its concise audit basis, and replace the literature table with an expandable literature summary.

**Architecture:** The existing automatic-selection branch supplies the workflow step, server-owned weighted selection, artifacts, and removal of manual selection APIs. The presentation layer reads the latest `evidence`, `idea_review`, and `hypothesis_selection` artifacts; it never creates client-side selection state.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, pytest, React 19, TypeScript, Vite, Node test runner.

## Global Constraints

- `idea-selection` is the only hypothesis-selection path; users cannot manually choose a candidate.
- The server selects the highest weighted review and preserves its decision, selection reason, risks, and unknowns.
- Candidate cards show only Idea plus concise feasibility and selection basis; they do not show a full scoring table.
- The literature card is collapsed initially and expands in place; it reports counts, representative titles, queries, and source state.
- Browser network failures must say that `127.0.0.1:8000` is unreachable; HTTP business errors remain unchanged.
- Preserve `docs/superpowers/specs/2026-07-15-literature-summary-and-hypothesis-cards-design.md` during integration.

---

## File Structure

- `backend/app/agents/idea_selection.py` invokes Qwen with the `idea-selection` skill contract.
- `backend/app/workflow/idea_selection.py` validates reviews and performs deterministic server-side weighted selection.
- `backend/app/workflow/engine.py` writes `idea_review` and automatic `hypothesis_selection` before evidence reasoning.
- `frontend/src/{App.tsx,components/PipelineTimeline.tsx,pages/WorkbenchPage.tsx}` runs and displays `idea_selection` in the automatic sequence.
- `frontend/src/components/HypothesisBoard.tsx` renders Idea, concise selection basis, and the selected marker.
- `frontend/src/components/EvidenceTable.tsx` renders collapsed literature summary and expanded reference/source details.
- `frontend/src/api/client.ts` maps only browser transport failures to a backend connectivity message.
- `tests/backend/test_idea_selection.py` and `frontend/tests/ui-contract.test.mjs` guard the workflow and presentation contracts.

### Task 1: Integrate automatic idea selection

**Files:**
- Create: `backend/app/agents/idea_selection.py`, `backend/app/workflow/idea_selection.py`, `tests/backend/test_idea_selection.py`
- Modify: `backend/app/workflow/engine.py`, `backend/app/workflow/{steps,skills}.py`, `backend/app/{agents/supervisor.py,providers/llm.py,api/runs.py,workflow/plan_contract.py}`, `frontend/src/{App.tsx,components/PipelineTimeline.tsx,pages/WorkbenchPage.tsx,api/client.ts,components/HypothesisBoard.tsx}`, `frontend/tests/ui-contract.test.mjs`
- Test: `tests/backend/test_idea_selection.py`, `tests/backend/test_workflow_engine.py`, `tests/backend/test_api.py`

**Interfaces:**
- Consumes: `IdeaAgent.generate(...) -> {"candidates": list[dict]}` and verified evidence cards.
- Produces: `idea_selection`, `idea_review.evaluations`, and automatic `hypothesis_selection` with `selected`, `selected_indexes`, `weighted_score`, `selection_reason`, and `selection_mode="automatic_weighted_review"`.

- [ ] **Step 1: Restore failing selection tests**

Restore the tests from commits `68be605` and `6cd6eb4`. They assert highest weighted selection, lower-index tie break, rejection of invalid review indexes, absence of the manual route, and automatic selection required before evidence reasoning and planning.

- [ ] **Step 2: Verify the current workflow fails those tests**

Run: `D:\竞赛\.venv\Scripts\python.exe -m pytest tests/backend/test_idea_selection.py tests/backend/test_workflow_engine.py tests/backend/test_api.py -q`

Expected: FAIL because `idea_selection` and `IdeaSelectionAgent` are absent and manual selection remains.

- [ ] **Step 3: Apply the established workflow implementation**

```powershell
git cherry-pick 68be605
git cherry-pick 6cd6eb4
git cherry-pick 8dfa473
```

Resolve the design-document deletion by retaining the current design specification. The resulting engine must invoke `IdeaSelectionAgent.review(...)`, call `select_top_evaluation(...)`, save both artifacts, and make evidence reasoning and planning require automatic selection.

- [ ] **Step 4: Verify the automatic workflow**

Run: `D:\竞赛\.venv\Scripts\python.exe -m pytest tests/backend/test_idea_selection.py tests/backend/test_workflow_engine.py tests/backend/test_api.py tests/backend/test_end_to_end_dev_mode.py tests/backend/test_supervisor_agent.py -q`

Expected: PASS.

### Task 2: Show concise feasibility and selection basis

**Files:**
- Modify: `frontend/src/components/HypothesisBoard.tsx`, `frontend/tests/ui-contract.test.mjs`
- Test: `frontend/tests/ui-contract.test.mjs`

**Interfaces:**
- Consumes: latest `hypothesis`, `idea_review`, and `hypothesis_selection` artifacts.
- Produces: a card with `Idea`, `可行性与选择依据`, an automatic-selection marker, review decision, selection reason, gates, risks, and unknowns; no manual controls or raw score.

- [ ] **Step 1: Add a failing UI contract**

Add a test that requires `idea_review`, `自动选择`, `可行性与选择依据`, `selection_reason`, `gates`, `risks`, and `unknowns`, while forbidding `type="checkbox"`, `确认选择`, `onSelectHypotheses`, `weighted_score`, and `scoreText` in `HypothesisBoard.tsx`.

- [ ] **Step 2: Verify the contract fails**

Run: `pnpm --dir frontend exec tsx --test tests/ui-contract.test.mjs`

Expected: FAIL because the branch UI exposes `weighted_score` and lacks the concise basis label.

- [ ] **Step 3: Implement the concise review mapping**

Replace `scoreText` with `feasibilityBasis(evaluation, fallback)`. It joins non-empty `gates`, `mde`, `risks`, and `unknowns` in that order; renders the review decision and selection reason when present; and keeps the selected-card `自动选择` marker. Use `claim` for `Idea`. Do not add local selection state or a selection button.

- [ ] **Step 4: Verify the card**

Run: `pnpm --dir frontend exec tsx --test tests/ui-contract.test.mjs`

Expected: PASS.

Run: `pnpm --dir frontend run build`

Expected: exit code 0.

### Task 3: Add expandable literature summary and connectivity copy

**Files:**
- Modify: `frontend/src/components/EvidenceTable.tsx`, `frontend/src/api/client.ts`, `frontend/tests/ui-contract.test.mjs`
- Test: `frontend/tests/ui-contract.test.mjs`

**Interfaces:**
- Consumes: latest `evidence.content.references`, `evidence.content.sources.calls`, and `evidence.content.warnings`.
- Produces: collapsed counts, at most two titles, status, and a toggle; expanded table with all references, queries, and warnings.

- [ ] **Step 1: Add failing UI contracts**

Require `useState(false)`, `查看全部文献`, `收起文献`, `sources.calls`, and `warnings` in `EvidenceTable.tsx`; require `127.0.0.1:8000` in `client.ts`.

- [ ] **Step 2: Verify the contracts fail**

Run: `pnpm --dir frontend exec tsx --test tests/ui-contract.test.mjs`

Expected: FAIL because the current table is always visible and fetch errors are not translated.

- [ ] **Step 3: Implement summary, disclosure, and transport error mapping**

Use `findLatestArtifact(artifacts, "evidence")`; add `const [detailsOpen, setDetailsOpen] = useState(false)`; show reference count, `references.slice(0, 2)`, and a status derived from warnings or empty results. Render the table, `sources.calls` query list, and warnings only when `detailsOpen` is true. Keep existing local-library actions unchanged.

Wrap `fetch(...)` in `client.ts`: if a caught `TypeError` message contains `fetch`, throw `new Error("无法连接后端服务（127.0.0.1:8000）。请确认后端正在运行。")`; rethrow every other error.

- [ ] **Step 4: Verify frontend behaviour**

Run: `pnpm --dir frontend exec tsx --test tests/ui-contract.test.mjs`

Expected: PASS.

Run: `pnpm --dir frontend run build`

Expected: exit code 0.

### Task 4: Verify and commit the integrated feature

**Files:**
- Test: `tests/backend/test_idea_selection.py`, `tests/backend/test_workflow_engine.py`, `tests/backend/test_api.py`, `tests/backend/test_end_to_end_dev_mode.py`, `tests/backend/test_supervisor_agent.py`, `frontend/tests/ui-contract.test.mjs`

**Interfaces:**
- Consumes: Tasks 1–3.
- Produces: fresh evidence for automatic selection, concise cards, expandable literature, and production compilation.

- [ ] **Step 1: Run backend verification**

Run: `D:\竞赛\.venv\Scripts\python.exe -m pytest tests/backend/test_idea_selection.py tests/backend/test_workflow_engine.py tests/backend/test_api.py tests/backend/test_end_to_end_dev_mode.py tests/backend/test_supervisor_agent.py -q`

Expected: PASS.

- [ ] **Step 2: Run frontend verification**

Run: `pnpm --dir frontend exec tsx --test tests/ui-contract.test.mjs`

Expected: PASS.

Run: `pnpm --dir frontend run build`

Expected: exit code 0.

- [ ] **Step 3: Commit intentional changes**

```powershell
git add backend frontend tests docs/superpowers
git commit -m "feat: summarize literature and automate idea selection"
```
