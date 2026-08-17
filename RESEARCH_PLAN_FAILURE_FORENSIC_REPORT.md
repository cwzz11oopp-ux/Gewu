# Research Plan Failure — Read-only Forensic Report

## Scope and preservation statement

This was a read-only examination of the persisted Run checkpoint, artifacts,
events, source code, active provider-status read endpoints, and local logs. No
Run was resumed or retried; no Artifact/checkpoint/code was changed; no new Run
or Git commit was created.

## 1. Identified Run

| Field | Persisted value |
|---|---|
| Run | `run_832279c32785` |
| Research question | “针对 IPIX 17 海杂波小目标检测任务，能否通过改进 TimesNet 的多尺度时序特征融合机制，在保持低虚警率的同时提高目标检测概率？” |
| Created | `2026-08-16 10:54:46 +08:00` |
| Updated | `2026-08-16 11:20:57 +08:00` |
| Status / current stage | `failed` / `research_plan` |
| Selected hypothesis | `CAND-003` (selected index `2`; persisted selection alias `hypothesis_3`) |
| Failed step | `research_plan` |
| Failure timestamp | `2026-08-16T11:20:56.290701+08:00` (step completion); failure artifact at `11:20:55.996502+08:00` |

## 2. Root exception and propagation

### Lowest persisted trigger

`ValueError: RESEARCH_PLAN_REVIEW_EXHAUSTED`

The source-level origin is `backend/app/workflow/engine.py`,
`WorkflowEngine._run_step`, line **952**. It occurs only after the third
DeepSeek plan review returns a valid structured `REVISE` response:

1. line 921 iterates `range(max_deepseek_plan_revision + 1)`;
2. the configured bound is 2 revisions, therefore 3 reviews are allowed;
3. line 941 takes the exhaustion branch when the verdict is `REJECT` **or**
   `review_round >= max_deepseek_plan_revision`;
4. line 948 persists `art_62efbdcdf11a` (`failure_record`), then line 952
   raises the exception.

The failure record is:

```json
{
  "code": "MODEL_OUTPUT_VALIDATION_FAILURE",
  "message": "DeepSeek did not produce an acceptable plan within the bounded revision limit.",
  "attempt": 3,
  "scientific_state_mutated": false
}
```

### Traceback evidence

The process did **not** persist a Python traceback, and no `QWEN_DIAGNOSTIC_LOG`
file exists for this Run. Therefore a verbatim runtime traceback cannot be
truthfully recovered. The complete source-grounded propagation path is:

```text
WorkflowOrchestrator._drive
  backend/app/workflow/orchestrator.py:150  engine.run_step(run_id, step_id)
WorkflowEngine.run_step
  backend/app/workflow/engine.py:216        self._run_step(run_id, step_id)
WorkflowEngine._run_step
  backend/app/workflow/engine.py:952        raise ValueError("RESEARCH_PLAN_REVIEW_EXHAUSTED")
ValueError: RESEARCH_PLAN_REVIEW_EXHAUSTED

WorkflowEngine.run_step catches Exception
  backend/app/workflow/engine.py:225-232
  -> marks step `research_plan` failed with code/message
  -> re-raises
WorkflowOrchestrator._drive catches Exception
  backend/app/workflow/orchestrator.py:167-180
  -> marks the Run `failed`, automatic=false
  -> appends “Automatic pipeline stopped after a step failure.”
```

Thus the frontend’s `风险 / 异常：research_plan` is an accurate projection of
the failed step, but it omits the material cause: **bounded scientific review
exhaustion**, not a low-level runtime crash.

## 3. LLM-call evidence

### Persisted plan-review calls

| Review | Time | Provider / model | Verdict | Feasibility | Issues |
|---|---:|---|---|---|---:|
| `art_d0fc311feedf` | 11:14:37.428 +08:00 | DeepSeek / `deepseek-v4-flash` | `REVISE` | `FEASIBLE_AFTER_REVISION` | 11 |
| `art_32f4d5f48784` | 11:17:46.695 +08:00 | DeepSeek / `deepseek-v4-flash` | `REVISE` | `FEASIBLE_AFTER_REVISION` | 6 |
| `art_32ede1af0681` | 11:20:55.690 +08:00 | DeepSeek / `deepseek-v4-flash` | `REVISE` | `FEASIBLE_AFTER_REVISION` | 10 |

The role configuration routes plan generation/revision to Qwen
`qwen3.7-max`, and plan review to DeepSeek `deepseek-v4-flash`. The persisted
provider configuration exposes Qwen timeout `300s` and DeepSeek timeout
`600s`. `planning.review_plan` uses the reasoning policy, so the DeepSeek
review route receives the reasoning timeout (600s in this configuration).

