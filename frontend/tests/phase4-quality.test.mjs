import assert from "node:assert/strict";import {readFile} from "node:fs/promises";import test from "node:test";
test("Phase 4 UI uses real artifact surfaces",async()=>{const p=await readFile(new URL("../src/components/workspace/IdeaPage.tsx",import.meta.url),"utf8");assert.match(p,/证据链/);assert.match(p,/evidenceSources/);});
