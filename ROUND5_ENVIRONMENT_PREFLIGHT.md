# Round 5 Environment Preflight

Date: 2026-08-14

## Source / interpreter separation

| Check | Actual value | Result |
| --- | --- | --- |
| Source root | `D:\竞赛_clean_round4_ready` | PASS |
| Python executable | `D:\竞赛\.venv\Scripts\python.exe` | PASS |
| Python prefix | `D:\竞赛\.venv` | PASS |
| Python version | 3.12.13 | PASS |
| Backend module source | `D:\竞赛_clean_round4_ready\backend` | PASS |
| Backend main source | `D:\竞赛_clean_round4_ready\backend\app\main.py` | PASS |
| Workflow module source | `D:\竞赛_clean_round4_ready\backend\app\workflow\engine.py` | PASS |
| Skill module source | `D:\竞赛_clean_round4_ready\backend\app\workflow\skills.py` | PASS |
| Old source imported | No | PASS |
| Editable-install pollution | No `-e` old-project entry found; no `.pth` reference to `D:\竞赛` | PASS |

The preflight set `DATA_DIR` only for the checking process to
`D:\竞赛_clean_round4_ready_round5_runtime\data`.  The source tree was not used as
runtime storage.

## Dependency / local runtime checks

| Check | Result | Notes |
| --- | --- | --- |
| `numpy` import | PASS | available in the reused environment |
| `torch` import | PASS | 2.13.0+cu132; CUDA available |
| `matplotlib` import | FAIL | not installed; not listed in the project's declared requirement files |
| `pandas` import | FAIL | not installed; not listed in the project's declared requirement files |
| Local interpreter smoke test | PASS | printed `ROUND5_RUNTIME_OK` in the separate runtime directory |

No dependency was installed.  `matplotlib`/`pandas` are recorded for the generated
experiment's later dependency decision; their absence does not justify changing the
frozen source or pre-installing Ising-specific packages.

## Real-provider readiness

| Check | Result |
| --- | --- |
| Qwen config available | **NO** — `QWEN_API_KEY_MISSING` |
| Literature provider | PASS — `arxiv_semantic_scholar` configured |
| Selected experiment provider | **FAIL** — `remote_gpu` requires `REMOTE_GPU_HOST`, `REMOTE_GPU_USER`, and `REMOTE_GPU_PROJECT_DIR` |
| Backend started | Not started by design |
| Skill Runtime smoke test | Importable; formal model invocation blocked by missing Qwen configuration |
| Round 5 ready | **NO** |

## Decision

The source/virtual-environment separation is correct, but formal Round 5 must not
start yet.  Starting the backend without secure Qwen configuration and a ready
experiment provider would either fail the real execution chain or use a fallback,
which is invalid for this acceptance run.

To continue, provide secure session environment values for `QWEN_API_KEY` and either
configure the existing remote provider or explicitly choose/configure a local real
experiment provider.  Do not write the key into `.env`, source files, reports, or
Git.  Then rerun this preflight before launching Uvicorn from the clean source root.

