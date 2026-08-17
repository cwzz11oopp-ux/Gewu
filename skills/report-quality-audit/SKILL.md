---
name: report-quality-audit
description: Internally audit a generated Chinese research report for factual grounding, evidence integrity, reproducibility, coherence, and restrained academic style before export. Use during report export after section drafting; do not expose the audit rubric or scores in the reader-facing report.
---

# Report Quality Audit

Audit report quality rather than guessing whether text was written by AI.

## Priority order

1. Verify facts and evidence against the internal research-state ledger.
2. Verify scientific completeness and reproducibility.
3. Verify logic and chapter coherence.
4. Improve style without treating style signals as proof of authorship.

## Research-state discipline

Treat the ledger as a general provenance system, never as a topic-specific patch.

- Require every process artifact except the ledger snapshot itself to have a ledger entry.
- Keep artifact content immutable; use `content_sha256` to identify the exact recorded version.
- Read `lifecycle_status` and `validity_status` independently. Historical does not mean false,
  and active does not automatically mean verified.
- Use active, verified experiment facts as report facts. Use superseded or historical values
  only when explicitly describing the research history.
- Prefer the authority order in the ledger when plan, hypothesis, execution, and prose differ.
- Do not silently choose between conflicting values. Use the canonical value and describe the
  earlier value only as a documented change when that history matters.
- Never copy ledger IDs, fingerprints, paths, status dictionaries, or audit metadata into the
  reader-facing report.

## Hard failures

Return a hard failure only when the report contains at least one unresolved defect that can invalidate or expose the deliverable:

- a numeric claim contradicts the audited experiment artifact;
- percent, proportion, and percentage-point units are confused;
- a result, parameter, statistical test, causal explanation, or citation is invented;
- a negative or inconclusive result is presented as support;
- a conclusion exceeds the stated dataset, model, parameter, seed, or evaluation scope;
- an unverified reference is used as evidence, or a citation cannot be linked to the final reference list;
- a required core chapter is absent or contains no substantive content;
- local paths, hashes, secrets, commands, internal IDs, provider fields, or audit dictionaries leak into reader-facing prose;
- two chapters make materially contradictory claims about the same experiment.

For every hard failure, identify the chapter, paragraph, exact claim, conflicting source fact, and required correction. Never infer a hard failure from tone or a single phrase.

Use only these stable hard-failure codes:

- `numeric_mismatch`
- `unit_mismatch`
- `fabricated_fact`
- `fabricated_citation`
- `verdict_inversion`
- `scope_overreach`
- `unverified_reference`
- `missing_core_section`
- `internal_leak`
- `cross_section_contradiction`

Every hard-failure object must contain a valid `code`, `section_id`, non-negative integer
`paragraph_index`, `claim`, `source_path`, `source_fact`, and `required_correction`.
`source_path` must resolve to an existing field in the supplied fact sheet, and `claim` must be
an exact substring of the identified paragraph in the current draft. A label such as
`grounding`, `quality`, `style`, `possibly unsupported`, or `needs checking` is not a hard
failure. Put underspecified concerns in `revision_required` instead.

## Revision-required issues

Request a targeted paragraph or chapter revision when:

- reasoning jumps from background to conclusion without evidence;
- an experiment iteration omits why a change was made or what changed afterward;
- results are listed without comparison to the predefined decision threshold;
- discussion repeats results instead of interpreting them;
- limitations are generic rather than tied to the actual design;
- the conclusion does not directly answer the research question;
- prose is predominantly English although a Chinese report was requested;
- paragraphs or chapters are exact or near duplicates.

## Soft style issues

Treat the following only as revision suggestions. They must never block export by themselves:

- conventional transitions such as “值得注意的是”“本文旨在”“综上所述”;
- isolated phrases such as “具有重要意义”;
- a paragraph that is shorter than the preferred range;
- repeated use of “本研究”“研究结果表明”;
- uniform paragraph length, mechanical transitions, excessive headings, or excessive bullets;
- formal, predictable, or simple language.

Judge these expressions in context. Revise them only when they introduce no fact, repeat prior content, exaggerate significance, or make the prose mechanical.

## Section rubric

Score each chapter from 1 to 5 for:

- factual grounding;
- evidence sufficiency;
- scientific completeness;
- logical coherence;
- specificity;
- reproducibility;
- natural academic Chinese.

Use scores to choose revision targets, not as an authorship detector. A style score alone cannot cause a hard failure.

## Audit workflow

1. Confirm that the ledger covers every supplied process artifact other than its own snapshots.
2. Compare every numeric and scientific claim with the canonical fact sheet and its verified
   source artifact.
3. Build an internal claim-to-evidence map.
4. Check chapter responsibilities and cross-chapter consistency.
5. Separate hard failures, revision-required issues, and soft style issues.
6. Provide complete replacement paragraphs only for passages that need changes.
7. Preserve correct numbers, uncertainty, negative findings, and citation identifiers.
8. Apply targeted replacement paragraphs for evidence-complete hard failures.
9. Re-audit the repaired report once. Return only evidence-complete hard failures that remain
   in the repaired text; do not repeat an issue that the replacement already removed.
10. If an evidence-complete defect remains, preserve the repaired draft and full failure object
   for diagnosis before blocking export.

Do not expose this audit, its scores, intermediate drafts, or internal reasoning in the final Word report.
