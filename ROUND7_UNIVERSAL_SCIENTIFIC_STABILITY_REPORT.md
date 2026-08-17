# Round 7 — Universal Scientific Stability Architecture

## Implementation facts

1. `backend/app/workflow/scientific_stability.py` introduces a domain-neutral Scientific Claim contract. A hard constraint without non-`none` provenance, source IDs, and `supported`/`verified` status is retained only as `provisional`; `hard_constraint_issues()` rejects it as a formal contract.
2. `unknown` is a first-class claim status. Dataset semantic fields are emitted as explicit unknown claims when structural inspection cannot establish their meaning.
3. `annotate_dataset_semantics()` preserves structural facts separately from semantic facts. `readiness_state()` routes unresolved semantic facts to `needs_verification` / `dataset_verification`, rather than fabricating a value or reporting a system failure.
4. `infer_research_profile()` and its applicability map distinguish empirical, computational, simulation, literature-synthesis, and mathematical research. Mathematical and literature-synthesis profiles mark dataset/training as not applicable.
5. `protocol_state()` uses only generic scientific roles (`primary_outcome`, `secondary_outcome`, `operating_condition`, `success_criterion`, `failure_criterion`, `statistical_support`, `control`, `baseline`). No domain metric name is present in the resolver.
6. The Research Plan entry now evaluates hypothesis readiness. Explicit `EVIDENCE_INSUFFICIENT + mechanism_gate=FAIL + recommendation=REVISE` produces `NEEDS_EVIDENCE`, a durable `hypothesis_readiness` artifact, and no plan/experiment task.
7. Planning stores `research_profile`, `protocol_state`, `readiness_state`, and the next progressive scientific stage. Stages are resolved as VERIFY/PILOT/MAIN/CONFIRM from readiness and applicability.
8. Every plan draft is persisted as an append-only `research_plan_candidate` with plan ID, round, parent plan ID, stage, normalized plan, provider/model metadata, and status. Every review is persisted with plan ID, round, review ID, and issue ledger. Final plans retain `plan_candidate_id` lineage.
9. `merge_issue_ledger()` carries prior issues forward, marks omitted prior issues resolved, and converts an unqualified new blocking issue in later rounds into a non-blocking issue with an explicit ledger reason.
10. Review exhaustion no longer raises `RESEARCH_PLAN_REVIEW_EXHAUSTED` into a failed Run. It stores `plan_revision_required` with the latest plan/review IDs and ledger, then places the Run in `NEEDS_PLAN_REVISION` with `recoverable=true`.
11. Provider/schema/transport classification is separate from scientific review. Plan-review provider failures retain the candidate and failure record under `RECOVERABLE_PROVIDER_ERROR`; actual engine system failures use `FAILED_SYSTEM`. Legacy custom engines keep their historical `failed` status for compatibility.
12. Plan-review context now contains only `selected_hypothesis_evidence_digest`, not `candidate_assessments[:5]`. The digest has deterministic overflow metadata; `context_telemetry` records component records/chars/tokens/budget/status. Literature selection exposes omitted record IDs with `secondary_digest_required` rather than silently dropping them.
13. `ScientificWorldState` is persisted as the compact current state in `RunRecord.scientific_world_state` and as append-only `scientific_world_state` artifacts. Existing artifacts remain the source of truth.
14. Dataset binding continues to lock the resolved selected directory and fingerprint; plan binding now carries semantic facts with the exact bound profile. The existing selected-directory regression remains green.
15. GitHub URL creation/API persistence and read-only inspection remain intact. An absent URL does not inspect; an unavailable URL persists a warning and research continues without synthetic code evidence.

## Required answers

