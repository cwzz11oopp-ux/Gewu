---
name: ablation-planner
description: Propose the minimum targeted ablations or diagnostic baselines needed after a partial or failed audited experiment. Use only when a named unsupported claim has competing explanations that a controlled follow-up can distinguish.
allowed-tools: read_run, read_artifact
---

# Ablation Planner

## Trigger

Use only for a `partial` or `failed` verdict. Rank unresolved causal questions by whether resolving them can change the claim verdict. Turn only the highest-value questions into a controlled ablation, sensitivity test, or diagnostic baseline.

## Output Contract

For each proposal return the unsupported claim, competing explanations, method, evidence basis, changed variable, fixed controls, metric and threshold, seed policy, expected positive and negative interpretation, compute estimate, and priority. Prefer the smallest set that changes a reviewer belief. Parameters belong in the experiment record, not the experiment ID.

## Boundaries

Do not rerun an unchanged failed experiment, bundle optional ablations into the primary run, or propose work that cannot affect the verdict. Every follow-up must fit the configured compute budget.
