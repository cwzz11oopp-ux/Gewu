# Final Cleanup Dependency Audit

Date: 2026-08-14  
Source assessed: `D:\竞赛_clean_round4_ready`  
Planned official root: `D:\Gewu`

## Scope and method

The audit searched source, startup scripts, environment templates, dependency
manifests, tests, frontend configuration, runtime configuration, and generated
documentation for old project roots, `PYTHONPATH`, editable-install markers,
runtime data paths, dataset paths, and artifact paths. The legacy virtual
environment was inspected read-only.

## Answers required for consolidation

1. **Does source still refer to `D:\竞赛`?**  No production backend,
   frontend, startup script, environment template, or test requires it. Two
   standalone report-generation tools had hard-coded legacy paths and were
   converted to resolve their root relative to their own file. Remaining
   occurrences are historical reports/plans and are not executable runtime
   configuration.

2. **Which references are true runtime dependencies?**  Relative defaults such
   as `backend/data`, `datasets`, and `experiments` are real runtime locations;
   they resolve from the selected project root or from explicit configuration.
   `LOCAL_GPU_PYTHON` is a real configurable dependency and must name
   `D:\Gewu\.venv\Scripts\python.exe` after migration.

3. **Which references are historical documentation?**  Round 4/5 reports and
   archived engineering plans record their then-current paths and prior test
   commands. They do not execute and may retain historical paths.

4. **Which configuration only exists under the old project?**  The former
   project's `backend/data/local_secrets.json`, `model_config.json`, and
   provider configuration contain persisted user settings. The clean source
   already has the minimum provider/model configuration created through the UI.
   No `.env` was found in the source root. Credentials must remain secret and
   are migrated only as runtime configuration, never into source or reports.

5. **Which runtime data only exists under the old project?**  The legacy tree
   contains historical runs, logs, research wiki entries, literature files, and
   old experiment workspaces. They are not required for a clean Round 5 start.
   The minimal current source runtime data is the UI-managed model/provider
   configuration; generated literature indexes are rebuildable.

6. **Which dependencies exist only in the old `.venv`?**  The old environment
   supplies the declared web/test packages plus `numpy 2.5.1`, a machine-local
   `torch 2.13.0+cu132` build, `torchvision 0.28.0+cu132`, and `torchinfo
   1.8.0`. The root requirements file had omitted `requirements/experiment.txt`;
   it now includes it. The public dependency declaration uses the matching
   official PyTorch CUDA index with the matching `+cu132` versions, so the
   build is independently reproducible rather than copied from the old
   environment. `pandas` and `matplotlib` were absent from the old environment
   and have been declared because the official preflight explicitly requires
   them. The complete-suite validation also established that `scipy==1.18.0` is
   a real experiment dependency: the dataset inspector uses `scipy.io.whosmat`
   to safely inspect MATLAB v4--v7.2 scientific data without loading complete
   arrays. It is now declared in the same experiment requirements group.

7. **What breaks if `D:\竞赛` is deleted now?**  The currently user-launched
   backend still uses its Python executable. It must be stopped and replaced by
   `D:\Gewu\.venv` before legacy removal. Historical runtime data also remains
   only in that old tree by design and will not be migrated for clean Round 5.

8. **Does renaming to `D:\Gewu` pose absolute-path risk?**  Yes until the
   currently running server is stopped and the new virtual environment and
   local-GPU setting are created. Source runtime code uses resolved/relative
   paths; after the two report tools were corrected, no executable source path
   is bound to either old root. A post-rename old-path scan and legacy-disconnect
   validation are still mandatory.

## Legacy virtual-environment linkage

The inspected legacy environment is Python 3.12.13. It has no project
editable-install entry and no project `.pth`/egg-link reference; only the
standard `distutils-precedence.pth` was present. The new environment must be
created from the dependency declaration and must not copy `site-packages`,
`Scripts`, or `pyvenv.cfg`.

## Required next actions

1. Stop the legacy-Python-backed server.
2. Rename the clean source directory to `D:\Gewu`.
3. Create a fresh `D:\Gewu\.venv`, install declared dependencies, and configure
   local GPU to use that interpreter.
4. Revalidate without the legacy source directory available before any deletion.
