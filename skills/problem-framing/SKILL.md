---
name: problem-framing
description: Convert a research request into a bounded and testable problem contract.
allowed-tools: read_run, read_artifact
---

# Problem Framing

Identify the research object, desired outcome, constraints, available evidence, compute budget, and unresolved assumptions. Separate user requirements from inferred assumptions.

Return a structured object with:

- `problem_statement`: one falsifiable research problem.
- `constraints`: explicit data, compute, time, deployment, and reporting limits.
- `knowledge_gaps`: questions that require literature or experiment evidence.
- `literature_queries`: 3–5 non-overlapping objects with `query`, `intent`, and
  `target_gap`. Use concise English academic queries. Intents may include
  BASELINE, DIRECT_METHOD, MECHANISM, BENCHMARK, EVALUATION,
  CONTRADICTORY_EVIDENCE, and RELATED_APPLICATION. Each query must resolve a
  distinct knowledge gap rather than paraphrase another query.

Do not search literature, choose a final hypothesis, or design an experiment in this step.
