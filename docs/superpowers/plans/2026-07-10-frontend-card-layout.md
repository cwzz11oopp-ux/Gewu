# Frontend Card Layout Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the four approved frontend display issues for history rows, literature metadata, card ordering, and the competition-mode topbar label.

**Architecture:** Keep all backend contracts unchanged. Add one dependency-free presentation helper module for deterministic text formatting, then update the existing React components and use named CSS grid areas so semantic workflow order and visual placement cannot drift apart.

**Tech Stack:** React 19, TypeScript, Vite, CSS Grid, Node 24 built-in test runner.

## Global Constraints

- Scope is limited to issues 1–4; deployment paths, literature knowledge architecture, local uploads, real experiment execution, and Skill behavior are excluded.
- History rows display only `RunRecord.problem_input`, limited to the first 8 Unicode characters plus `…` when longer, with the full question in the native `title` tooltip.
- Literature displays only title, authors, and actual DOI; when DOI is absent it displays `source`, and when both are absent it displays `期刊未知`.
- Desktop workflow order and placement are A Research Topic, B Literature Retrieval, C Candidate Hypotheses, D Experiment Design, E Experiment Execution; C spans both columns.
- At widths of 960px or less, A–E become one column and each literature row stacks its three fields without horizontal overflow.
- Remove only the topbar “比赛模式” item; preserve Qwen configuration, experiment mode, researcher entry, and all other competition-mode copy.
- Do not add runtime or test dependencies.

---

### Task 1: Presentation Formatting Helpers

**Files:**
- Create: `frontend/src/utils/presentation.ts`
- Create: `frontend/tests/presentation.test.ts`

**Interfaces:**
- Produces: `summarizeResearchProblem(value: unknown): { text: string; fullText: string }`
- Produces: `formatReferenceTitle(value: unknown): string`
- Produces: `formatAuthors(value: unknown): string`
- Produces: `formatReferenceIdentifier(reference: Record<string, unknown>): string`

- [ ] **Step 1: Write the failing presentation tests**

```ts
import assert from "node:assert/strict";
import test from "node:test";
import {
  formatAuthors,
  formatReferenceIdentifier,
  formatReferenceTitle,
  summarizeResearchProblem,
} from "../src/utils/presentation.ts";

test("research summary keeps exactly eight Unicode characters", () => {
  assert.deepEqual(summarizeResearchProblem("研究可信训练方案"), {
    text: "研究可信训练方案",
    fullText: "研究可信训练方案",
  });
});

test("research summary truncates after eight Unicode characters", () => {
  assert.deepEqual(summarizeResearchProblem("研究可信训练方案优化"), {
    text: "研究可信训练方案…",
    fullText: "研究可信训练方案优化",
  });
  assert.equal(summarizeResearchProblem("😀12345678").text, "😀1234567…");
});

test("research summary supplies the approved empty fallback", () => {
  assert.deepEqual(summarizeResearchProblem("   "), {
    text: "未填写研究问题",
    fullText: "未填写研究问题",
  });
});

test("reference presentation formats title and authors", () => {
  assert.equal(formatReferenceTitle("  Paper title  "), "Paper title");
  assert.equal(formatReferenceTitle(null), "未命名论文");
  assert.equal(formatAuthors(["Ada", "Grace"]), "Ada, Grace");
  assert.equal(formatAuthors([]), "作者未知");
});

test("reference identifier prefers DOI and falls back to journal", () => {
  assert.equal(formatReferenceIdentifier({ identifiers: { doi: " 10.1000/test " }, source: "JMLR" }), "10.1000/test");
  assert.equal(formatReferenceIdentifier({ identifiers: {}, source: " JMLR " }), "JMLR");
  assert.equal(formatReferenceIdentifier({}), "期刊未知");
});
```

- [ ] **Step 2: Run the tests to verify RED**

Run: `node --test tests/presentation.test.ts` from `frontend`

Expected: FAIL with `ERR_MODULE_NOT_FOUND` for `src/utils/presentation.ts`.

- [ ] **Step 3: Implement the minimal presentation helpers**

