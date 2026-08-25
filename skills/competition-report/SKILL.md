---
name: competition-report
description: Write a systematic Chinese undergraduate-thesis-quality research report from verified literature and audited experiment artifacts. Use for the final research report after experiment iteration is complete.
allowed-tools: read_run, read_artifact, render_report
---

# Competition Report

Write the report as a continuous research narrative, not as an artifact dump or a list of conclusions.

## Visual narrative and page balance

Treat figures, tables, and typographic hierarchy as part of the research argument. They must make an evidence-backed relationship easier to scan; they are not decoration and must never introduce unverified facts.

- Establish a visual anchor before the results chapter whenever the corresponding verified artifact exists. In the default eight-chapter structure, use the model-comparison visual in “数据集与实验方法”, the method pipeline and controlled-variable summary in “实验设计与评价标准”, and the research timeline or persisted training curve in “实验迭代与方法修正”.
- Reserve the results chapter for outcome evidence: the main comparison, per-seed pairing, and per-seed delta. Do not move every available figure there simply because the data originates from an experiment result.
- Keep the document visually paced: avoid more than two substantial prose-only chapters in a row when a grounded visual or compact comparison table is available; spread visuals across method, design, iteration, and results instead of appending them as a gallery.
- Use at most one primary figure for a single claim. When two figures answer the same question, prefer the one that reveals the decision boundary more directly; retain both only when they show genuinely different views, such as aggregate comparison and paired seed variation.
- Use a compact parameter/control table only for repeated fields. Do not convert explanatory prose into tables, and do not add emoji, decorative icons, stock imagery, or illustrative screenshots to an academic research report.
- Place each visual immediately after the paragraph that introduces its reading question. Keep its caption with the visual, use the report's single visual style, and let the caption state provenance and scope rather than repeat the surrounding prose.

Before export, perform a visual rhythm check: the opening third should contain at least one eligible method/design anchor; no later chapter should become a dense cluster while earlier chapters remain text walls; headings, tables, captions, and body text must remain visually distinct but restrained.

## Figure production quality

Treat publication quality as readability at the final Word display size, not merely a high pixel count. Use inline PNG figures at 300 DPI or better after placement; do not upscale a low-resolution source as a substitute for a legible chart.

- Use a Chinese-capable sans-serif figure font and reader-facing Chinese labels where a metric key is otherwise long. Keep established method names only when they improve identification.
- Reserve a clear text lane inside every flowchart node. Wrap long labels; never allow text to overlap arrows, borders, data marks, or adjacent nodes.
- Keep legends outside the data region. For more than two series, use a compact multi-column legend or direct labels, and shorten series names before reducing type size.
- If several recorded metrics are not on a common comparable scale, present them as labeled value cards or separate panels with explicit scales; do not imply a comparison through a shared bar height or an arbitrary axis.
- For training small multiples, keep titles above panels and tick labels below panels so panel identity, axes, and data labels do not compete for the same space.
- Before embedding, inspect every generated figure at 100% and reject/regenerate any figure with clipping, label overlap, legend intrusion, empty decorative space, or an unreadable caption relationship.

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
