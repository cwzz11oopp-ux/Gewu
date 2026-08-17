---
name: qwen-scientific-idea-review
description: Use when reviewing a research idea, generated proposal, or research plan for novelty, publication potential, scientific soundness, execution feasibility, experimental testability, risks, and evidence before deciding whether to pursue, revise, or stop it; especially for Qwen/Codex agent workflows.
---

# Qwen 科研 Idea 审阅与可行性评估

将评审视为**带证据的决策**，不是让模型凭印象打分。目标是判断“值得投入下一轮最小验证吗”，并清楚区分：学术价值、实际可执行性、当前证据强度与未知项。

## 适用范围与边界

适用于 LLM 生成或人工提出的计算机科学、AI for Science、数据科学及可通过文献与实验计划审查的研究 Idea。它输出研究决策支持，不替代领域专家、伦理审查、实验安全评估或真实预算批准。

不要把以下结果当作可行性结论：只有摘要、没有检索证据、没有资源约束、没有可证伪实验、或只由同一模型重复自评。此时允许且应当输出 `EVIDENCE_INSUFFICIENT`。

## 输入合同

先将 Idea 标准化为一页 `idea_card`；缺项必须标为未知，不能补造。

```yaml
problem: "要解决的具体问题与受益对象"
claim: "可被证伪的中心主张"
mechanism: "为什么该方法可能成立"
method: "核心方法、变量和比较对象"
scope: "任务、领域、边界条件"
constraints:
  deadline: "若已知"
  budget_compute: "若已知"
  data_access: "若已知"
  people_equipment: "若已知"
target: "目标会议/期刊或实际目标；可为空"
```

若用户未给出预算、数据权限、设备或期限，继续做文献和科学审查，但把执行结论限制为“条件性”，并列出最小待确认项。若这些缺项使 MDE、资源或合规性无法判断，则决策状态必须为 `EVIDENCE_INSUFFICIENT`；该规则优先于“条件性”表述。

## 非协商规则

1. **证据先于分数。** 每个关键判断至少连到一个可追溯来源、已验证资源记录或明确标注的推断。不得编造论文、实验结果、数据许可、算力、价格或引用。
2. **反证与支持同等检索。** 对每个中心主张分别寻找支持、最接近先例和失败/冲突证据；找不到不是“没有”，而是“未检到”。
3. **硬门槛优先。** 科学矛盾、不可获得的关键资源、不可证伪、明显不合规/不安全，任一项成立即不能给 `GO`；加权总分不能抵消它。
4. **将事实、推断、假设分开。** `FACT` 必须有来源；`INFERENCE` 写出推理链；`ASSUMPTION` 写出验证方式与失效影响。
5. **评审者不是作者的啦啦队。** 独立批评先于整合；禁止为了平衡而捏造优点或缺点，也禁止以措辞流畅、篇幅或权威性替代证据质量。

## 系统证据审计合同

运行时会提供 `evidence_audit.registry` 和逐候选 `candidate_audits`。必须遵守：

- 只使用 registry 中的 `evidence_id` 建立主张—证据关系，禁止自行生成证据 ID。
- 每个原子主张标注 `support / contradict / context`，并区分 `DIRECT / INDIRECT / ANALOGY`。
- 类比证据只能支持机制可行性，不能写成目标任务上的直接性能证据。
- `ASSUMPTION` 不得伪装成事实；必须给出验证方式和失效影响。
- 候选的证据门槛为 `FAIL` 时，不得给 `GO`，也不得用高加权分抵消。
- 返回 `claim_evidence_map`；没有可匹配证据的主张进入 `unknowns` 或触发 `EVIDENCE_INSUFFICIENT`。

## 工作流

### 1. 检索与证据账本

用工具检索，不依赖参数记忆。查询要覆盖问题、方法、关键机制、同义词、相邻领域和反例；优先原始论文、官方数据/代码/模型文档和权威数据库。对新近领域应说明检索日期与覆盖窗口。

先广召回，再按**问题、机制、方法、评估设置、结论**五个分面对候选工作重排。保留最接近的 3--8 篇，以及至少一条可能反驳中心主张的证据。维护账本：