```ts
function normalizedString(value: unknown, fallback: string): string {
  if (typeof value !== "string") return fallback;
  const normalized = value.trim();
  return normalized || fallback;
}

function truncateUnicode(value: string, maxCharacters: number): string {
  const characters = Array.from(value);
  return characters.length > maxCharacters
    ? `${characters.slice(0, maxCharacters).join("")}…`
    : value;
}

export function summarizeResearchProblem(value: unknown) {
  const fullText = normalizedString(value, "未填写研究问题");
  return { text: truncateUnicode(fullText, 8), fullText };
}

export function formatReferenceTitle(value: unknown): string {
  return normalizedString(value, "未命名论文");
}

export function formatAuthors(value: unknown): string {
  if (!Array.isArray(value)) return "作者未知";
  const authors = value
    .filter((author): author is string => typeof author === "string")
    .map((author) => author.trim())
    .filter(Boolean);
  return authors.length ? authors.join(", ") : "作者未知";
}

export function formatReferenceIdentifier(reference: Record<string, unknown>): string {
  const identifiers = reference.identifiers;
  const doi = identifiers && typeof identifiers === "object"
    ? normalizedString((identifiers as Record<string, unknown>).doi, "")
    : "";
  if (doi) return doi;
  return normalizedString(reference.source, "期刊未知");
}
```

- [ ] **Step 4: Run the tests to verify GREEN**

Run: `node --test tests/presentation.test.ts` from `frontend`

Expected: PASS, 5 tests, 0 failures.

- [ ] **Step 5: Commit Task 1**

```powershell
git add -- frontend/src/utils/presentation.ts frontend/tests/presentation.test.ts
git commit -m "test: add frontend presentation formatters"
```

---

### Task 2: History, Literature, Workflow Order, and Topbar Markup

**Files:**
- Create: `frontend/tests/ui-contract.test.mjs`
- Modify: `frontend/src/components/ProjectSettingsModal.tsx`
- Modify: `frontend/src/components/EvidenceTable.tsx`
- Modify: `frontend/src/components/HypothesisBoard.tsx`
- Modify: `frontend/src/pages/WorkbenchPage.tsx`
- Modify: `frontend/src/App.tsx`

**Interfaces:**
- Consumes: all four presentation functions from Task 1.
- Produces: semantic A/B/C component order and three-field literature markup with `data-label` attributes for responsive CSS.

- [ ] **Step 1: Write failing markup contract tests**

```js
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const readSource = (path) => readFile(new URL(`../${path}`, import.meta.url), "utf8");

test("topbar removes only the competition-mode pill", async () => {
  const app = await readSource("src/App.tsx");
  assert.doesNotMatch(app, /比赛模式|Trophy/);
  assert.match(app, /配置 Qwen/);
  assert.match(app, /实验:/);
  assert.match(app, /研究员/);
});

test("workbench renders literature before hypotheses", async () => {
  const workbench = await readSource("src/pages/WorkbenchPage.tsx");
  assert.ok(workbench.indexOf("<EvidenceTable") < workbench.indexOf("<HypothesisBoard"));
});

test("literature and hypotheses use B and C labels", async () => {
  const evidence = await readSource("src/components/EvidenceTable.tsx");
  const hypotheses = await readSource("src/components/HypothesisBoard.tsx");
  assert.match(evidence, /<span>B<\/span>/);
  assert.match(evidence, /<th>论文标题<\/th><th>作者<\/th><th>DOI \/ 期刊<\/th>/);
  assert.doesNotMatch(evidence, /<th>年份<\/th>|<th>来源<\/th>|<th>验证<\/th>/);
  assert.match(hypotheses, /<span>C<\/span>/);
});

test("history rows use research-problem summaries only", async () => {
  const settings = await readSource("src/components/ProjectSettingsModal.tsx");
  assert.match(settings, /summarizeResearchProblem\(item\.problem_input\)/);
  assert.doesNotMatch(settings, /item\.artifacts\.length|item\.status/);
});
```

- [ ] **Step 2: Run the contract tests to verify RED**

Run: `node --test tests/ui-contract.test.mjs` from `frontend`

Expected: FAIL because the old competition pill, C/B labels, five table headers, and history metadata still exist.

- [ ] **Step 3: Update history rows**

