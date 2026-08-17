# Plan-Linked Experiment Console Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make experiment execution render the selected experiment plan's evaluation definitions and fix candidate-hypothesis card overflow.

**Architecture:** A pure presentation helper converts plan evaluations and actual result metrics into stable rows, plus optionally identifies a real numeric series. React components consume only those helpers and change layout classes; no backend contract or experiment execution path changes.

**Tech Stack:** React 19, TypeScript, Vite, Node built-in test runner, CSS Grid.

## Global Constraints

- Do not change backend experiment execution, plan normalization, or API payloads.
- Never hardcode domain-specific experiment metrics, thresholds, units, or mock chart points.
- Use only `plan.evaluations` and `experiment_result.metrics` as the metric sources.
- Candidate hypotheses, experiment design, and experiment execution must span both desktop workspace columns; below 960px the workspace remains one column.
- The execution SVG is rendered only from an actual numeric result sequence containing at least two finite values.

---

## File Structure

- Modify: `frontend/src/utils/presentation.ts` — pure, testable evaluation-row and series extraction helpers.
- Modify: `frontend/tests/presentation.test.ts` — behavioral coverage for helper edge cases.
- Modify: `frontend/src/components/ExperimentPanel.tsx` — consume plan and result artifacts, expose lifecycle states, render dynamic table/chart.
- Modify: `frontend/src/components/ArtifactEditor.tsx` — provide a semantic anchor class for the design-to-execution sequence.
- Modify: `frontend/src/styles.css` — full-width cards, no-overflow candidate grid, responsive execution layout and chart empty state.
- Modify: `frontend/tests/ui-contract.test.mjs` — source-level contract regressions for the new UI behavior.

### Task 1: Derive plan-linked metric display data

**Files:**
- Modify: `frontend/src/utils/presentation.ts`
- Modify: `frontend/tests/presentation.test.ts`

**Interfaces:**
- Produces: `ExperimentMetricRow`, `buildExperimentMetricRows(plan, resultMetrics)`, and `findMetricSeries(resultMetrics)`.
- Consumed by: `frontend/src/components/ExperimentPanel.tsx`.

- [ ] **Step 1: Write the failing tests**

```ts
import { buildExperimentMetricRows, findMetricSeries } from "../src/utils/presentation.ts";

test("plan metric rows retain order and merge matching result values", () => {
  assert.deepEqual(
    buildExperimentMetricRows(
      { evaluations: [
        { metric: "测试准确率", direction: "↑", method: "p<0.05" },
        { metric: "标准差", direction: "↓", method: "3 seeds" },
      ] },
      { "测试准确率": 0.93 },
    ),
    [
      { key: "测试准确率", label: "测试准确率", direction: "↑", criterion: "p<0.05", result: "0.93", source: "plan" },
      { key: "标准差", label: "标准差", direction: "↓", criterion: "3 seeds", result: "待运行", source: "plan" },
    ],
  );
});

test("result-only metrics append after plan metrics", () => {
  assert.deepEqual(
    buildExperimentMetricRows({ evaluations: [{ metric: "loss" }] }, { loss: 0.2, elapsed_seconds: 12 }),
    [
      { key: "loss", label: "loss", direction: "未指定", criterion: "未指定", result: "0.2", source: "plan" },
      { key: "elapsed_seconds", label: "elapsed_seconds", direction: "结果输出", criterion: "运行结果未在实验设计中声明", result: "12", source: "result" },
    ],
  );
});

test("only finite numeric sequences qualify as a chart series", () => {
  assert.deepEqual(findMetricSeries({ notes: ["a", "b"], accuracy: [0.7, 0.8, 0.9] }), { key: "accuracy", values: [0.7, 0.8, 0.9] });
  assert.equal(findMetricSeries({ accuracy: [0.7], loss: [0.1, Number.NaN] }), null);
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pnpm exec tsx --test tests/presentation.test.ts`

Expected: FAIL because `buildExperimentMetricRows` and `findMetricSeries` are not exported.

- [ ] **Step 3: Implement the smallest pure helpers**

```ts
export type ExperimentMetricRow = {
  key: string;
  label: string;
  direction: string;
  criterion: string;
  result: string;
  source: "plan" | "result";
};

export function buildExperimentMetricRows(plan: Record<string, unknown> | undefined, resultMetrics: Record<string, unknown>) {
  const rows: ExperimentMetricRow[] = [];
  const known = new Set<string>();
  const evaluations = Array.isArray(plan?.evaluations) ? plan.evaluations : [];
  for (const evaluation of evaluations) {
    if (!evaluation || typeof evaluation !== "object" || Array.isArray(evaluation)) continue;
    const record = evaluation as Record<string, unknown>;
    const key = normalizedString(record.metric, "");
    if (!key || known.has(key)) continue;
    known.add(key);
    rows.push({ key, label: key, direction: normalizedString(record.direction, "未指定"), criterion: normalizedString(record.method, "未指定"), result: key in resultMetrics ? String(resultMetrics[key]) : "待运行", source: "plan" });
  }
  for (const [key, value] of Object.entries(resultMetrics)) {
    if (known.has(key)) continue;
    rows.push({ key, label: key, direction: "结果输出", criterion: "运行结果未在实验设计中声明", result: String(value), source: "result" });
  }
  return rows;
}

export function findMetricSeries(resultMetrics: Record<string, unknown>) {
  for (const [key, value] of Object.entries(resultMetrics)) {
    if (Array.isArray(value) && value.length >= 2 && value.every((item) => typeof item === "number" && Number.isFinite(item))) {
      return { key, values: value };
    }
  }
  return null;
}
```