| id | 结论 | 类型 | 立场 | 来源与定位 | 强度/局限 |
|---|---|---|---|---|---|
| E1 | 可核验的原子结论 | FACT | 支持/反驳/背景 | URL + 页/节/表（可得时） | 高/中/低及原因 |

未工具检索时，`novelty` 必须是 `UNVERIFIED`，不能给高新颖性分。

### 2. 双轴学术评审

**新颖性（不是语义相似度）**：逐项比较 Idea 与最接近工作在问题、机制、方法、设定和主张上的差异。给出 `novelty_delta`：

- `0` 重复或已被直接覆盖；
- `1` 小的实现/参数变化；
- `2` 已知组件组合，价值待证；
- `3` 在新条件下有清晰、可检验的贡献；
- `4` 新机制或新问题表述，并有初步合理性；
- `5` 可能开辟方向，但证据仍不足时不以分数代替验证。

**学术/科学合理性**：检查中心因果或机制链、适用前提、与已知理论/实证的冲突、混杂因素和外推边界。明确“机制尚未证明”与“已被证伪”的区别。

### 3. 执行可行性与可验证性

逐项审查并给出条件、证据和风险：

| 维度 | 必答问题 |
|---|---|
| 数据/材料 | 能否合法获得？规模、质量、标注、偏差是否匹配主张？ |
| 方法/工程 | 关键算法、工具链、实现能力与未知依赖是什么？ |
| 资源 | 计算、API、人员、设备、费用和时间的量级；哪些为单点失败？ |
| 实验 | 是否有可证伪假设、强基线、指标、统计/重复方案和消融？ |
| 合规/复现 | 许可、隐私、安全、伦理和复现工件是否可接受？ |

设计一个**最小判别性实验（MDE）**：它必须直接检验核心机制而非只展示总体指标。写明：数据/材料、干预与对照、基线、主指标、预注册式成功阈值、失败阈值、预计成本、停止条件及下一步。若已充分理解主张却证明不存在可证伪检验，`testability_gate = FAIL`；若主张/机制信息不足以设计 MDE，则为 `UNKNOWN` 并触发 `EVIDENCE_INSUFFICIENT`。

### 4. 独立审查、分歧与校准

至少产生以下彼此独立的草案，先不共享结论：

- `literature_reviewer`：最近先例、重合和新颖性差分；
- `scientific_critic`：机制、假设、反例和混杂；
- `execution_reviewer`：数据、工程、资源、合规；
- `experiment_reviewer`：MDE、指标、基线、失败门槛；
- `adversarial_reviewer`：寻找会使项目不成立的证据和最致命反驳。

若只有一个模型实例，使用隔离上下文/独立采样并改变角色和证据包；这只是降低相关偏差，**不是**独立专家共识。只在评分或事实发生实质冲突时进行短轮次辩论：每方只能提出带账本 id 的论点，`meta_reviewer` 按证据可验证性与相关性裁决，不按多数或修辞裁决。

使用重复评审的离散度作为不确定性信号，不把 self-consistency 当作正确性证明。报告 `confidence`（低/中/高）及其依据：证据覆盖、来源质量、评审分歧和关键未知项；禁止无校准地输出“80% 可信”。若有历史人工标注，按领域/任务做校准检查（如分箱可靠性和过度自信），再解释概率。

### 5. 决策规则

分别报告 `academic_value` 与 `execution_readiness`（各 0--5），以及风险等级；分数必须能回指证据账本。推荐权重仅在用户未指定时使用：新颖性 20%、科学合理性 20%、潜在影响 15%、实验可验证性 20%、资源/工程可行性 20%、复现/合规 5%。

先执行门槛，再排序：

```text
GO          所有硬门槛通过；MDE 在约束内；关键主张有足够证据。
REVISE      有希望，但一个或多个可修复缺口需要先补证据/缩小范围。
PIVOT       核心贡献被近似工作覆盖，或机制/实验设计需根本重构。
STOP        关键假设已有强反证，或不可行/不合规且无可信缓解路径。
EVIDENCE_INSUFFICIENT
            检索、资源或实验信息不足，无法诚实地作出上述决定。
```

## Qwen / Codex 执行约定

