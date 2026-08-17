import assert from "node:assert/strict";import {readFile} from "node:fs/promises";import test from "node:test";
test("results surface preserves download API affordances",async()=>{const p=await readFile(new URL("../src/components/ExperimentPanel.tsx",import.meta.url),"utf8");assert.match(p,/experimentFileUrl/);assert.match(p,/下载结果 JSON/);});
