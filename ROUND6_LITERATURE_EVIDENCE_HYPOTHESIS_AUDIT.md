# Round 6 文献—证据—假设链路审计（只读）

## 审计范围与证据等级

- 审计对象：`run_9e7d3cd0c97f`（MobileNetV2 Fashion-MNIST Chinese），不是已完成的 `run_a5c60cfe56ff`。
- 执行约束：未修改 workflow、Skill、前端、Artifact 或 Run；未触发检索、重跑或创建 Run；未创建 Git commit。
- 取证来源：`backend/data/runs.json` 中的持久化 Run / Artifact / event、运行中的只读 `GET http://127.0.0.1:8000/api/runs/run_9e7d3cd0c97f`、后端及前端源码。
- API 读数（审计时）：`status=failed`、`current_step=evidence_reasoning`、`updated_at=2026-08-15T23:03:30.656281+08:00`、14 个 Artifact、6 个 event；与持久化状态一致。
- 标记：**已确认** = 持久化状态、API 或源码直接证实；**推断** = 根据源码控制流重建；**不可得** = 本次持久化日志没有该数据，不能补造。

## 结论摘要

这两个表象有共同的“选择/展示压缩”背景，但不是“45 → top-5 → 第 5 条失败 → 全流程失败”的真实执行链。

1. **已确认**：45 是去重后的可导出文献集，不是最终假设模型的完整上下文。后端先从 60 个原始候选去重到 45，再做 24 篇核心文献筛选；`PromptContextBudget.max_reference_chars=7000` 最终只将 5 张文献卡传给 Qwen 假设生成。这是实际的 LLM 上下文信息压缩，不能仅归因于 UI。
2. **已确认**：Research Tree 另有独立前端截断：`papers.slice(0, 5)`、`evidence.slice(0, 5)`、`hypotheses.slice(0, 4)`；且在 ID 不能匹配时按数组下标强行补边。因此 P01→E01→H1 一类边不是真实 provenance。
3. **已确认**：`EVID-75575ca7930c` 没有失败。它是来自 Fashion-MNIST 文献综述的一条 verified、`contradict`、`DIRECT` 证据；它没有进入任一候选的 `contradicting_evidence`，只是被列入所有候选共同的 30 条 gap evidence。
4. **已确认**：`evidence_reasoning` 步骤本身完成（22:45:05–23:03:29，`error=null`）。随后自动选择阶段发现四个候选均 `evidence_insufficient`，持久化 `NO_SELECTABLE_HYPOTHESIS` 后抛出 `ValueError`。Orchestrator 的宽泛异常处理将整个 Run 标为 failed，并保留 `current_step=evidence_reasoning`。
5. **已确认**：本次 Qwen 调用成功并结构化返回；没有 provider/API、超时、JSON、枚举或 conflict serializer 错误证据。请求级 token、HTTP、重试和原始响应没有被持久化，因此不能声称存在或不存在某一次底层 HTTP 故障，只能说最终成功调用没有记录异常。

## 1. 45 篇文献的真实流向

### 数量与阶段

