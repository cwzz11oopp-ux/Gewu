---
name: paper-write
description: Draft and revise a research paper section by section from an approved plan, using verified citations and audited experiment results while avoiding fabricated claims and generic AI prose.
---

# Paper Writing

Write one approved section per call so progress can be saved and reviewed.

## Writing requirements

- Follow the approved section purpose and evidence list.
- Write in the language selected by the user.
- Use natural academic prose. Avoid generic openings, inflated novelty claims, repeated summaries, and phrases that sound like an AI template.
- Explain why a method or comparison is used, not only what was done.
- Report negative and partial results directly.
- Use exact experiment values; do not infer missing values.
- Cite only verified references supplied in the input.
- Keep limitations concrete and tied to design, data, compute, or statistical power.
- Return clean prose, not Markdown headings and not a complete LaTeX document.

## Section review

Before returning a section, check:

1. Every numeric statement exists in the supplied result artifacts.
2. Every citation key exists in the supplied reference set.
3. The section does not contradict the terminal verdict.
4. The prose contains interpretation rather than a raw artifact dump.
5. Internal paths, hashes, artifact IDs, prompts, and tool names are absent.

## Revision

When feedback is provided, revise only the affected content. Preserve verified numbers, citations, and claims that were not challenged.
