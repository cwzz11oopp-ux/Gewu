# Runtime Skill Map

`backend/app/workflow/skills.py` is the source of truth for runtime routing.
The project currently routes 24 unique Skills: 21 in the research workflow
including conditional and failure paths, plus 3 in optional paper writing.

## Research workflow

| Step | Agent | Skills |
| --- | --- | --- |
| `problem_understanding` | Research Agent | `problem-framing` |
| `knowledge_integration` | Research Agent | `research-lit`, `research-wiki` |
| `hypothesis_generation` | Idea Agent | `idea-creator` |
| `evidence_reasoning` | Critic Agent | `idea-selection`, `novelty-check`, `research-review` |
| `research_plan` | Planning Agent | `research-refine`, `hypothesis-experiment-gate`, `experiment-plan` |
| `experiment_task` | Experiment Agent | `experiment-implementation` |
| `experiment_run_analysis` | Experiment Agent | `run-experiment`, `analyze-results`, `experiment-audit` |
| `experiment_run_analysis` when monitoring is enabled | Experiment Agent | `monitor-experiment` |
| `experiment_diagnosis` failure route | Experiment Diagnostic Agent | `experiment-diagnosis` |
| `feedback_revision` | Critic Agent | `experiment-iteration`, `result-to-claim` |
| `feedback_revision` when follow-up is allowed | Critic/Planning Agents | `research-refine`, `experiment-plan` |
| `feedback_revision` for a partial or failed verdict | Planning Agent | `ablation-planner` |
| `report_export` | Writer Agent | `competition-report`, `report-quality-audit` |

The atomic experiment path is:

```text
experiment-plan
  -> experiment-implementation
  -> run-experiment
  -> analyze-results
  -> experiment-audit
  -> result-to-claim
```

An experiment preparation, execution, analysis, or audit failure may enter
`experiment-diagnosis`. A partial or failed audited result may enter the
bounded follow-up route.

## Optional paper writing

Paper writing starts only after the user explicitly selects it.

| Paper stage | Skills |
| --- | --- |
| Plan or revise the outline | `paper-writing`, `paper-plan` |
| Draft or revise sections | `paper-writing`, `paper-write` |
| Audit numbers, citations, and claim boundaries | `paper-writing` |
| Export Word and LaTeX | deterministic export from the audited state |

## Catalog boundary

Direct `skills/<skill-id>/SKILL.md` directories are catalog entries, but catalog
presence does not authorize automatic routing. Vendored cross-model variants
and shared references are excluded:

- `shared-references`
- `skills-codex`
- `skills-codex-claude-review`
- `skills-codex-gemini-review`
