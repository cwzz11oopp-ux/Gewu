# AI Scientist V2 Beta Sprint Status

> Snapshot: 2026-08-11. This file reports only code and tests completed in the current workspace. It does not describe planned work as implemented.

## Outcome

The V2 scientific core now exists as an independent backend vertical slice beside V1. It does not extend `WorkflowEngine`, does not replace the V1 API route, and does not modify the frontend.

The central acceptance invariant is covered by both deterministic and real-repository integration tests:

```text
ResearchState
  -> ResearchController selects H1 + RUN_EXPERIMENT
  -> audited protocol-compatible repository experiment
  -> EvidenceUnit + Belief update
  -> H1 becomes promising and Frontier is reranked
  -> ResearchController selects H1 + RUN_REPLICATION
  -> second audited experiment
  -> second EvidenceUnit + Belief update
  -> H1 becomes validated
  -> next Controller decision moves to H2
```

The second action is selected from the updated branch status and `next_actions`; it is not an iteration-number Pipeline rule.

## A. Architecture

### Implemented V2 core

- provider-neutral Pydantic domain: ProblemProfile, BaselineProfile, ExperimentProtocol, ProtocolFingerprint, ProtocolCompatibilityResult, ResearchAction/Operator, ResearchBranch/Frontier, BeliefState, BudgetState, EvidenceUnit, ExperimentRecord and ResearchState;
- explicit priority components with known/unknown, estimation method and provenance;
- legal branch transition state machine;
- best-first FrontierPolicy plus BudgetPolicy and StopPolicy;
- ResearchController with `next_action(state) -> ResearchAction`;
- experiment outcome -> evidence/belief/branch/frontier/budget state update;
- service-level ResearchLoop and Beta ResearchGraph with condition, cycle, compact checkpoint and explicit resume/recovery;
- logically separated JSON ResearchStateStore, FrontierStore, ExperimentRegistry and EvidenceStore;
- Claim-Evidence Graph with verified-claim export gate.

### Reused V1 infrastructure

- ExperimentBundle/Manifest and authoritative result identity;
- ExperimentAgent deterministic audit and existing Local/SSH provider boundary through `ExperimentExecutionAdapter`;
- existing arXiv-compatible LiteratureProvider and local LiteratureLibrary;
- existing Qwen-compatible provider through provider-neutral `LegacyQwenAdapter`.

### Still legacy

- FastAPI application startup and public research route still use V1 WorkflowEngine/WorkflowOrchestrator;
- V1 frontend still centers PipelineTimeline;
- current Writer/PaperWritingManager has not yet received the Claim-Evidence adapter;
- V1 Artifact remains single-parent.

## B. Executed demo

`test_v2_repository_research_e2e.py` creates a temporary tracked ML repository and executes real Python/Git operations:

```text
Question: can a threshold correction improve the fixture model?
Local baseline at clean base commit: accuracy 0.8
Frontier: H1 and H2
Decision 1: H1 + RUN_EXPERIMENT
Worktree 1: edit existing model.py, static check, smoke test, formal run
Result 1: accuracy 1.0, protocol compatible, audit passed, variant committed
State: H1 promising
Decision 2: H1 + RUN_REPLICATION
Worktree 2: starts from H1 code commit, no implementation mutation
Result 2: accuracy 1.0, protocol compatible, audit passed
State: H1 validated, belief support increased, uncertainty reduced
Next decision: H2 + RUN_EXPERIMENT
```

The fixture is only a platform verification repository. Its results are not scientific evidence about an external research problem.

## C. Research Frontier behavior

- proposed/queued branches enter first experiment selection;
- compatible audited support moves a branch to promising;
- a promising branch schedules replication before validation;
- a second supporting result moves it to validated;
- incompatible or unaudited results remain observations and move the branch to inconclusive;
- repeated contradictory evidence can reject a branch;
- terminal branches are excluded from best-first selection.

Every priority component retains its value or explicit unknown state, estimation method and provenance. Model qualitative heuristics are labeled as heuristics, not calibrated probabilities.

## D. Provenance and scientific gate

The real-repository integration verifies this chain:

```text
ResearchBranch
  -> ExperimentRecord
  -> base Git commit
  -> code Git commit
  -> ExperimentProtocol + deterministic SHA-256 fingerprint
  -> config
  -> authoritative result JSON metrics
  -> EvidenceUnit
  -> Belief and Branch update
```

Direct improvement claims require both exact ProtocolCompatibilityGate success and experiment audit success. A changed split or failed/mock audit produces `COMPARISON_NOT_ALLOWED`; the observation is retained but cannot become a verified improvement claim.

## E. Test evidence

Commands run on 2026-08-11:

```text
python -m pytest tests/backend -q
  441 passed, 3 skipped

node --test frontend/tests/ui-contract.test.mjs
  32 passed

pnpm --dir frontend run build
  succeeded
```

The 28 V2 tests cover domain invariants, protocol fingerprints and comparison gates, budget, branch transitions, best-first selection, two scientific iterations, V1 experiment adapter, real repository worktrees, baseline reproduction, literature access levels, ModelGateway/Ideator validation, checkpoint/resume and Claim-Evidence provenance.

Known non-blocking environment warning: Python emits a `RequestsDependencyWarning` from the host Anaconda package path. It did not fail tests, but environment isolation should be investigated before production packaging.

## F. Remaining work

### Production hardening

- replace the Beta graph wrapper with official LangGraph + SQLite checkpointing after pinning and validating the dependency;
- transactional consistency across the four JSON stores;
- durable recovery of in-flight Local/SSH experiments through the graph recovery callback;
- sandbox/resource policies beyond argv/cwd/executable/path checks;
- concurrent scheduler and cross-process locking;
- Artifact multi-parent data migration.

### Feature completeness

- V2 FastAPI endpoints and project/session lifecycle;
- live Qwen branch-generation integration test with configured credentials;
- general repository implementation planner rather than pre-approved file contents;
- richer baseline environment reconstruction and reported-result extraction;
- PaperCard full-text section parsing and evidence extraction;
- Critic action suggestions and failure-driven branch expansion;
- Claim-Evidence adapter into the existing Writer;
- V2 Beta frontend view.

### Optional enhancements

- additional literature sources, hybrid retrieval and embeddings;
- DeepSeek coding escalation and GPT independent review;
- Docker/RemoteDocker/Apptainer providers;
- PostgreSQL, distributed scheduling and advanced search policies.
