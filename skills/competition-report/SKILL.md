---
name: competition-report
description: Write a systematic Chinese undergraduate-thesis-quality research report from verified literature and audited experiment artifacts. Use for the final research report after experiment iteration is complete.
allowed-tools: read_run, read_artifact, render_report
---

# Competition Report

Write the report as a continuous research narrative, not as an artifact dump or a list of conclusions.

## Workflow

1. Build a fact sheet from verified literature, the selected hypothesis, the final plan, every audited experiment result, and its linked revision.
2. Plan the report with these chapters:
   - 研究背景与研究问题
   - 理论依据与研究假设
   - 数据集与实验方法
   - 实验设计与评价标准
   - 实验迭代与方法修正
   - 实验结果与分析
   - 讨论与局限性
   - 研究结论与复现说明
3. Write each chapter independently. Give it a clear purpose, 2–6 developed paragraphs, and transitions from the preceding chapter.
4. Write the abstract only after all chapters are complete.
5. Audit the assembled report for contradictions, repeated conclusions, mixed units, irrelevant references, English prose, and internal implementation fields.

## Paragraph contract

- Write natural formal Chinese at the level of a solid undergraduate thesis.
- Each paragraph must develop one point through context, evidence or method, and interpretation. Avoid paragraphs that only announce a topic or repeat a heading.
- Prefer 180–350 Chinese characters per paragraph. Use shorter paragraphs only for a necessary definition or conclusion.
- Use prose for reasoning, iteration, results interpretation, discussion, and conclusions.
- Use tables only for comparable parameters or numeric results. Use numbered lists only for reproducible procedures.
- Connect chapters logically. Do not restate the conclusion in the abstract, executive summary, results, discussion, limitations, and conclusion with nearly identical wording.

## Evidence and language

- Treat artifacts as the factual boundary. Do not invent measurements, settings, citations, or causal explanations.
- Report negative and null results directly.
- Distinguish percent from percentage points and keep units consistent across rounds.
- State what changed in each iteration, why it changed, and what the next result showed. Do not paste raw revision text.
- Use Chinese for all exposition. Retain English only for established names such as Dropout, FashionMNIST, Test Accuracy, code identifiers, and bibliographic titles.
- Exclude file paths, hashes, artifact IDs, contract IDs, provider internals, commands, raw status values, and file inventories from reader-facing prose.
- Convert `failed`, `partial`, `supported`, booleans, and similar machine values into contextual Chinese statements.

## Reference contract

- Include only verified references directly used in the report.
- Prefer foundational work for the method and dataset plus literature needed to justify evaluation choices.
- Do not fill a quota. Three relevant references are better than fifteen weakly related references.
- Ensure every listed reference is cited or discussed in a chapter, and every cited work appears in the final list.

## Prohibited style

Do not write:

- “作为 AI”“本文旨在”“综上所述可以看出”“具有重要意义”等模板化 filler；
- one-sentence sections;
- multiple fragmented “结论”“判断”“后续处理” blocks;
- raw key-value dictionaries translated into headings;
- unsupported praise, novelty claims, or recommendations.

End with one bounded conclusion: what the evidence supports, what it does not support, and under which dataset, model, parameters, and evaluation procedure the finding holds.
