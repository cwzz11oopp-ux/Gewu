# 自动 Idea 评审与选择设计

## 目标

在候选假设生成后，系统直接使用 `skills/idea-selection/SKILL.md` 对所有候选 Idea 进行带证据的科学评审，并自动选择综合加权得分最高的候选进入后续证据推理、研究计划和实验。用户不再需要手动勾选候选。

选择最高分不等于宣称其已通过所有科学门槛：即使所有候选都是 `REVISE`、`PIVOT`、`STOP` 或 `EVIDENCE_INSUFFICIENT`，系统仍会选择最高分，但会完整保留该决策、硬门槛、风险和未知项。

## 工作流

工作流顺序调整为：

```text
问题理解
  -> 知识整合
  -> 假设生成
  -> 自动 Idea 评审与选择
  -> 证据推理
  -> 研究计划
  -> 实验任务、运行、反馈和报告
```

`idea_selection` 是新的工作流步骤，写入两个 Artifact：

- `idea_review`：对全部候选的审计报告；
- `hypothesis_selection`：由服务端确定的唯一选中候选，供后续步骤读取。

`evidence_reasoning` 和 `research_plan` 都必须要求 `hypothesis_selection` 存在，禁止手动 API 调用绕过自动选择步骤。

若 `hypothesis_generation` 产生空候选列表，立即返回 `HYPOTHESIS_CANDIDATES_EMPTY`。不得保存空 hypothesis Artifact、不得调用 Idea 审评、不得进入证据推理或消耗 Supervisor 修订额度。

## Idea 评审契约

新增 `IdeaSelectionAgent`，使用与其他 Agent 相同的 Qwen Provider，并加载完整的 `idea-selection` Skill 指令。输入为问题、约束、已验证 evidence cards 与规范化候选假设。

模型必须返回每个候选对应的一份审评记录：

```json
{
  "evaluations": [
    {
      "candidate_index": 0,
      "idea_card": {"problem": "...", "claim": "...", "mechanism": "...", "method": "...", "scope": "...", "constraints": {}, "target": "..."},
      "evidence_ledger": [{"id": "E1", "claim": "...", "type": "FACT", "stance": "support", "source": "...", "strength": "medium"}],
      "closest_prior_work": [],
      "gates": {"testability": "PASS", "resources": "CONDITIONAL", "compliance": "PASS"},
      "scores": {"novelty": 0, "scientific_soundness": 0, "impact": 0, "testability": 0, "execution_feasibility": 0, "reproducibility_compliance": 0},
      "mde": {},
      "risks": [],
      "decision": "REVISE",
      "confidence": "medium",
      "unknowns": []
    }
  ]
}
```

六个分项分数均为 0–5。服务端而非模型计算总分，使用 Skill 指定权重：新颖性 20%、科学合理性 20%、影响力 15%、可验证性 20%、资源/工程可行性 20%、复现/合规 5%。按总分降序选择；同分时选择原候选索引最小者。模型不得自行决定 `selected_index`。

服务端验证每个原始候选恰有一项评审、索引无重复且范围正确、各分项数值和决策枚举合法。格式无效时可请求有限次修订；所有候选的评审都不得缺失。模型调用失败（含额度或 HTTP 错误）直接向用户返回 Provider 错误，绝不被计为候选内容修订。

## UI 与可审计性

候选假设区域展示候选、自动选择标记、总分、决策、主要风险和选择理由；不再提供“确认选择”按钮或人工勾选来推进工作流。自动选择步骤完成后，自动化继续运行。

保留“新增候选假设”入口。新增成功后，系统清除旧的 `idea_review` 与 `hypothesis_selection`，自动重新运行 `idea_selection`，以新的全体候选作为评审对象；任何后续 Artifact 均按现有 rerun 规则失效。

每次步骤追踪记录实际模型模式、Skill ID、评审数量、各候选分数和服务端选中的索引；不记录 API Key。选中候选的 `decision`、`gates`、`risks` 与 `unknowns` 传入证据推理、研究计划和报告，使低置信度或未通过门槛不会被隐去。

## 测试

- 空候选在假设生成阶段返回 `HYPOTHESIS_CANDIDATES_EMPTY`，且不会产生 `hypothesis`、`idea_review`、`reasoning` Artifact。
- 有效评审会按服务端的默认权重自动选择最高分候选，而不是相信模型给出的选择。
- 同分时稳定选择最小索引。
- `REVISE` 与 `EVIDENCE_INSUFFICIENT` 的最高分候选仍可被选中，并保留风险状态。
- 评审输出缺候选、索引重复、分数越界或决策非法时被拒绝。
- `evidence_reasoning` 和 `research_plan` 在没有 `hypothesis_selection` 时拒绝运行。
- 前端自动化顺序包含 `idea_selection`，且不包含人工暂停或确认选择。
- 现有后续流程继续从自动选择的 Artifact 取得候选。