Import `summarizeResearchProblem`. In the history map, compute the presentation value and render only its summary:

```tsx
{runs.length ? runs.map((item) => {
  const researchProblem = summarizeResearchProblem(item.problem_input);
  return (
    <div className="history-item-row" key={item.id}>
      <button className="history-item" title={researchProblem.fullText} onClick={() => loadRun(item.id)}>
        <strong>{researchProblem.text}</strong>
      </button>
      <button className="danger-button" onClick={() => deleteRun(item.id, researchProblem.fullText)}>删除</button>
    </div>
  );
}) : <p className="muted-copy">暂无历史研究。创建 Run 后会出现在这里。</p>}
```

Rename the local delete confirmation parameter from `title` to `researchProblem` and interpolate that full value.

- [ ] **Step 4: Replace the literature table with three formatted columns**

Import the three reference helpers, normalize `references` to `Array<Record<string, unknown>>`, keep the search and verified-count rows, then render:

```tsx
<div className="section-title"><span>B</span><h2>文献检索与验证</h2></div>
<table className="data-table literature-table">
  <colgroup><col className="paper-title-column" /><col className="paper-author-column" /><col className="paper-id-column" /></colgroup>
  <thead><tr><th>论文标题</th><th>作者</th><th>DOI / 期刊</th></tr></thead>
  <tbody>
    {rows.map((record, index) => {
      const title = formatReferenceTitle(record.title);
      const authors = formatAuthors(record.authors);
      const identifier = formatReferenceIdentifier(record);
      return (
        <tr key={`${title}-${index}`}>
          <td data-label="论文标题"><span className="paper-title" title={title}>{title}</span></td>
          <td data-label="作者"><span className="paper-authors" title={authors}>{authors}</span></td>
          <td data-label="DOI / 期刊"><span className="paper-identifier" title={identifier}>{identifier}</span></td>
        </tr>
      );
    })}
  </tbody>
</table>
```

Use demo data with a real DOI string for the first item and `source` fallbacks for items without DOI; do not retain generic `"DOI"` placeholder values.

- [ ] **Step 5: Align B/C component order and remove the topbar pill**

- Move `<EvidenceTable>` immediately after the A research topic section and before `<HypothesisBoard>` in `WorkbenchPage.tsx`.
- Change the hypothesis badge from B to C.
- Remove `Trophy` from the `lucide-react` import and delete only `<span className="top-pill"><Trophy ... /> 比赛模式</span>`.

- [ ] **Step 6: Run presentation and markup tests**

Run: `node --test tests/presentation.test.ts tests/ui-contract.test.mjs` from `frontend`

Expected: PASS, 9 tests, 0 failures.

- [ ] **Step 7: Commit Task 2**

```powershell
git add -- frontend/src/App.tsx frontend/src/pages/WorkbenchPage.tsx frontend/src/components/ProjectSettingsModal.tsx frontend/src/components/EvidenceTable.tsx frontend/src/components/HypothesisBoard.tsx frontend/tests/ui-contract.test.mjs
git commit -m "fix: simplify research and literature cards"
```

---

### Task 3: Deterministic Desktop and Responsive Layout

**Files:**
- Modify: `frontend/tests/ui-contract.test.mjs`
- Modify: `frontend/src/styles.css`

**Interfaces:**
- Consumes: card class names and literature `data-label` attributes from Task 2.
- Produces: named desktop grid areas, 44px history rows, fixed 52/28/20 literature columns, two-line titles, single-line authors, and mobile stacked rows.

- [ ] **Step 1: Add failing CSS contract tests**

Append:

