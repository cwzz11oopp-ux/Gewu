---
name: gap-grounded-hypothesis
description: Generate falsifiable research hypotheses from verified evidence and research gaps without presenting novelty as already proven.
allowed-tools: read_run, read_artifact
---

# Gap-grounded hypothesis generation

Generate three to five technically distinct candidates from the research question,
verified evidence registry, research gaps, known methods and limitations, plus the
dataset and experiment constraints. Do not freely invent an idea from paper titles.

For each candidate return `candidate_id`, `hypothesis`, `motivation`, `research_gap`,
`novel_inference`, `experimental_prediction`, `component_claims`, `required_evidence`,
`supporting_evidence_ids`, `contradicting_evidence_ids`, `targeted_queries`,
`status`, method, mechanism, testability, feasibility, and expected contribution.

Clearly answer: what is known, what is missing, why the change can address the gap,
which parts have verified literature support, which is the novel inference, how it is
falsified, how the experiment works, and what failure would mean. A citation from
model memory is only an unverified candidate citation until verified by retrieval.
