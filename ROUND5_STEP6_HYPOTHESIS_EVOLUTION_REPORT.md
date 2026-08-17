# Round 5 Step 6 — Scientific Hypothesis Evolution Loop

## Status

**Completed: YES**

Step 6 adds a bounded, evidence-provenanced scientific interpretation and hypothesis
evolution layer to the existing `feedback_revision` workflow step. It does not relax
Step 1–5 gates and does not run a new Fashion-MNIST E2E.

## Modified architecture

### Added

- `backend/app/workflow/scientific_evolution.py`
  - authoritative scientific-status enum: `SUPPORTED`, `CONTRADICTED`,
    `INCONCLUSIVE`, `REFINEMENT_REQUIRED`;
  - deterministic disagreement detection;
  - deterministic synthesis and bounded evolution decision;
  - lineage-checked working-hypothesis construction.
- `tests/backend/test_scientific_evolution.py`
  - focused provenance, agreement, contradiction, inconclusive/disagreement,
    unavailable-secondary-provider, iteration-limit, and evidence-required tests.
- `ROUND5_STEP6_HYPOTHESIS_EVOLUTION_REPORT.md`.

### Modified

- `backend/app/workflow/engine.py`: extends the existing `feedback_revision` path
  after a validated `experiment_result`.
- `backend/app/agents/critic.py`: adds bounded structured result interpretation,
  with explicit primary/independent provider selection.
- `backend/app/providers/llm.py`: `ModelRoleRouter.generate_json_for_provider()`
  reuses the existing Qwen/DeepSeek OpenAI-compatible abstraction; mock support was
  extended for regression coverage.
- `skills/experiment-iteration/SKILL.md` and `skills/result-to-claim/SKILL.md`:
  align scientific decision literals and the dual-review boundary with runtime code.
- `tests/backend/test_workflow_engine.py`: fixture support for the new structured
  scientific-analysis calls.

## Hypothesis semantics

The two rules now coexist:

1. The original/user hypothesis Artifact remains immutable. The Step 5 user-claim
   anchor remains untouched; no update path changes that Artifact's claim.
2. A scientific revision is a new `working_hypothesis` Artifact, never an overwrite.
   It records `source: scientific_revision`, `parent_hypothesis_id`, `parent_claim`,
   `derived_from`, `revision_reason`, and `hypothesis_revision`.

`build_working_hypothesis()` rejects an empty `derived_from` list with
`SCIENTIFIC_REVISION_EVIDENCE_REQUIRED`. The lineage test demonstrates:

`user hypothesis v1 → validated result / evidence / scientific conclusion → working hypothesis v2`.

The research question remains the stable `problem` Artifact. Step 6 does not implement
research-question reframing.

## Dual-model analysis

At `feedback_revision`, only after an existing validated `experiment_result`:

1. Qwen receives the current hypothesis, accepted plan, validated result, and literature
   evidence and creates `qwen_scientific_analysis`.
2. DeepSeek independently receives the same shared inputs, not Qwen reasoning, and
   creates `deepseek_scientific_review`.
3. If DeepSeek is not configured/routable, an explicit
   `SECONDARY_REVIEW_UNAVAILABLE` Artifact is persisted. No provider is impersonated.

DeepSeek is not used for AST validation, Bundle validation/repair, deterministic
Harness compilation, dataset binding, result IDs, file-path validation, or security
checks.

## Deterministic disagreement and conclusion lineage

`detect_disagreement()` compares hypothesis status, recommended action, confounders,
and evidence gaps. It emits either `SCIENTIFIC_AGREEMENT`,
`SCIENTIFIC_DISAGREEMENT`, or `SECONDARY_REVIEW_UNAVAILABLE`; it never votes or
selects a model.

The persisted lineage is:

`experiment_result`
→ `qwen_scientific_analysis` + `deepseek_scientific_review`
→ `scientific_disagreement`
→ `scientific_synthesis`
→ `scientific_conclusion`
→ `hypothesis_evolution_decision`
→ optional `working_hypothesis`.

`scientific_conclusion` records research-question and hypothesis IDs, the claim,
evidence for/against, limitations, confounders, unresolved questions, confidence, and
all Artifact IDs from which it derives. When analyses disagree, synthesis becomes
`INCONCLUSIVE`, agreement is `LOW`, and the next action is `MORE_EVIDENCE`.

## Bounded evolution policy

The authoritative decisions are:

- `KEEP_HYPOTHESIS` for agreed supported evidence; no duplicate revision is created.
- `REFINE_HYPOTHESIS` for an evidence-backed narrowed proposal.
- `REPLACE_HYPOTHESIS` for an evidence-backed contradiction with a distinct proposal.
- `MORE_EVIDENCE` for inconclusive evidence or cross-model disagreement.
- `GENERATE_ALTERNATIVE_HYPOTHESES` and `ANSWER_RESEARCH_QUESTION` are reserved
  authoritative decision literals for future routing, without creating a second
  workflow in this Step.

The existing `max_feedback_iterations` bounds the loop. Reaching it emits
`MORE_EVIDENCE` with `RESEARCH_ITERATION_LIMIT_REACHED`; it does not manufacture a
scientific conclusion or another hypothesis.

Existing code/runtime/environment/Plan-contract failures remain outside this layer.
In particular, `RESEARCH_PLAN_REVIEW_EXHAUSTED` from Step 5 cannot be treated as
scientific contradiction and cannot cause hypothesis evolution.

## Tests

- Focused Step 6 suite:

  `6 passed, 82 deselected in 0.49s`

- Complete backend regression:

  `519 passed, 2 skipped in 83.25s`

The tests cover immutable v1 + lineage v2, agreed supported path, contradicted path,
inconclusive/disagreement path, explicit DeepSeek unavailability, provenance-required
revision, and bounded-loop behavior.

## Explicit declarations

- Round 5 Step 6 completed: **YES**
- Round 6 entered: **NO**
- Fashion-MNIST new full training executed: **NO**
- Existing Step 1–5 validators weakened: **NO**
- Original user hypothesis overwritten: **NO**
- DeepSeek API key committed: **NO**
- Git commit created: **NO**