| 阶段 | 数量 | 结论 | 证据 |
|---|---:|---|---|
| 原始检索候选（raw candidate cards） | 60 | `Retrieved` 的最原始计数；不是 60 篇已验证、去重论文 | `art_e0b9dd2ab0b2.content.sources.raw_candidate_count` |
| 去重后、可导出的 references | 45 | UI 所称“45 篇文献”的准确含义；也是 `dedup_count` | 同 Artifact 的 `dedup_count=45`、`references.length=45` |
| 核心筛选集 | 24 | 由检索 policy 的相关性、质量、时效与多样性排序后，取核心集 | `core_references.length=24`；`max_core_reference_count=24` |
| 进入假设生成 Qwen 的文献卡 | 5 | 实际 Hypothesis Agent 上下文，不是 45 或 24 | 2026-08-15 22:45:04 event 的 `input_summary.evidence_count=5` |
| 初始 Evidence Reasoning 选定文献卡 | 12 | 从 45 张 verified references 依候选已有 evidence basis、相关性、可靠性筛选 | reasoning Artifact 的 `targeted_retrieval.original_evidence_count=12`；`_focused_evidence_for_candidates(..., limit=12)` |
| 定向补检索得到的文献卡 | 73 | 两轮恢复后的 literature registry 总量；不是初始 45 的简单 top-k | `literature_registry` 的 73 条卡 |
| 产生 claim-level Evidence 的唯一 paper_id | 55 | Evidence registry 中可观测到的唯一 `paper_id` 数；73 张卡含重复或缺乏 paper_id 的卡 | `evidence_registry` 199 条中 unique `paper_id=55` |
| claim-level Evidence 总数 | 199 | 已提取的 `EVID-*` 记录总数 | `evidence_registry.length=199` |
| 进入 gap reasoning 的唯一 Evidence | 30 | 30 个 `gap_evidence` ID，四个 candidate map 共享同一集合 | `research_gaps.length=30`；unique `gap_evidence=30` |
| 进入假设生成的 `EVID-*` | 0 | 顺序上假设生成先于 evidence reasoning；其输入为 5 张 literature card，而非 evidence/gap ID | hypothesis event 与 Artifact schema |
| 假设候选 | 4 | `CAND-001` 至 `CAND-004` | `art_b293626cc404` |

因此，“45 篇”是**真实持久化的、去重后的检索结果池**，但绝不是所有都进入 Qwen Hypothesis prompt 的完整 Research Context。实际链路为：

```text
60 raw candidate cards
  → 45 deduplicated/exportable references
  → 24 core references（科学检索/多样性筛选）
  → 5 literature cards（7,000 字符 LLM prompt 预算）
  → 4 hypotheses

45 references
  → 12 initial candidate-focused cards（Evidence Reasoning）
  → 2 rounds targeted retrieval
  → 73 literature cards / 199 EVID records / 30 gaps
  → 4 candidate assessments / 0 selectable
```

### 限制分类

| 位置 | 限制 | 分类 | 影响 |
|---|---|---|---|
| `literature_policy.py` | 最多 5 query、每 query 8、每 source 30、最多 60 candidate、24 core | 检索 reranking / 科学选择 | 显式、可配置、可审计 |
| `knowledge.py` | `ranked[:max_candidate_count]`、`_diverse_core(..., 24)` | 科学选择 | 45 在本 Run 未碰到 60 上限；仍被压缩到 24 core |
| `prompt_context.py` | `max_reference_chars=7000`、`select_units` | LLM context limit | 24 张核心卡最终只选入 5 张；是实质信息损失 |
| `engine.py` | `_focused_evidence_for_candidates(..., limit=12)` | Evidence Reasoning 的候选相关上下文选择 | 初始 45 → 12，后有两轮定向补检索 |
| `researchViewModel.ts` | `slice(0,5)` / `slice(0,4)` | UI display limit | 只影响显示，但配合伪边会误导 provenance |
| `bootstrap/greenfield.py` 的 `[:5]` 等 | bootstrap / 示例路径 | 非本 Run 执行路径 | 与本次结论无因果关系 |

## 2. Hypothesis Agent 的真实输入与 H1–H4 provenance

### 实际 Qwen 调用

**已确认**：22:45:04 的 `Idea Agent` event 为 `hypothesis.generate`，模型 `qwen3.7-max`、route `reasoning`、`thinking_enabled=true`、无 fallback、`json_repaired=false`、`shape_normalized=false`。输入摘要为 `evidence_count=5`。

调用前 `engine.py` 将 `core_references`（若为空才回退 `references`）转换为 literature cards，再以 7,000 字符预算选择。传入的是 compact problem 与每张卡等价字段：title、year/source、verified identifier、intent/relevance、evidence summary / claim、URL 等。此时尚未运行 claim extraction/gap analysis，故不存在传入的 `EVID-*`、`gap_ids`、candidate-specific supporting/contradicting maps。

