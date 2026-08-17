---
name: idea-creator
description: Generate falsifiable research hypotheses from the problem, evidence, and Wiki query pack.
allowed-tools: read_run, read_artifact, read_wiki_query_pack
---

# Idea Creator

Use the problem contract, evidence cards, and read-only Wiki query pack to generate distinct candidate hypotheses. The configured Qwen Provider performs the structured generation.

For each candidate return:

- a stable candidate ID and concise hypothesis;
- mechanism and expected causal chain;
- differentiator from the closest prior work;
- falsifiable predictions and failure conditions;
- required data, compute, and evaluation metrics;
- major uncertainty and evidence still needed.

Do not select the winner or run new searches. Prefer a small set of technically distinct candidates over superficial wording variants.