```js
test("desktop grid uses the approved A through E placement", async () => {
  const css = await readSource("src/styles.css");
  assert.match(css, /grid-template-areas:\s*"topic literature"\s*"hypotheses hypotheses"\s*"design experiment"/);
  assert.match(css, /\.hypothesis-card\s*\{[^}]*grid-area:\s*hypotheses/s);
  assert.match(css, /\.literature-card\s*\{[^}]*grid-area:\s*literature/s);
});

test("literature and history styles enforce bounded text", async () => {
  const css = await readSource("src/styles.css");
  assert.match(css, /\.history-item-row\s*\{[^}]*min-height:\s*44px/s);
  assert.match(css, /\.paper-title-column\s*\{[^}]*width:\s*52%/s);
  assert.match(css, /\.paper-author-column\s*\{[^}]*width:\s*28%/s);
  assert.match(css, /\.paper-id-column\s*\{[^}]*width:\s*20%/s);
  assert.match(css, /-webkit-line-clamp:\s*2/);
});

test("mobile grid and literature rows stack without horizontal overflow", async () => {
  const css = await readSource("src/styles.css");
  assert.match(css, /grid-template-areas:\s*"topic"\s*"literature"\s*"hypotheses"\s*"design"\s*"experiment"/);
  assert.match(css, /\.literature-table td\s*\{[^}]*grid-template-columns:\s*82px minmax\(0, 1fr\)/s);
});
```

- [ ] **Step 2: Run the CSS contract tests to verify RED**

Run: `node --test tests/ui-contract.test.mjs` from `frontend`

Expected: FAIL because named areas, bounded paper fields, and mobile row layout do not exist.

- [ ] **Step 3: Add named grid placement and bounded desktop fields**

Update `.workspace-grid`:

```css
.workspace-grid {
  display: grid;
  grid-template-columns: minmax(300px, 0.95fr) minmax(420px, 1.45fr);
  grid-template-areas:
    "topic literature"
    "hypotheses hypotheses"
    "design experiment";
  gap: 14px;
  align-content: start;
  align-items: start;
}

.research-topic-card { grid-area: topic; }
.literature-card { grid-area: literature; }
.hypothesis-card { grid-area: hypotheses; }
.design-card { grid-area: design; }
.experiment-runner-card { grid-area: experiment; }
```

Add:

```css
.history-item-row { min-height: 44px; }
.history-item { min-width: 0; min-height: 44px; overflow: hidden; }
.history-item strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.literature-table { table-layout: fixed; }
.paper-title-column { width: 52%; }
.paper-author-column { width: 28%; }
.paper-id-column { width: 20%; }
.paper-title,
.paper-authors,
.paper-identifier { min-width: 0; }
.paper-title {
  display: -webkit-box;
  overflow: hidden;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  line-height: 1.35;
}
.paper-authors { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.paper-identifier { display: block; overflow-wrap: anywhere; color: #0756d6; font-weight: 700; }
```

Remove the old generic `grid-column: span 1` card grouping that conflicts with named areas.

- [ ] **Step 4: Add the approved narrow-screen layout**

Inside `@media (max-width: 960px)` add:

```css
.workspace-grid {
  grid-template-areas:
    "topic"
    "literature"
    "hypotheses"
    "design"
    "experiment";
}

.literature-table colgroup,
.literature-table thead { display: none; }
.literature-table,
.literature-table tbody,
.literature-table tr,
.literature-table td { display: block; width: 100%; }
.literature-table tr { padding: 8px 0; border-bottom: 1px solid #e6edf6; }
.literature-table td {
  display: grid;
  grid-template-columns: 82px minmax(0, 1fr);
  gap: 8px;
  border: 0;
  padding: 4px 0;
}
.literature-table td::before {
  content: attr(data-label);
  color: #334155;
  font-weight: 800;
}
```

- [ ] **Step 5: Run all frontend tests and build**

Run from `frontend`:

```powershell
node --test tests/presentation.test.ts tests/ui-contract.test.mjs
npm run build
```

Expected: 12 tests pass, 0 fail; `tsc -b && vite build` exits 0.

- [ ] **Step 6: Verify desktop and mobile behavior in the browser**

Run `npm run dev -- --host 127.0.0.1`, open the local page, and verify:

- Desktop: A left/B right, C full width, D left/E right; literature has only three columns; no competition pill.
- History modal: each research question is one bounded line, deletion buttons align, and hover reveals the full question.
- Width 960px or narrower: A–E are one column; each paper shows three stacked labeled fields; no horizontal page overflow.

- [ ] **Step 7: Commit Task 3**

```powershell
git add -- frontend/src/styles.css frontend/tests/ui-contract.test.mjs
git commit -m "fix: align workflow card layout"
```