**已确认**：本 Run 没有 DeepSeek 假设或 evidence-reasoning 调用记录；相关执行者均为 Qwen。不能把后续的 199 个 EVID 当作 Qwen hypothesis 的输入。

### 初始候选的已持久化来源

下表的 H1–H4 是 UI 对候选位置的显示含义；原始 Artifact 实际 ID 是 `CAND-001` 至 `CAND-004`，其本身没有 `id` 字段。

| UI 显示 | 原始候选 | `paper_ids` / source | `evidence_ids` | `gap_ids` | 已持久化证据基础 |
|---|---|---|---|---|---|
| H1 | CAND-001 | `arxiv:2208.03641` | 非 `EVID-*`；`supporting_evidence_ids=[arxiv:2208.03641]` | **未保存** | *No More Strided Convolutions or Pooling...*；另有“未验证推断” |
| H2 | CAND-002 | `arxiv:2406.03478` | 非 `EVID-*`；`supporting_evidence_ids=[arxiv:2406.03478]` | **未保存** | *Convolutional Neural Networks and Vision Transformers for Fashion MNIST Classification...*；另有“未验证推断” |
| H3 | CAND-003 | `arxiv:1808.03818` | 非 `EVID-*`；`supporting_evidence_ids=[arxiv:1808.03818]` | **未保存** | *Automatically designing CNN architectures using genetic algorithm...*；另有“未验证推断” |
| H4 | CAND-004 | `arxiv:2208.03641` | 非 `EVID-*`；`supporting_evidence_ids=[arxiv:2208.03641]` | **未保存** | 同 H1 的论文；另有“未验证推断” |

这说明初始 hypothesis Artifact 的 provenance contract 不完整：它只有 URL/title/arXiv 字符串和 `evidence_basis`，没有可与后续 Evidence Registry 连接的 `paper_id`、没有 `EVID-*`、没有 `gap_ids`。所以不能诚实地生成 H1–H4 的完整 claim-level lineage；“不存在”是审计结论，不是空数据可被 UI 以一条代表边替代的理由。

后续 evidence reasoning 的 Candidate Evidence Maps 是另一层、后生成的 provenance：CAND-001/002/003/004 分别有 7/8/8/5 条 supporting evidence；仅 CAND-002 有一条 actual contradicting evidence (`EVID-e10c05b66a4f`)；每个候选均关联同一组 30 条 gap evidence。这些 map 不能倒写成“假设生成时实际输入的证明”。

## 3. 为什么页面只显示 P01–P05，以及为何出现伪 1:1 lineage

**已确认，前端可复现：**`frontend/src/components/researchViewModel.ts` 的 `buildTree`：

1. 仅保留 `visiblePapers = papers.slice(0, 5)`、`visibleEvidence = evidence.slice(0, 5)`、`visibleHypotheses = hypotheses.slice(0, 4)`。
2. Evidence 的 `sourceId` 是 registry 中实际 `paper_id`，如 `PAPER-d2cf9ab105b4`；而前 45 个 reference 没有相同的 `paper_id`，`normalizePapers` 回退生成 `P01`…`P45`。
3. 查找失败后 graph builder 用 `visiblePapers[index % visiblePapers.length]` 强制为 Evidence 配一篇论文。
4. Hypothesis 边完全按下标：`visibleEvidence[index % visibleEvidence.length]`；没有读取 candidate evidence map、source URL 或 gap lineage。

故图中的 P01→E01、…、P05→E05、E01→H1、…、E04→H4 是**布局用的 synthetic index pairing**，不是 API serializer 截断后的真实一对一科学来源。具体地，前 5 个 evidence rows 为：前三条来自同一篇 BlurPool 文献、第四和第五条来自 Fashion-MNIST 综述；第五条因 ID 不匹配被前端强配为 `P05`，尽管其真实 `paper_id` 是 `PAPER-d2cf9ab105b4`，且根本不是 reference 列表中的第五篇。

