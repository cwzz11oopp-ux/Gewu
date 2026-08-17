---
name: research-review
description: Critically evaluate candidate hypotheses against evidence quality, feasibility, and competition value.
allowed-tools: read_run, read_artifact, audit_evidence
---

# Research Review

Evaluate each candidate on evidence support, novelty, technical coherence, falsifiability, resource feasibility, and relevance to the competition objective. Distinguish fatal contradictions from fixable uncertainty.

Audit every important claim against the runtime evidence registry. Record whether each link is direct support, indirect support, analogy, context, or contradiction. A real citation is not sufficient when its reported task, dataset, mechanism, or outcome does not entail the candidate claim.

Return:

- `active_hypothesis`: the selected candidate with explicit rationale;
- `rejected_candidates`: reason for each rejection;
- `evidence_gaps`: unresolved claims that affect the selection;
- `risks`: ranked technical and evaluation risks;
- `claim_evidence_map`: atomic claim to verified evidence ID, stance, relation, strength, and limitation;
- `unsupported_claims`: claims that remain assumptions or need targeted retrieval;
- `review_summary`: concise decision record.

Do not invent citations or experimental outcomes. Semantic acceptance is performed separately by ReviewerAgent with a fresh Qwen context.

When evidence is insufficient, return concrete, search-ready `required_evidence` items rather than generic requests such as "more literature". Name the task, mechanism, dataset or evaluation relation that must be verified. The workflow may run at most one targeted retrieval pass and then repeat the review with the expanded verified registry.
