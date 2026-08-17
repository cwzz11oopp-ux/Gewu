# AI Scientist V2 Final Demo QA

Date: 2026-08-11 (Asia/Shanghai)

## Scientific records

- Session: `research_dc8671de582b`
- Selected branch: `branch_ed1838f55920`
- Upstream commit: `7bc720e951fe422b8f8814aa5aa1b64121d26b4c`
- Variant commit: `195bbb8904619518da234566f4a9ea4d67d72ee1`
- Main experiment: `micrograd_live_exp_1`, `relu_conformance_score 0.875 → 1.0`
- Full-revert ablation: `micrograd_live_exp_2_ablation`, prior variant `1.0 → 0.875`, original protocol fingerprint `eae7faa373b82d0f1127fe45efc7aea4c1c3481d65a4504544ad2f602085ca49`
- Bounded robustness: `micrograd_live_exp_3_robustness`, `0.875 → 1.0`, boundary protocol fingerprint `37d5805e41f6fc9b750aa7b33ea14c75306d4ac26ec5879b2c31d0314b5ea897`
- Final belief: support `0.95`, uncertainty `0.25`; branch status `validated`
- Final live Critic: `qwen3.7-max`, reasoning route, no fallback or JSON repair

The bounded robustness grid contains only `±0.0`, `±1e-15`, `±1e-12`, and `±1.0`. NaN, infinity, subnormal values, and general floating-point behavior are explicitly excluded from supported claims.

## Invalid ablation diagnostic

The first live planner ablation proposed `max(0.0, x)`. It retained the target positive-zero canonicalization capability and scored `1.0`, so it was rejected as an invalid mechanism-removal control. The diagnostic is preserved at `backend/data/final-demo-hardening/micrograd/invalid-live-planner-ablation.json`; it is absent from the final ExperimentRegistry, EvidenceStore, Belief update, and Claim-Evidence Graph.

## UI verification

The local `/v2` page was tested in the in-app browser after a production build:

- Demo A opens as an offline deterministic snapshot.
- Demo B switches to session `research_dc8671de582b` without a network request.
- The public trajectory contains nine research-facing stages ending in `Final Conclusion`.
- Three audited ExperimentRecords and `SUPPORTED`, `PARTIALLY_SUPPORTED`, and `NOT_SUPPORTED` claims are visible.
- No browser console warning or error was emitted.
- Document width no longer overflows the viewport after constraining the trajectory grid item.

## DOCX QA

Artifact: `backend/data/final-demo-hardening/micrograd/micrograd-research-report.docx`

Structural QA passed:

- valid DOCX ZIP package;
- one section, 312 non-empty paragraphs, 78 headings, 20,740 extracted text characters;
- all 14 required V2 canonical report sections are present;
- no Unicode replacement characters and no credential marker matches.

Visual QA limitation: LibreOffice was absent. A silent `winget` install was stopped after the five-minute limit because it did not complete and no `soffice.exe` was installed. The required `render_docx.py --emit_pdf` attempt then failed with `FileNotFoundError [WinError 2]` at the DOCX-to-PDF converter boundary. No PDF/PNG pages were produced, so this release does not claim page-by-page visual verification.

## Final regression

- `.venv\Scripts\python.exe -m pytest tests/backend -q`: `453 passed, 3 skipped` in `123.65s`.
- Skips: two real CUDA smoke tests require `RUN_GPU_SMOKE=1`; one dataset inspection test requires the absent optional `scipy` package.
- `node --test frontend/tests/ui-contract.test.mjs`: `33 passed, 0 failed`.
- `pnpm --dir frontend run build`: TypeScript and Vite build succeeded; 1,789 modules transformed.

Credential-marker scans found no `QWEN_API_KEY`, `qwen_api_key`, `api_key`, `Authorization`, or `Bearer` marker in the final text artifacts or DOCX package members.