1. Yes — provenance-less hard constraints are prohibited from becoming hard contracts.
2. Yes — UNKNOWN is a formal scientific status.
3. Yes — dataset UNKNOWN becomes a verification route, not a guess.
4. Yes — Research Profile marks non-applicable capabilities and skips their experimental assumptions.
5. Yes — Engine/protocol resolver contains no fixed scientific metric vocabulary.
6. No — explicit evidence-insufficient, mechanism-failed, revise-required hypotheses cannot enter MAIN/formal planning.
7. Yes — the plan stores and routes the next progressive stage rather than requiring a final protocol at VERIFY.
8. Yes — reviews use a convergent issue ledger.
9. No — REVISE exhaustion becomes `NEEDS_PLAN_REVISION`, not Run failure.
10. Yes — plan candidates and reviews are append-only with parent lineage.
11. Yes — unresolved scientific states and `FAILED_SYSTEM` are separated.
12. Yes — plan-review context has component telemetry and explicit overflow behavior.
13. Yes — unselected 60k-character candidate assessments are excluded from selected-plan context.
14. Yes — GitHub URL persistence is retained through frontend/API/Run creation/checkpoint model compatibility.
15. Yes — optional GitHub failure is warning-only and non-blocking.
16. Yes — the dataset contract uses the resolved user-selected directory, not its parent catalog directory.
17. Yes — provider failures are classified separately from scientific REVISE/revision ledger state.
18. Yes — plan/review and world-state artifacts are append-only; no destructive continuation path was added.
19. Yes — absent `github_repository_url` and absent `scientific_world_state` load through Pydantic defaults.

## Cross-domain stability matrix and invariants

`tests/backend/test_universal_scientific_stability.py` covers the universal matrix with deterministic fixtures:

| Scenario | Result |
| --- | --- |
| Empirical classification-like data | empirical profile, no GitHub requirement |
| Paper/source improvement-like task | source remains optional; no hard operating point is manufactured |
| Continuous-target regression | empirical profile; resolver emits no metric-specific contract |
| Forecasting | hybrid profile; no classification protocol is injected |
| Literature/theoretical synthesis | dataset/training not applicable |
| Mathematical problem | dataset/training not applicable |
| Dataset semantic UNKNOWN | `NEEDS_VERIFICATION` / VERIFY |
| Evidence insufficient | `NEEDS_EVIDENCE`, never MAIN |
| Review convergence | prior issues retained/resolved; unqualified new blocker downgraded |
| Review exhaustion | `NEEDS_PLAN_REVISION`, recoverable |
| Negative/inconclusive scientific result | represented as scientific outcome states, not `FAILED_SYSTEM` |
| GitHub unavailable | warning-only continuation (existing optional-source regression) |
| Context overflow | bounded selected digest and explicit telemetry |
| Provider timeout/failure | preserved candidate + `RECOVERABLE_PROVIDER_ERROR` |
| Malformed structured output | existing supervisor structured-output validation/revision path remains covered by backend regression |
| Checkpoint continuation | append-only plan/review candidate lineage remains present after recoverable stop |

The focused Round 7 test set verifies INV-1 through INV-10: scientific unresolved states do not become `FAILED_SYSTEM`; hard contracts require provenance; UNKNOWN is stable without evidence; non-ready evidence does not route to MAIN; review exhaustion and optional sources are recoverable; over-budget context is explicit; revisions retain historical artifacts; not-applicable capabilities are not forced; and profiles/resolver contain no fixed metric names.

## Test facts

- Focused Round 7 + plan-review + dataset + optional-source + orchestration tests: `33 passed`.
- Full backend regression: `546 passed, 2 skipped`.
- Frontend production build: passed (`pnpm build`).
- No real research Run was created, resumed, or modified by this Round.
- No real research E2E was executed.
- No Git commit was created.

ROUND 7 IMPLEMENTATION COMPLETE

No scientific metric was hardcoded for a specific domain.
Unknown information is never silently promoted to fact.
Scientific unresolved states do not become system failures.
Optional source failures do not block research.
Review revisions preserve full lineage.
Context is bounded with no silent scientific truncation.
No real Run was modified or resumed.
No real research E2E was executed.
No Git commit was created.
