---
name: paper-writing
description: Orchestrate an interactive Qwen paper-writing workflow after a research report is complete, with human outline confirmation, section-by-section drafting, claim and citation audit, and final Word plus LaTeX output.
---

# Interactive Paper Writing

Treat paper writing as an optional workflow after the research report. Never start it during report export.

## Required stages

1. Collect writing settings: venue, language, paper type, author line, and user notes.
2. Load the verified research report, experiment results, selected references, and the `paper-plan` Skill.
3. Produce a paper plan and stop for human confirmation.
4. After confirmation, load the `paper-write` Skill and draft one section at a time.
5. Persist every completed section and expose progress after each section.
6. Audit all numeric claims against experiment artifacts and all citations against verified references.
7. Stop for final human review. If feedback is provided, revise affected sections and repeat the audit.
8. Export a readable Word manuscript and a LaTeX source package.

## Interaction contract

- Use durable states: `planning`, `waiting_plan_confirmation`, `writing`, `auditing`, `waiting_final_confirmation`, `revising`, `completed`, `failed`, `interrupted`.
- Show the active Skill, current section, completed section count, total section count, progress percentage, and any blocking issue.
- Support stop, resume, plan revision, final revision, and retry.
- Never hide a long-running paper job behind a single HTTP request.

## Evidence rules

- Use only verified references already attached to the run.
- Do not download or reproduce copyrighted paper full text.
- Every central claim must map to a verified citation or an audited experiment result.
- Preserve negative findings and methodological corrections.
- Never strengthen a conclusion beyond the final experiment verdict.
- If a number cannot be found in raw experiment artifacts, remove it or mark it for human verification.

## Output contract

- Word is the readable manuscript.
- LaTeX is a ZIP containing `main.tex`, `sections/*.tex`, and `references.bib`.
- Do not generate PDF unless the user separately requests compilation.