Exact per-request duration, HTTP status, provider retry count, `finish_reason`,
`max_tokens`, response body and raw output are **not persisted**. The only
available wall-clock intervals are mixed intervals containing both a Qwen plan
revision and the following DeepSeek review:

- step start → review 1: 377.70s (includes initial plan generation, a
  supervisor-requested plan revision, and review 1);
- review 1 → review 2: 189.27s (includes Qwen revision + review 2);
- review 2 → review 3: 189.00s (includes Qwen revision + review 3);
- total `research_plan` step: 756.56s.

No persisted event, failure record, server log, or diagnostic trace indicates a
timeout, HTTP 429/5xx, connection reset/close, context-length rejection,
truncation, JSON-decode error, structured-output failure, or schema-validation
exception. The backend stderr has one unrelated Windows client-socket reset at
server lifecycle level; it has no Run/task correlation and is not evidence for
this failure. Retry policy is not persisted in the active provider config; the
source default is one retry per model, but the actual attempt count cannot be
proven without the missing diagnostic trace.

## 4. Actual research-plan input size

The numbers below were recomputed from the actual checkpoint using the same
application serialization and prompt-context functions. Estimated tokens are
the application’s `ceil(chars / 4)` heuristic, not provider token accounting.

### Plan generation (`planning.build_plan`)

| Component | Records | Chars | Est. tokens |
|---|---:|---:|---:|
| Selected hypothesis-selection artifact | 1 | 4,167 | 1,042 |
| Bound dataset option/card | 1 | 1,886 | 472 |
| Plan context: contract, full dataset profile, split metadata | 1 | 4,662 | 1,166 |
| Three routed skill instructions | 3 | 10,640 | 2,660 |
| Plan JSON schema hint | 34 top-level fields | 2,493 | 624 |
| Full serialized request | — | **24,108** | **6,027** |

No literature synthesis, themes, gaps, evidence registry, scientific feedback,
previous artifacts, GitHub data, or Code Evidence is supplied directly to
`planning.build_plan`; only the evidence-reasoned selection is.

### Plan review (`planning.review_plan`): known persisted components

| Component | Records | Chars | Est. tokens |
|---|---:|---:|---:|
| Compact research problem | 1 | 977 | 245 |
| Selected `CAND-003` | 1 | 455 | 114 |
| Selected-hypothesis rationale | 1 | 75 | 19 |
| Literature cards after `select_units(..., 7000)` | **5 of 24** core refs | 6,947 | 1,737 |
| `candidate_assessments[:5]` | **4** | **258,201** | **64,551** |
| Authoritative plan contract | 1 | 1,019 | 255 |
| Dataset profile / option / resource constraints | 4 objects | 4,477 | 1,119 |
| Three routed skill instructions plus review directive | 3 | 10,280 | 2,570 |
| Review schema hint | 5 fields | 396 | 99 |
| Full known request excluding the non-persisted current plan | — | **283,415** | **70,854** |

The exact final size for each review cannot be recomputed because each
`current_research_plan` (initial plan and two revised plans) was never
persisted. Hence **283,415 characters / 70,854 estimated tokens is a lower
bound** for every review request.

### The 283 evidence records

The UI’s 283 evidence count is real: `reasoning.evidence_registry` has 283
records. They are **not** passed as a direct `evidence_literature_compact_summary`
list; that field contains five literature cards. However, all four candidate
assessments are injected unboundedly by
`engine.py:4056-4058`. Together they are 258,201 chars and contain:

- 304 `EVID-…` occurrences (66 unique IDs);
- 101 `E…` registry IDs (220–235 per individual assessment occurrence);
- repeated evidence/rationale structures across four assessments.

This contradicts the declared `context_policy` value
`compact_summaries_only_no_unbounded_artifact_injection`. It is a material
context-bounding defect and likely source of needless cost/latency, but there
is **no persisted provider error** showing that it caused this Run’s rejection.

## 5. Raw output, parser and schema findings

- Raw Qwen build/revision responses: **not persisted**.
- Raw DeepSeek review responses: **not persisted**; their normalized structured
  results are persisted as the three `plan_review` artifacts.
- The three stored review outputs are non-empty, structured, have valid
  `REVISE` verdicts, valid `FEASIBLE_AFTER_REVISION` values, non-empty issues,
  and non-empty suggested fixes. There is no stored markdown fence, malformed
  JSON, missing required review field, or type error.
