# Clean Snapshot Manifest

| Source | Included | Destination / classification | Reason |
| --- | --- | --- | --- |
| `backend/` | yes, excluding `backend/data/` and caches | `backend/` / CORE | application source and dependency declaration |
| `frontend/` | yes, excluding `node_modules/` and `dist/` | `frontend/` / CORE | React/Vite source and build definition |
| `skills/` | yes, excluding nested VCS metadata | `skills/` / CORE | runtime Skill Loader inputs |
| `tests/` | yes | `tests/` / CORE | regression validation |
| `scripts/`, `tools/`, `requirements/`, `.github/`, `docs/` | yes | same paths / CORE | operations, dependencies and documentation |
| root templates and metadata | yes | root / REQUIRED_RUNTIME_CONFIG | safe configuration templates and project metadata |
| `datasets/README.md` | yes | `datasets/README.md` / REQUIRED_RUNTIME_CONFIG | local dataset setup guidance |
| `.venv/`, `.pnpm-store/` | no | EXCLUDE | recreatable installed environment |
| `.git/`, `.worktrees/` | no | EXCLUDE | local repository object/history and duplicate worktrees |
| `datasets/` content | no | EXCLUDE | external FashionMNIST/IPIX data |
| `backend/data/`, `experiments/`, `outputs/`, `artifacts/` | no | EXCLUDE | mutable runtime history and generated outputs |
| `tmp/`, caches, `__pycache__/`, `.pytest_cache/` | no | EXCLUDE | temporary/generated files |
| Round reports/issues/diagnostics | no | `D:\竞赛_validation_history` / VALIDATION_HISTORY | source and validation history are separated |

## Rebuild notes

1. Create a Python environment and install `requirements.txt` plus the files in
   `requirements/` as documented by the project.
2. Install frontend dependencies with pnpm in `frontend/`.
3. Copy `.env.example` to a local `.env` and configure provider and dataset
   paths; do not commit that file.
4. Place datasets outside this snapshot or configure their local path through
   runtime settings.  No dataset is bundled here.
5. Start the backend with `python -m uvicorn backend.app.main:app --host
   127.0.0.1 --port 8000`, then verify `GET /api/system/runtime-info`.
