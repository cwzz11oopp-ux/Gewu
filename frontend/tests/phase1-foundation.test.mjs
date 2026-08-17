import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const source = (path) => readFile(new URL(`../${path}`, import.meta.url), "utf8");

test("blocking preflight never starts the production pipeline", async () => {
  const app = await source("src/App.tsx");
  const start = app.slice(app.indexOf("async function startResearch"), app.indexOf("async function loadRun"));
  assert.match(start, /await api\.preflightRun\(current\.id\)/);
  assert.match(start, /if \(preflight\.blocking\)[\s\S]*throw new Error/);
  assert.ok(start.indexOf("preflight.blocking") < start.indexOf("api.startPipeline"));
});

test("passed preflight starts the production pipeline", async () => {
  const app = await source("src/App.tsx");
  const start = app.slice(app.indexOf("async function startResearch"), app.indexOf("async function loadRun"));
  assert.match(start, /current = await api\.startPipeline\(current\.id\)/);
});

test("provider HTTP errors preserve provider code and detail for the user", async () => {
  const app = await source("src/App.tsx");
  const start = app.slice(app.indexOf("async function startResearch"), app.indexOf("async function loadRun"));
  assert.match(start, /`\$\{item\.name\}: \$\{item\.code \|\| item\.detail \|\| "不可用"\}`/);
  assert.match(app, /setErrorMessage\(userFacingError\(error\)\)/);
});

test("legacy runs remain loadable without Phase 1 fields", async () => {
  const app = await source("src/App.tsx");
  const types = await source("src/api/types.ts");
  assert.match(types, /research_constraints\?:/);
  assert.match(types, /research_constraints_artifact_id\?:/);
  assert.match(app, /problem: loaded\.problem_input/);
  assert.match(app, /constraints: loaded\.constraints \|\| ""/);
});