- `finish_reason`, truncation signal, HTTP status and raw response length are
  unavailable because the optional diagnostic trace was not enabled/persisted.

`_PLAN_SCHEMA` is a JSON prompt hint, not a Pydantic model. It requests the
following top-level plan fields: `objective`, `hypotheses`, `method`,
`dataset`, `comparisons`, `evaluations`, `procedure`, `parameters`, `seeds`,
`statistical_summary`, `success_criteria`, `failure_criteria`,
`expected_artifacts`, `stop_conditions`, `primary_experiment`,
`optional_ablations`, `traceability`, `resources`, `risks`,
`additional_sections`, `diagnosis`, `revised_hypothesis`,
`mechanism_and_evidence`, `boundary_conditions`, `alignment_contract`,
`baseline_and_controls`, `feasibility_risks`, `staged_gates`,
`formal_experiment_entry_conditions`,
`positive_negative_inconclusive_rules`, `remaining_unknowns`,
`capacity_confounder`, and `local_dataset_loader_verification`.

At transport normalization, only `objective` and `procedure` are required
root keys; `normalize_plan` fills many absent fields with empty collections.
The plan-review schema requires `verdict`, `issues`, and
`experiment_feasibility`; additionally, a `REVISE` must contain `issues` and
`suggested_fixes`. All three review outputs met those checks. This was **not a
Pydantic/schema validation failure**.

## 6. Review/repair behavior

The run did not fail after one LLM/schema failure. It performed:

1. Qwen initial plan generation;
2. supervisor validation rejection at 11:11:06, requesting one plan revision;
3. DeepSeek review 1 → `REVISE`;
4. Qwen `planning.revise_from_review`;
5. DeepSeek review 2 → `REVISE`;
6. Qwen `planning.revise_from_review`;
7. DeepSeek review 3 → `REVISE`;
8. bounded review exhaustion → failed step/Run.

The direct review reasons were scientific and executable-design defects, not
provider errors. Across the three rounds they include: unverified IPIX label
semantics; an unsupported “standard split” claim; ambiguous/unsafe split and
overlapping-window leakage; inadequate Pfa=1e-4 tail support and test-set
threshold calibration; incomplete outcome/effect-size/power rules; unspecified
TimesNet configuration/training policy; non-executable CA-CFAR control; and
ambiguous resource/latency protocol.

## 7. Checkpoint and Artifact state

Persisted before failure:

- `CAND-003` is present in `art_f8e59e8aa059`;
- evidence review/assessment and automatic selection are persisted;
- 4 candidate reasoning checkpoints, 1 idea-review checkpoint, 1 targeted
  retrieval artifact, 1 final reasoning artifact, and 1 selection artifact
  remain available;
- all three `plan_review` artifacts remain available;
- `art_62efbdcdf11a` preserves the exhaustion failure.

Not persisted:

- initial plan candidate;
- two revised plan candidates;
- raw LLM responses / raw HTTP metadata;
- a dedicated plan-candidate attempt/validation artifact;
- explicit retry lineage between the three plan candidates.

The orchestrator labels this checkpoint `recoverable: true`, and it has all
upstream inputs needed to technically re-enter `research_plan`. However, the
current generic `rerun_from(research_plan)` removal logic removes non-locked
artifacts sourced from `research_plan`, including the three reviews and failure
record, before regenerating. Therefore: **technically resumable, but unsafe for
forensic-preserving resume unless those records are first retained externally.**
No resume was performed.

## 8. GitHub-source involvement

Despite the stated URL `https://github.com/thuml/Time-Series-Library`, this
Run’s persisted `github_repository_url` is empty. It has no `github_source` or
`code_evidence` artifact, no source status, and no Code Evidence count. GitHub
inspection did not enter plan generation or review and did not propagate an
error into `research_plan`.

## 9. Timeline (last 20+ key persisted transitions)