## 4. `evidence_reasoning` “失败”的真实根因

### 真实事件与 Artifact

| 时间（+08:00） | 事实 |
|---|---|
| 22:45:05.218 | `evidence_reasoning` step 开始 |
| 23:03:29.402 | `Critic Skill` event：`Reviewed every candidate and paused for human hypothesis selection.`；`critic.evidence_reasoning` / `qwen3.7-max` 成功，未 fallback、未 JSON repair、未 shape normalization |
| 23:03:29.688 | step 记录为 `completed`，`error=null` |
| 23:03:30.607 | `Workflow Orchestrator` event：`error=NO_SELECTABLE_HYPOTHESIS`、`error_type=ValueError` |
| 23:03:30.656 | Run 被标为 `failed`，`current_step` 仍为 `evidence_reasoning` |

`art_7ce9a4ee8996`（parent 为 reasoning Artifact `art_3a63feefdcd3`）的正式失败内容是：

```text
code: NO_SELECTABLE_HYPOTHESIS
message: All reviewed hypotheses are rejected or evidence-insufficient.
required_candidates: 4
completed_valid_candidates: 4
```

四个 candidate assessment 均为 `evidence_insufficient`：CAND-001/002 的最终建议为 `TARGETED_RETRIEVAL`，CAND-003/004 为 `REJECTED_EVIDENCE_UNAVAILABLE`。系统已经做完最多两轮恢复（`retrieved_count=90`、`new_evidence_count=154`），仍没有可自动选择候选。

### 分类结论

- provider/API failure：**未发现，且最终调用成功证据与该分类相反**。
- timeout：**不可得**；没有持久化此调用的 timeout 记录，不能凭缺失断言“无 timeout”。
- malformed JSON / parser / schema validation：**未发现**；成功 event 明确为 `json_repaired=false`、`shape_normalized=false`，且 reasoning Artifact 已完整持久化。
- conflict-evidence handling failure：**未发现**；`contradict` 是当前 Skill 和 evidence pipeline 接受的合法 stance。
- context truncation：**存在且重要**（45→24→5、45→12），但没有证据证明它是这次 `NO_SELECTABLE_HYPOTHESIS` 的唯一或直接抛错点。
- 直接根因：**已确认，为所有候选均未通过 evidence-selection business/scientific gate，随后 workflow 把该业务结果传播成 Run failed。**

### stack trace / provider 原始请求

**不可得（原始运行日志未持久化）**：没有 `EVID-75575ca7930c` 专属 provider 请求；Evidence 提取是本地确定性函数，不是一证据一模型请求。持久化 event 也不含 request start/end、单调用 latency、input/output tokens、HTTP status、attempt/retry count 或完整 raw response。源码虽在某些失败分支可形成诊断片段，但本 Run 未持久化该类错误条目。

可由源码精确重建、但不是历史 traceback 原文的控制流为：

```text
WorkflowEngine 选择/自动推进
  → _require_evidence_reasoned_hypothesis_selection(...)
  → completed reviews == 4；viable == []
  → add_artifact(model_failure, code=NO_SELECTABLE_HYPOTHESIS)
  → raise ValueError("NO_SELECTABLE_HYPOTHESIS")
  → WorkflowOrchestrator 的 except Exception
  → update_workflow_state(status="failed")
```

这就是为什么本报告不能给出不存在的 provider stack trace：实际异常是 workflow 的 `ValueError`，不是 Qwen response parser 的异常。

## 5. P05 → `EVID-75575ca7930c` “冲突”分析

### 真实 Evidence 记录