- **工具优先。** 用搜索、论文库、代码库、数据集和本地资源工具收集证据；工具不可用时明确能力边界，绝不模拟调用结果。
- **长上下文分块。** 先为每篇长文抽取带 `paper_id` 与定位的证据卡，再把卡片、检索式和未决问题保存在外部账本；不要把整批论文反复塞入上下文。优先保留最近的工具结果、证据卡和决策约束。
- **推理模式。** 把内部推理与面向用户的证据结论分离；需要深度机制/反驳分析时启用可用的 reasoning 模式，常规账本整理使用非推理/快速模式。不要输出隐含推理链；输出可审计的简明理由即可。
- **结构化输出。** 通过工具/API 的 JSON Schema 约束产出，校验失败时把校验错误交回模型修复。顶层字段固定为 `idea_card`、`evidence_ledger`、`closest_prior_work`、`gates`、`scores`、`mde`、`risks`、`decision`、`confidence`、`unknowns`。保留可读 Markdown 摘要。
- **上下文与工具调用兼容性。** 以当前部署的 Qwen chat template、tool-call parser 与 API 文档为准；推理内容解析并非所有 OpenAI-compatible 服务都支持。把模型版本、上下文上限、检索日期和可用工具写入运行记录。

## 最终交付模板

1. **决策一句话**：`GO/REVISE/PIVOT/STOP/EVIDENCE_INSUFFICIENT` + 最关键原因。
2. **Idea 卡与边界**：主张、机制、约束、目标。
3. **证据化评审**：最近工作差分、支持/反驳证据、科学和实施判断；所有关键句附账本 id。
4. **门槛与评分**：每个 gate 的 `PASS/CONDITIONAL/FAIL/UNKNOWN`，再给可追溯的双轴分数；不得只给总分。
5. **MDE 与失败门槛**：可执行的最小实验、预算级别、成功/失败判据和停止条件。
6. **最大风险与下一行动**：最多 3 项，按信息价值排序；优先做能最快改变决策的检索或试验。
7. **校准声明**：置信度、分歧、未知项、检索日期与来源列表。

## 常见错误

| 错误 | 正确做法 |
|---|---|
| 把相似度低当作新颖 | 对最接近工作逐分面比较，并验证检索覆盖。 |
| 用平均分掩盖不可做的实验 | 先判硬门槛；失败即不 `GO`。 |
| 多个 agent 复述同一证据 | 隔离证据包，并要求新的账本 id 或明确同源。 |
| 让用户压力迫使确定结论 | 报告 `EVIDENCE_INSUFFICIENT` 与最短补证路径。 |
| 把“没有检到”写成“文献不存在” | 说明检索范围、日期和局限。 |

## Design lineage / 设计变更与依据（截至 2026-07-14）

本版保留原 CCF 风格 Idea Reviewer 的标准化、文献检索、`novelty delta`、多评审和 `Accept/Revise/Pivot` 优点；新增独立的科学、资源与实验可行性层，MDE/失败门槛，证据账本，硬门槛优先，以及反自我迎合与校准机制。

- [Idea Novelty Checker](https://aclanthology.org/2025.sdp-1.9/) 支持“广召回—嵌入过滤—分面重排—文献化理由”的新颖性流程。
- [HARPA](https://arxiv.org/abs/2510.00620) 将可检验性与文献锚定连接到实际执行成功；[ScholarEval](https://arxiv.org/abs/2510.16234) 将 soundness 与 contribution 作为证据化评估核心。
- [Many Heads Are Better Than One](https://aclanthology.org/2025.acl-long.1368/) 与 [ResearchAgent](https://aclanthology.org/2025.naacl-long.342/) 支持迭代、多视角的科研 Idea 工作流；[AI Scientist-v2](https://pub.sakana.ai/ai-scientist-v2/paper/paper.pdf) 展示以搜索/实验闭环推进自动科研，而非一次性审稿。
- Qwen 约定以官方 [Qwen3](https://qwenlm.github.io/blog/qwen3/)、[Qwen-Agent 上下文管理](https://qwenlm.github.io/Qwen-Agent/en/guide/core_moduls/context/) 与 [Qwen3 部署/推理和工具解析](https://github.com/QwenLM/Qwen3/blob/main/docs/source/deployment/vllm.md) 文档为准；实际能力必须按所部署模型与服务版本验证。
