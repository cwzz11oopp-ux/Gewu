---
name: novelty-check
description: Compare candidate hypotheses with verified literature and identify novelty risks.
allowed-tools: read_run, read_artifact, literature_search, audit_evidence
---

# Novelty Check

For each candidate, identify the closest verified prior work, overlapping mechanism, differing assumption, and remaining contribution. Use external search only to close a concrete evidence gap.

Use only evidence IDs present in the runtime evidence registry. Compare problem, mechanism, method, evaluation setting, and claimed contribution separately. A source from an adjacent task is analogy evidence, not direct novelty evidence.

Return `closest_work`, `overlap`, `difference`, `novelty_risk`, `unsupported_claims`, `required_checks`, and `claim_evidence_map`. Do not label a candidate novel merely because no result was found. Missing evidence increases uncertainty.
