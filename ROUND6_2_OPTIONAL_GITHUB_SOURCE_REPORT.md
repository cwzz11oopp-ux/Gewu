# Round 6.2 — Optional GitHub Source Input Report

## Outcome

Completed the minimal optional GitHub source-input capability on the current
Round 6.1 baseline. No real research Run was created or mutated, no repository
code was executed, and no Git commit was created.

## Contract implemented

- `RunRecord.github_repository_url` is optional and defaults to `null`.
  Historic checkpoints without the field continue to validate.
- `POST /api/runs` and the research UI accept the optional URL. The UI labels it
  as optional, uses `https://github.com/owner/repository` as its placeholder,
  and explains in Chinese that inspection is read-only.
- With no URL, the workflow does not call the source inspector, creates no
  GitHub/code-evidence artifact, and persists
  `source_code_evidence_ids: []` for generated hypotheses.
- With a public GitHub URL, the new source inspector reads only a bounded API
  tree and selected README/Python/config files through HTTPS. It never clones,
  installs, shells out, downloads data, or executes repository content.
- It derives Code Evidence exclusively from parsed Python AST declarations and
  actual content hashes. Each record contains:
  `code_evidence_id`, `repository_url`, `repository_commit`, `source_file`,
  `symbol`, `line_start`, `line_end`, `claim`, and `file_hash`.
- Parsed data is persisted as `github_source` and `code_evidence` artifacts;
  Code Evidence is supplemental `research_synthesis` context and does not
  replace the existing literature → theme/future-work → gap pipeline.
- Hypotheses receive that context and may cite only persisted
  `source_code_evidence_ids`. Invented IDs are surfaced as provenance validation
  errors before normalization; they are not positionally repaired or silently
  attached.
- Invalid URL, API/read/permission failure, or no readable selected file yields
  `github_source_status: unavailable` plus warnings, while normal research
  continues. The detail strip renders `未提供` / `已解析` /
  `读取失败（研究已正常继续）`.

## Regression coverage

`tests/backend/test_optional_github_source.py` adds synthetic, offline fixtures
covering:

1. actual file-tree/source reading → AST Code Evidence → synthesis → hypothesis;
2. no URL leaves the existing flow untouched and does not inspect GitHub;
3. invalid URL degrades to unavailable while hypothesis generation continues;
4. selected-file read failure degrades to unavailable while research continues;
5. legacy Run checkpoint without the new field;
6. API persistence of the optional field; and
7. rejection/normalization behavior for a fake `CODE-…` provenance ID.

## Verification

- Focused backend regression:
  `13 passed in 2.10s`
  (`test_optional_github_source.py` + `test_research_synthesis.py`)
- Frontend production build: passed (`tsc -b && vite build`).
- Full backend regression:
  `540 passed, 2 skipped, 0 failures, 0 errors` in `91.796s`.

## Scope guard

No Fashion-MNIST/IPIX E2E or external research run was started. Existing
validator, bundle, harness, dataset, scientific-contract, and repair-loop
behavior was not weakened or bypassed.
