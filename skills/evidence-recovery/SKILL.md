---
name: claim-evidence-recovery
description: Build claim-level evidence, analyze research gaps, and recover promising hypotheses through bounded targeted retrieval.
allowed-tools: read_run, read_artifact, literature_search, search_local_literature
---

# Claim-level evidence and recovery

Treat a paper as a source, never as evidence by itself. Extract a specific, traceable
claim from its title, abstract, or available text. Each evidence record must retain
`evidence_id`, `paper_id`, `claim`, `evidence_type`, `stance`, `relation`, relevance,
confidence, source quality, and a source location.

Use only these evidence types: `METHOD`, `MECHANISM`, `RESULT`, `LIMITATION`,
`RESEARCH_GAP`, `DATASET_OBSERVATION`, and `COMPUTATIONAL_COST`. A claim may be
`support`, `contradict`, or `neutral`; label its connection `DIRECT` or `INDIRECT`.
Do not claim a result absent from the source text.

Before hypothesis generation, summarize solved problems, known methods, limitations,
contradictions, untested settings, feasible combinations, and experimentable gaps.
Every ResearchGap must link to supporting evidence IDs. A new hypothesis must state:
motivation, component mechanism, research gap, novel inference, experimental
prediction, testability, feasibility, and expected contribution.

The novel inference is expected to be unproven. Evidence must support its motivation,
components, and gap; it must not be required to pre-prove the final experimental
outcome. Citations outside the verified registry are `unverified` and trigger
verification/retrieval; they are not formal evidence.

For each candidate, build a Candidate Evidence Map by matching evidence claims to
candidate claims (mechanism and gap), not merely by paper title or URL. Preserve
supporting, contradicting, missing, and unverified entries.

Critic decisions are exactly `GO`, `REVISE`, `PIVOT`, `STOP`, or
`EVIDENCE_INSUFFICIENT`. These are the production Idea Selection API contract.
Use `EVIDENCE_INSUFFICIENT` for an otherwise worthwhile candidate lacking
motivation, mechanism, or gap evidence. Preserve the targeted-retrieval plan in
`unknowns`, `mde`, and the candidate-specific evidence record: missing claims,
queries, required source type, and why each item is needed. Do not use
`TARGETED_RETRIEVAL` as a decision literal. Use `STOP` only with a concrete
scientific, feasibility, policy, or contradictory-evidence reason.

When no candidate can enter experiment, perform candidate-specific retrieval and
re-extract evidence before returning `NO_SELECTABLE_HYPOTHESIS`. The recovery limit
is configured by the workflow (maximum two rounds). Exhaustion rejects only that
candidate as `REJECTED_EVIDENCE_UNAVAILABLE`; other candidates remain eligible.