| 字段 | 值 |
|---|---|
| Evidence | `EVID-75575ca7930c` |
| parent Artifact | `art_3a63feefdcd3`（reasoning） |
| paper_id | `PAPER-d2cf9ab105b4` |
| 标题 | *Convolutional Neural Networks and Vision Transformers for Fashion MNIST Classification: A Literature Review* |
| URL | `http://arxiv.org/abs/2406.03478v1` |
| claim | `Utilizing the Fashion MNIST dataset, we delve into the unique attributes of CNNs and ViTs.` |
| evidence type / relation | `DATASET_OBSERVATION` / `DIRECT` |
| stance | `contradict` |
| verification | `verified` |
| confidence / relevance | 0.746 / 0.4527 |
| provider / model / attempt | **不适用 / 不可得**：本地 extraction 的一条输出，非独立 provider call |

同论文中紧邻的一条支持记录 `EVID-49bdfc692cc9` 与它的逐字段差异仅为 evidence ID、claim、`METHOD` vs `DATASET_OBSERVATION`、`support` vs `contradict`；两者 paper、`DIRECT` relation、verified 状态、confidence 和 relevance 相同。没有 conflict enum 被拒、字段缺失或序列化异常。

源码中 `extract_claim_evidence` 的 stance 分类是轻量级关键词规则：句子包含 `however`、`but`、`fails`、`limited`、`limitation` 或 `not` 时即为 `contradict`；`analyze_research_gaps` 会将 `contradict` 纳入 gap 集。因而这里的“冲突”是自动 claim-level 分类，不等于“该 Evidence 运行失败”，更不等于“P05 失败”。

更重要的是，`EVID-75575ca7930c` 不在任何 candidate 的 `contradicting_evidence` 中；它仅位于四个 candidate map 共同的 30 条 `gap_evidence`。真正进入 candidate-level contradicting map 的只有 CAND-002 的 `EVID-e10c05b66a4f`（*Block Sparse Flash Attention*，`INDIRECT`）。因此 UI P05 的红色“冲突/失败”既误把 stance 映成 node failed，也错误暗示它造成了流程停止。

补充：Skill contract 要求 evidence 保留 source location；该记录有 title/URL，但没有 `locator/location/section` 字段。此为**已确认的 provenance contract 缺口**，不是本次运行失败异常。

## 6. 状态传播为何不一致

后端真实状态为：hypothesis generation 已完成、四个候选为 `candidate`；evidence reasoning 已完成；自动 selection 因无可选候选而把 Run 置 failed；后续 research plan 等步骤仍 pending。

前端 `normalizeHypotheses` 把非 selected、非 refuted 的候选统一标成 `candidate`；`buildTree` 又把任何这类 candidate 强制显示为 `thinking`。它没有读取 Run failed、`NO_SELECTABLE_HYPOTHESIS` 或 assessment=`evidence_insufficient`。所以 H1–H4 的“推理中”不是后端仍在推理、也不是 placeholder 正在运行，而是一个前端状态映射缺陷。

更准确的展示语义应是“候选已生成；证据不足 / 未选中”，并将 parent Run 失败明确为“自动选择未找到可验证候选”（或按产品定义转为可恢复的人工决策边界），而非宣称 evidence reasoning 请求失败。

## 7. 两个问题是否同源

**结论：部分相关，非单一故障链。**

- 共同点：两处都有压缩。后端对 LLM 上下文做 24→5 与 45→12 的选择；前端再做 5/5/4 的展示截断。
- 非共同点：没有“第 5 个 Evidence 调用”——`EVID-75575ca7930c` 是 199 条本地 claim extraction 记录中的第五条。它不是独立 Qwen 调用，且没有失败。
- 真实停止链是“4 个候选完成审查 → 两轮定向恢复完成 → 全部 evidence insufficient → `NO_SELECTABLE_HYPOTHESIS`”。
- 风险关联：初始 hypothesis 仅看 5 张卡、初始 critic 仅看 12 张卡会影响候选和证据覆盖质量，可能提高全部候选 evidence-insufficient 的概率；但现有持久化证据不足以证明它是本次唯一因果原因。

