# AI Scientist V2 Final Demo Hardening runbook

These demos remain separate so the deterministic research proof is always available when a live provider or public network is not.

## Demo A: deterministic threshold regression

Purpose: show that one locked protocol produces an audited five-point parameter response and a three-state Claim-Evidence Graph.

```powershell
.\scripts\start_v2_demo.ps1 -Demo A
```

Open `http://127.0.0.1:5173/`. Demo A is the initial persisted snapshot and does not require network or live Qwen. It shows:

- thresholds `0.1 / 0.2 / 0.3 / 0.4 / 0.5` under one protocol fingerprint;
- accuracy `1.0 / 1.0 / 0.8 / 0.8 / 0.8` against baseline `0.8`;
- the stable improvement interval `[0.1, 0.2]`;
- `SUPPORTED`, `PARTIALLY_SUPPORTED`, and `NOT_SUPPORTED` claims;
- a scientific trajectory without internal pipeline-step labels.

Acceptance check: both targeted test files pass, every parameter point is audited and protocol-compatible, and the UI labels threshold `0.2` as not uniquely supported.

## Demo B: live public-repository research

Purpose: show the completed public micrograd loop: live Qwen ideation and planning, audited first experiment, strict full-revert ablation, bounded finite robustness, live Critic, belief/frontier updates, three-state Claim-Evidence Graph, and final Writer export.

```powershell
.\scripts\start_v2_demo.ps1 -Demo B
```

This displays the persisted validated result. A fresh live rerun is explicitly opt-in and requires public network plus a ready Qwen RuntimeConfigStore:

```powershell
.\scripts\start_v2_demo.ps1 -Demo B -RunLive
```

Repository: `https://github.com/karpathy/micrograd.git`.

The external locked evaluator is `scripts/evaluate_micrograd_relu.py`. It is not copied into or editable from an experiment worktree. The core grid and robustness grid are separate explicit protocols. The latter includes only `±0.0`, `±1e-15`, `±1e-12`, and `±1.0`; it explicitly excludes NaN, infinity, subnormal, and general floating-point claims.

Validated public result:

- session: `research_dc8671de582b`;
- upstream: `7bc720e951fe422b8f8814aa5aa1b64121d26b4c`;
- live implementation: `195bbb8904619518da234566f4a9ea4d67d72ee1`;
- main experiment: `0.875 → 1.0`;
- full-revert ablation: `1.0 → 0.875` under the original protocol;
- finite boundary robustness: `0.875 → 1.0` under its separately reproduced locked protocol;
- final branch status: `validated`; belief support `0.95`, uncertainty `0.25`;
- live final Critic model: `qwen3.7-max`, no fallback;
- report: `backend/data/final-demo-hardening/micrograd/micrograd-research-report.docx`.

The first live ablation planner proposed `max(0.0, x)`, which retained the target canonicalization capability. It was rejected as an invalid mechanism-removal control, archived in `backend/data/final-demo-hardening/micrograd/invalid-live-planner-ablation.json`, and was not used as supporting scientific evidence. The accepted ablation uses the audited upstream commit as a strict full-revert control.

Acceptance check: `/` Demo B shows Question → Branch → First Experiment → Critic → RUN_ABLATION → Second Experiment → Evidence Update → Final Conclusion, plus all three ExperimentRecords and the tri-state Claim-Evidence view.

## Full checkpoint verification

```powershell
.venv\Scripts\python.exe -m pytest tests/backend -q
node --test frontend/tests/ui-contract.test.mjs
pnpm --dir frontend run build
```

Do not place credentials in commands, output files, screenshots, or demo recordings.

## Feature freeze and live dependencies

Final Demo Hardening is feature-frozen. Do not add Agents, Graph runtimes, Providers, databases, official LangGraph migration, PostgreSQL, Docker, DeepSeek/GPT, or embedding work before the demo.

Demo A is the offline fallback. Demo B's persisted snapshot is also viewable offline, but a fresh clone/model run requires GitHub network access and a Qwen key configured through RuntimeConfigStore. No credential belongs in repository files or demo artifacts.
