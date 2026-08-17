# Phase 4 Research Quality & Recovery Report

- Added deterministic two-stage literature selection with dedupe, bounded budget, explicit Stage 1 fields, chunked Stage 2 PaperProfile provenance, and no silent truncation.
- Added production Skill allow-list: common plus exactly the active task scope; foreign Codex/Claude/Gemini/Bash/WebSearch/MCP named skills are excluded.
- Added append-only 3+3 recovery routing (Qwen repairs 1–3, DeepSeek recovery 4–6, then engineering-unresolved) without consuming Idea versions.
- Added formal-positive-only targeted ablation planning preserving the frozen fair contract and requiring independent ResultEvidence.
- No real Run, model request, training, historic-data mutation, or Git commit was performed.
