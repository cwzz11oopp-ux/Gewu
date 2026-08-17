import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

test("Idea workspace retains the complete candidate and evidence navigation surface", async () => {
  const page = await readFile(new URL("../src/components/workspace/IdeaPage.tsx", import.meta.url), "utf8");
  for (const label of ["Candidate Ideas", "综合评分", "证据链", "选择理由", "想法检视器"]) assert.match(page, new RegExp(label));
  assert.match(page, /model\.hypotheses\.map/);
  assert.match(page, /evidenceSources/);
});