## 8. 当前 policy：是否单条 Evidence 失败即失败

当前设计**不是**“任意一条 Evidence 失败就立刻终止”：

- Claim-level evidence 可是 `support`、`contradict` 或 `neutral`；冲突证据是合法输入。
- 每个候选分别有 evidence map、missing/unverified 信息和 checkpoint。
- 没有可选候选时必须 candidate-specific targeted retrieval；本 Run 的上限两轮已执行。
- 恢复耗尽时只把对应候选标为 `REJECTED_EVIDENCE_UNAVAILABLE`，其他候选应保持可选资格。
- 最终只有在完成 4 个 review 且没有任何 viable candidate 时，才产生 `NO_SELECTABLE_HYPOTHESIS`。

本 Run 的问题不是“单 Evidence failure 无容错”，而是：业务性无可选候选被 orchestrator 的通用异常策略折叠成 failed Run，且 UI 将 claim stance、阶段状态和合成边混为一谈。

## 9. 后续修复建议（仅建议，未实施）

### P0 — 科学正确性与可审计 provenance

- 在 hypothesis Artifact 创建时保存稳定的 `paper_id`、输入文献卡 ID、输入集合 hash、`supporting_evidence_ids` / `supporting_gap_ids`（若阶段顺序保留，则明确它们当时不可得）；禁止把 arXiv 字符串伪装为 EVID ID。
- 为每个 claim Evidence 强制保留 locator（abstract sentence index、section 或文档位置），并让 stance 从可解释的局部文本规则/模型判断连同理由持久化，避免仅关键词“not”产生科学上过强的“冲突”语义。
- 对 24→5 和 45→12 记录输入 ID 清单、排序分数、淘汰理由与 coverage 指标；必要时设定每个 hypothesis 的多源/主题覆盖下限，避免字符预算静默决定科学依据。

### P1 — workflow reliability / 状态语义

- 将 `NO_SELECTABLE_HYPOTHESIS` 作为明确的可恢复科学决策边界（例如 `awaiting_hypothesis_revision` / `no_selectable_hypothesis`），不要被通用 `except Exception` 覆盖成“evidence_reasoning failed”。
- 让 step 结果、auto-selection 结果、Run 状态分别持久化；若仍需 failed，也应有 `failure_stage=hypothesis_selection`，而非保留已经 completed 的 evidence_reasoning 为失败阶段。
- 持久化每次 LLM 调用的脱敏 request ID、开始/结束、duration、HTTP、attempt/fallback、token usage 和受限长度 raw/摘要，保证下一次审计可给出实际 traceback 与请求级证据。

### P2 — provenance / visualization

- Graph edge 必须由真实 `paper_id → evidence_id → candidate_id/gap_id` 生成；若 lineage 缺失，显示“来源未建立”，绝不能 index fallback 补造边。
- UI 同时显式呈现 `60 → 45 → 24 → 5`、`45 → 12 → recovery 73 → 199 → 30`，以计数徽标/展开层代替把全部研究压成五条代表线。
- 不把 `stance=contradict` 映射为 node `failed`；显示为“矛盾/限制证据”，并区分其是否实际被某 candidate map 使用。

### P3 — UI presentation

- 把 H1–H4 从“推理中”改为“候选已生成，证据不足 / 未被选择”，并显示 `NO_SELECTABLE_HYPOTHESIS` 的可恢复说明。
- 展示“前 5 条预览，共 45 篇 / 199 条证据”，提供分页或展开；在没有真实 lineage 时提供 provenance warning，而非图形暗示因果。
- 对 Run failed 页面显示精确错误 code、业务阶段与关联 Artifact：`art_3a63feefdcd3`、`art_7ce9a4ee8996`。

## 10. 审计停止点

本报告完成后停止。未实施任何建议，未改变目标 Run 的科学状态、Artifact、文献、实验或 Git 状态。
