# Agent Architecture

## Runtime Boundary

This project is a Qwen-powered research workflow for code-centered neural-network experiments. It is distributed through GitHub and runs on the machine that starts the backend. Real experiments use either that machine's local GPU or a generic SSH GPU target.

Application Agent classes live under `backend/app/agents`. The top-level `.agents` directory is not part of application execution; it is reserved only for possible future external Codex configuration.

## Supervisor and SkillRuntime

`SupervisorAgent` owns planning, static delegation, deterministic output checks, revision budgets, semantic-review dispatch, and accepted Wiki commits. It never performs literature search, report writing, local process execution, or SSH execution itself.

`SkillRegistry` is the single source of truth for workflow assignments. `SkillLoader` reads `skills/<skill-id>/SKILL.md`, validates safe paths, parses frontmatter, and hashes the complete instruction body. `SkillRuntime` loads the assigned Skills, applies instruction budgeting at Markdown section boundaries, and computes `authorized_tools` using this intersection. Workflow provider calls must pass the resulting authorization gate before execution:

```text
Skill declared application tools
intersection Agent policy
intersection registered backend tools
intersection configured tools
```

Legacy names such as `Bash(*)`, `WebSearch`, and `Agent` are not aliases for backend permissions. A Skill receives a capability only by declaring its exact registered application tool name. Provider calls remain controlled by application code; Skill text never grants operating-system permissions.

Each workflow event records one `supervisor_agent.delegate` call and one `skill_runtime.prepare` call with Agent ID, Skill IDs, instruction hash, authorized tools, denied tools, and omitted sections.

### Atomic Skill Execution

The aggregate `RuntimePackage` authorizes the whole workflow step, but a domain operation receives only the atomic Skill protocol it is executing. `SkillRuntime.instructions_for()` rejects Skill IDs that were not routed for the parent step and renders the selected Skill from its complete `SKILL.md` body. This prevents unrelated capability prompts from being treated as one large advisory prompt.

The experiment and feedback path is executed in this order:

```text
hypothesis-experiment-gate
  -> experiment-plan
  -> experiment-implementation
  -> run-experiment
  -> analyze-results
  -> experiment-audit
  -> (execution/analysis/audit failure)
       experiment-diagnosis -> bounded repair -> retry (maximum 2)
  -> experiment-iteration
  -> result-to-claim
  -> (partial/failed and follow-up allowed)
       research-refine + experiment-plan + ablation-planner
```

`analyze-results` receives only authoritative runtime metrics and cannot replace them. `experiment-audit` receives the manifest, complete generated source with hashes, environment, attempts, and result so it can detect result-to-code inconsistencies in addition to deterministic ID, metric, and GPU checks. Its `is_real_experiment` decision is authoritative for downstream claim and export eligibility.

`ExperimentDiagnosticAgent` is separate from Reviewer judgment. It classifies concrete runtime failures, writes an `experiment_diagnosis` Artifact, and may invoke only deterministic, registered repairs. Current repairs can quarantine a known incomplete dataset archive and retry, retry a transient analysis operation without rerunning completed training, or regenerate only the current experiment bundle after a generated-code exception. It never installs packages, edits credentials, changes CUDA configuration, changes the accepted plan, or runs arbitrary commands. Unknown failures are advisory even if the diagnosing model proposes a mutation.

`result-to-claim` receives the hypothesis, accepted plan, analysis, and audit and returns structured supported and unsupported claims, revisions, next action, and evidence links. An honest `partial` or `failed` result is a valid completed feedback artifact. The Reviewer must not require the proposed future ablation to exist before accepting the feedback that schedules it.

## Static Assignment

| Step | Agent | Primary Skill | Fixed or Conditional Capability |
| --- | --- | --- | --- |
| `problem_understanding` | ResearchAgent | `problem-framing` | none |
| `knowledge_integration` | ResearchAgent | `research-lit` | `research-wiki` |
| `hypothesis_generation` | IdeaAgent | `idea-creator` | Wiki query pack read |
| `evidence_reasoning` | CriticAgent | `idea-selection`, `novelty-check` | `research-review` |
| `research_plan` | PlanningAgent | `research-refine` | `hypothesis-experiment-gate`, `experiment-plan` |
| `experiment_task` | ExperimentAgent | `experiment-implementation` | bundle builder |
| `experiment_run_analysis` | ExperimentAgent | `run-experiment` | `analyze-results`, `experiment-audit`; `monitor-experiment` when enabled |
| `experiment_diagnosis` (failure route) | ExperimentDiagnosticAgent | `experiment-diagnosis` | bounded cache repair, retry, or current-bundle regeneration |
| `feedback_revision` | CriticAgent | `experiment-iteration`, `result-to-claim` | `research-refine`, `experiment-plan`, and `ablation-planner` for an allowed partial/failed follow-up |
| `report_export` | WriterAgent | `competition-report`, `report-quality-audit` | citation and result audit |

Composite orchestration Skills such as `idea-discovery`, `research-pipeline`, `experiment-bridge`, `paper-writing`, and `auto-review-loop*` are excluded from ordinary step routing.

## Qwen and Reviewer Separation

Domain Agents receive the configured `LLMProvider`; competition mode uses `QwenLLMProvider`. The compatibility name `HypothesisAgent` points to `IdeaAgent`.

`ReviewerAgent` uses a fresh Qwen request for semantic review. It receives only a staged candidate Artifact, explicitly allowed Wiki files, and a versioned rubric. It does not receive a Supervisor summary or write-capable tools. Semantic review applies to evidence reasoning, research planning, feedback revision, and report export after deterministic checks pass.

`MockLLMProvider` remains a development fallback. Events record `fallback_used=true` and a reason whenever it is used. Competition mode must fail explicitly when Qwen configuration is missing.

## Skill Catalog Boundary

The catalog scans only direct, 一级 `skills/<skill-id>/SKILL.md` directories. It excludes imported cross-model variants and shared material:

- `shared-references`
- `skills-codex`
- `skills-codex-claude-review`
- `skills-codex-gemini-review`

Catalog discovery does not alter static routing. Non-routed vendored Skills cannot be selected by `SkillRegistry`.

## Run State and Guards

`RunRecord` contains versioned Artifacts and Event records. Users can inspect traces, edit and lock Artifacts, rerun from a step, and compare revisions.

Accepted outputs must satisfy these guards:

- uploaded-only literature is not an exportable verified citation;
- empty or degraded Wiki state cannot block external literature retrieval;
- experiment results come from the manifest-declared result file, not stdout;
- a GPU-required run passes CUDA preflight and records the actual device;
- `experiment_1_result` is linked to `experiment_1` within its Run;
- report claims use only verified citations and audited real experiment results.
