# Round 6.1 Dynamic Literature / Hypothesis Revision Report

Date: 2026-08-16

Scope: code plus mock/synthetic validation only. No real research query, literature retrieval, experiment, real Run mutation, or Git commit was executed.

## 1. Dynamic literature coverage

The previous policy contained fixed defaults: `max_query_count=5`, `max_results_per_query=8`, `max_results_per_source=30`, `max_candidate_count=60`, and `max_core_reference_count=24`.

The policy now uses configurable 20/20/100/100/24 operational hard caps. They are not scientific completion criteria. `core_references` is explicitly a compact legacy/UI view, not the input to research synthesis. The verified bounded collection is persisted as `references`, then synthesized.

`literature_coverage` persists retrieved/verified counts; theme, method, conclusion, limitation, and future-work coverage; gap stability; new-information rate; coverage and saturation scores; decision; hard cap; and `hard_cap_reached`. `hard_cap_reached` is distinct from `saturated` and never claims that coverage is sufficient. Synthetic 12, 45, and 80 paper collections all processed successfully.

## 2. Complete Gap processing

`synthesis_prompt_context` no longer skips trailing records after a character budget is consumed. `build_gap_processing_pipeline` assigns every Gap to one deterministic detailed batch, persists it, then emits a bounded secondary structural synthesis.

Persisted contract:

```json
{"total_gap_count": 0, "processed_gap_count": 0, "batch_count": 0, "source_gap_ids": [], "gap_coverage": 1.0}
```

Full batch detail stays in the Research Synthesis Artifact. An over-budget complete representation raises a contract error instead of silently losing lineage. Synthetic 6, 45, and 90 Gap cases all satisfied `processed_gap_count == total_gap_count` and `gap_coverage == 1.0`.

This repair also fixed a real boundary mismatch: knowledge integration emits Pydantic `EvidenceCard` objects while persisted checkpoint/API paths contain dicts. Synthesis now accepts both representations, preventing verified literature from being silently discarded before Gap creation.

## 3. Append-only hypothesis rounds

`hypothesis_revision_required` recovery bypasses destructive `_rerun_from("hypothesis_generation")`. It appends a new hypothesis/evidence pair. Each new round stores `round_id`, `round_index`, `parent_round_id`, `revision_reason`, `scientific_feedback`, and `created_candidate_ids`.

New prompts receive prior claims, used Gap IDs and evidence feedback. A locked reasoning Artifact locks only its own parent hypothesis round; it cannot suppress evidence reasoning for the new round. User-supplied revisions preserve historic Artifacts too.

The persisted-checkpoint synthetic test verified Round 1 H1/reasoning/evidence/assessment followed by revision and Round 2 H5/new reasoning/new evidence, with all Round 1 hypothesis, evidence, reasoning, assessment, selection and plan Artifacts retained.

## 4. Strong provenance gate

New generated candidates with missing `source_gap_ids` fail validation with `CANDIDATE_PROVENANCE_REQUIRED`; unknown IDs fail with `CANDIDATE_PROVENANCE_UNKNOWN_GAP`. Both take the existing repair path and are rejected at its limit, rather than saved with invented provenance.

The engine derives paper, claim and future-work IDs only from persisted source gaps. No index fallback, first-Gap fallback, arXiv-to-EVID mapping, or synthetic lineage was added. Historic data remains `provenance_status=unavailable`; a legacy regression confirms missing fields are not fabricated.

## 5. Frontend adaptation

Research Map displays actual literature count and coverage state: `Coverage sufficient`, `Coverage evaluation continues`, or `Hard cap reached · Coverage incomplete`. It exposes persisted hypothesis rounds and derived future-work lineage. Legacy records still load without synthetic data.

## 6. Validation

| Check | Result |
| --- | --- |
| Focused synthetic regression | 7 passed |
| Full backend regression | 533 passed, 2 skipped, 0 failures (90.521s) |
| Frontend TypeScript/Vite build | passed |
| Research Map synthetic fixture | passed |

The backend command was scoped to `tests/backend` with pytest `--import-mode=importlib`, avoiding same-name collection collisions from the archived Round 5 package without changing test behavior.

## 7. Safety confirmation

- No real research problem, literature retrieval, Fashion-MNIST E2E, or real Run was run or modified.
- No Git commit was created.
