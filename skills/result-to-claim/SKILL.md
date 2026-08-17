---
name: result-to-claim
description: Decide which planned claims are supported, partial, or failed using an audited experiment result. Use after experiment analysis and integrity audit, before any plan revision or report export.
allowed-tools: read_run, read_artifact, audit_result
---

# Result to Claim

Read the accepted hypothesis and its exact plan lineage together with the validated analysis and audit. Execution success alone is not scientific support.

For each claim, record the method used to judge it, the measured basis, the decisive metric or comparison, and the remaining uncertainty. Check failed attempts, baseline strength, seed variance, missing controls, effect size, decision thresholds, and external-validity limits.

## Output Contract

Return `verdict` as `supported`, `partial`, or `failed`; also return `feedback`, `required_revision`, `supported_claims`, `unsupported_claims`, `revisions`, `next_action`, `evidence_links`, and `overclaim_risks`. Make every revision traceable to a named metric, audit issue, or unsupported claim. Do not strengthen language beyond the measured result.

## Acceptance

`partial` and `failed` are valid completed outputs. Put future evidence in `revisions` and `next_action`; never describe an unrun experiment as evidence. ReviewerAgent performs the independent semantic check.

## Output Language

When the research question is Chinese, write every user-facing narrative field in Simplified Chinese: `feedback`, `required_revision`, `next_action`, `revisions`, `supported_claims`, `unsupported_claims`, `overclaim_risks`, and every field in `result_analysis`. Keep machine enums, JSON keys, error codes, raw metric keys, and English academic search queries unchanged.
## Step 6 dual-review boundary

Use only the authoritative scientific statuses `SUPPORTED`, `CONTRADICTED`,
`INCONCLUSIVE`, and `REFINEMENT_REQUIRED`. Independent reviews are inputs to a
deterministic disagreement report, not votes. When they disagree, report the
disagreement and request more evidence; do not select a model or overstate a claim.