- [ ] **Step 4: Run the presentation tests to verify they pass**

Run: `pnpm exec tsx --test tests/presentation.test.ts`

Expected: PASS with all presentation tests green.

- [ ] **Step 5: Commit Task 1**

```bash
git add frontend/src/utils/presentation.ts frontend/tests/presentation.test.ts
git commit -m "feat: derive experiment metrics from plan"
```

### Task 2: Render the linked plan and execution console without overflow

**Files:**
- Modify: `frontend/src/components/ExperimentPanel.tsx`
- Modify: `frontend/src/components/ArtifactEditor.tsx`
- Modify: `frontend/src/styles.css`
- Modify: `frontend/tests/ui-contract.test.mjs`

**Interfaces:**
- Consumes: `buildExperimentMetricRows(plan, resultMetrics)` and `findMetricSeries(resultMetrics)` from `presentation.ts`.
- Produces: D/E linked visual states and responsive C/D/E layout.

- [ ] **Step 1: Write the failing UI contract tests**

```js
test("execution derives its rows and optional chart from plan and result artifacts", async () => {
  const experimentPanel = await readSource("src/components/ExperimentPanel.tsx");
  assert.match(experimentPanel, /artifact\.type === "plan"/);
  assert.match(experimentPanel, /buildExperimentMetricRows\(plan, metrics\)/);
  assert.match(experimentPanel, /findMetricSeries\(metrics\)/);
  assert.match(experimentPanel, /series \? \(/);
  assert.doesNotMatch(experimentPanel, /20,140 55,105/);
});

test("hypotheses and plan-linked experiment cards span the desktop workspace", async () => {
  const styles = await readSource("src/styles.css");
  assert.match(styles, /\.hypothesis-card,[\s\S]*\.design-card,[\s\S]*\.experiment-runner-card[\s\S]*grid-column: 1 \/ -1/);
  assert.match(styles, /\.hypothesis-grid[\s\S]*repeat\(2, minmax\(0, 1fr\)\)/);
  assert.doesNotMatch(styles, /repeat\(2, minmax\(240px, 1fr\)\)/);
});
```

- [ ] **Step 2: Run the UI contract tests to verify they fail**

Run: `node --test tests/ui-contract.test.mjs`

Expected: FAIL because the current panel does not read the plan or helper functions and CSS uses a fixed 240px candidate-card minimum.

- [ ] **Step 3: Implement the component and CSS changes**

```tsx
const plan = [...artifacts].reverse().find((artifact) => artifact.type === "plan")?.content;
const metricRows = buildExperimentMetricRows(plan, metrics);
const series = findMetricSeries(metrics);
```

Render an empty metrics message only when `metricRows.length === 0`; otherwise render columns “指标 / 方向 / 判定方式 / 结果”. Replace static SVG points with `series.values` mapped to the SVG view box, and render `<div className="chart-empty-state">本次任务未提供趋势数据</div>` when `series` is null. Add `plan-linked-design-card` to the design section and make C/D/E use:

```css
.hypothesis-card,
.design-card,
.experiment-runner-card {
  grid-column: 1 / -1;
}

.hypothesis-grid {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}
```

At `max-width: 960px`, retain the existing single-column grid and make `.runner-grid` one column. Do not change the experiment API, backend files, or plan artifact fields.

- [ ] **Step 4: Run UI contracts and the frontend build**

Run: `node --test tests/ui-contract.test.mjs; pnpm build`

Expected: UI contract tests PASS and Vite exits with code 0.

- [ ] **Step 5: Commit Task 2**

```bash
git add frontend/src/components/ExperimentPanel.tsx frontend/src/components/ArtifactEditor.tsx frontend/src/styles.css frontend/tests/ui-contract.test.mjs
git commit -m "feat: link experiment console to plan metrics"
```

## Final Verification

- [ ] Run `pnpm exec tsx --test tests/presentation.test.ts`.
- [ ] Run `node --test tests/ui-contract.test.mjs`.
- [ ] Run `pnpm build`.
- [ ] Inspect `git diff 7ee0f8a..HEAD -- frontend/src/components/ExperimentPanel.tsx frontend/src/components/ArtifactEditor.tsx frontend/src/styles.css frontend/src/utils/presentation.ts` to confirm no backend or API contract changes.