| Time (+08:00) | Stage | Event / artifact | Status |
|---|---|---|---|
| 10:54:47.370 | problem | step started | running |
| 10:54:47.706 | dataset inspection | `dataset_profile` persisted | verified |
| 10:54:48.165 | dataset inspection | locked local dataset event | success |
| 10:55:01.421 | problem | `problem` persisted | success |
| 10:55:01.942 | problem | step completed | success |
| 10:55:02.276 | knowledge | step started | running |
| 10:55:09.396 | knowledge | `evidence` persisted | 85 verified refs |
| 10:55:09.633 | knowledge | `research_synthesis` persisted | 85 papers / 4 themes / 8 gaps |
| 10:55:10.253 | knowledge | step completed | success |
| 10:55:10.605 | hypothesis | step started | running |
| 10:58:29.495 | hypothesis | `hypothesis` persisted | 4 candidates |
| 10:58:30.045 | hypothesis | step completed | success |
| 10:58:30.403 | evidence reasoning | step started | running |
| 10:59:49.555 | evidence reasoning | idea-review checkpoint | persisted |
| 11:00:43.210 | evidence reasoning | candidate checkpoint 1 | persisted |
| 11:01:46.709 | evidence reasoning | candidate checkpoint 2 | persisted |
| 11:02:42.122 | evidence reasoning | candidate checkpoint 3 | persisted |
| 11:03:38.519 | evidence reasoning | candidate checkpoint 4 | persisted |
| 11:04:00.456 | evidence reasoning | targeted retrieval | persisted |
| 11:08:17.544 | evidence reasoning | final idea review | persisted |
| 11:08:17.826 | evidence reasoning | final reasoning | persisted |
| 11:08:18.920 | evidence reasoning | automatic selection of `CAND-003` | success |
| 11:08:19.731 | research plan | step started | running |
| 11:11:06.102 | research plan | supervisor requested plan-generation revision | retry/repair |
| 11:14:37.429 | research plan | DeepSeek plan review 1 | `REVISE` |
| 11:17:46.695 | research plan | DeepSeek plan review 2 | `REVISE` |
| 11:20:55.690 | research plan | DeepSeek plan review 3 | `REVISE` |
| 11:20:55.997 | research plan | failure record persisted | exhausted |
| 11:20:56.291 | research plan | step marked failed | `RESEARCH_PLAN_REVIEW_EXHAUSTED` |
| 11:20:56.946 | orchestrator | automatic pipeline stopped event | Run failed, recoverable=true |

The final successful event is the persisted third valid DeepSeek review. The
first failure state is the deterministic `ValueError` raised immediately after
that review at `engine.py:952`.

## Final finding

**ROOT CAUSE:** Three valid DeepSeek scientific plan reviews concluded that the
plan remained materially non-executable/scientifically under-specified after
the bounded repair loop. The third `REVISE` exhausted the configured two
revision limit.

**DIRECT TRIGGER:** `WorkflowEngine._run_step` raised
`ValueError("RESEARCH_PLAN_REVIEW_EXHAUSTED")` at
`backend/app/workflow/engine.py:952`.

**WHY RUN BECAME FAILED:** `WorkflowEngine.run_step` converts that exception
to a failed `research_plan` step, re-raises it, and `WorkflowOrchestrator._drive`
converts any unhandled step exception into Run status `failed`.

**CLASSIFICATION:** `7. scientific validation failure` (with a separate
context-bounding stability risk). It is not evidenced as transient
infrastructure failure, LLM-generation failure, structured-output/schema
failure, context overflow, deterministic program bug, state/checkpoint bug, or
GitHub/source enrichment failure.

**RECOVERABILITY:** `safe resume` is not the accurate forensic label. The
checkpoint is technically resumable at `research_plan`, but current rerun
cleanup would delete the non-locked plan-review/failure artifacts. Classify it
as **unsafe resume for preservation; technically resumable after preserving
forensic records**.

**STABILITY DEFECT:** **yes**, but not the immediate error classification:

1. review context injects 258k chars of candidate assessments despite claiming
   compact bounded summaries;
2. plan candidates and raw model responses are not persisted before review;
3. generic rerun cleanup can erase the very plan-review evidence needed to
   diagnose/recover a rejected plan.

## Non-implemented recommendations

1. Bound/batch `candidate_assessments` in plan review and pass only a
   selection-specific evidence digest; retain all evidence in artifacts, not in
   every review prompt.
2. Persist each plan candidate, normalized validation result, model metadata,
   request-size metric, review parent ID, and raw-response forensic metadata
   (with secrets redacted) before any review/revision.
3. Treat `REVISE` exhaustion as a distinct recoverable
   `research_plan_revision_required` state rather than the same UI/Run failure
   bucket used for infrastructure exceptions.
4. Add a checkpoint-safe plan-review continuation that preserves the failed
   lineage and appends a new review/revision attempt; do not use generic
   destructive rerun cleanup for this state.
5. Keep bounded retries for transient provider failures separate from
   scientific revision rounds. Apply structured-output repair only to malformed
   provider output, not to scientific review findings.
6. Before a future plan attempt, produce explicit loader/README semantic
   verification, split identity, Pfa calibration/evaluation protocol, CFAR
   decision rule, effect-size/power rationale, and fixed model/measurement
   protocol. Do not weaken the existing scientific contract to pass review.
