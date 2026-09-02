---
name: experiment-iteration
description: Analyze an audited experiment result, identify scientific knowledge gaps, retrieve targeted evidence, compare bounded optimization directions, and select the smallest informative next experiment. Use for every feedback_revision step before revising a plan or accepting a terminal result.
allowed-tools: read_run, read_artifact, audit_result, query_wiki, search_local_literature, literature_search
---

# 实验迭代

将一次迭代视为不可拆分的状态转换：

`已审计结果 → 结果诊断 → 定向资料查询 → 候选方向比较 → 最小修订 → 新实验 → 新审计结果`

每轮结束后生成内部 `research_state`。保留旧产物，但把同类旧版本标记为
`superseded`；把真实运行参数和实测指标标记为 `verified`；把被实验否定的主张
标记为 `rejected`；把尚未验证的推断标记为 `unverified`。记录新旧版本关系和
冲突解决依据，不覆盖历史内容。

## 结果诊断

先提取实测事实、未达标准、改善指标、退化指标、不确定性、方法学问题、原因假设和知识缺口。不得把代码、环境、依赖或数据路径错误包装成科学优化；这些问题交给诊断流程，并冻结科学合同。

只有知识缺口会影响下一轮科学决策时才生成 `literature_queries`。每个查询必须关联触发指标、观察值、待回答问题和检索原因；检索式使用简洁的英文学术关键词，面向用户的解释使用简体中文。

没有外部知识缺口时仍须比较候选方向，但以本轮实测结果和已核验证据为依据，
不得为了执行检索而制造问题。

## 证据与方向选择

将资料标记为 `FACT`、`INFERENCE`、`ASSUMPTION` 或 `CONTRADICTION`。资料只能帮助解释和选方向，不能覆盖真实实验结果。证据不足时输出 `EVIDENCE_INSUFFICIENT`，不得编造来源。

生成 2 至 4 个候选方向，并比较预期改善、证据可信度、信息增益、计算成本和科学风险。优先选择能区分原因的单变量消融，不要只选择声称最可能涨分的方案。

选中方向必须说明：待解决问题、结果依据、资料依据、唯一主要改变、固定控制、目标指标、可能退化项、成功规则、失败规则和停止规则。

方向输出必须包含机器字段 `decision`，取值只能是 `REPORT`、`REVISE` 或 `PIVOT`。当没有安全、可执行且有信息增益的后续实验时使用 `REPORT`；只有存在明确的 `selected_direction` 时才能使用 `REVISE` 或 `PIVOT`。不得通过 `next_action`、`selection_reason` 等自然语言暗示与 `decision` 相反的路由。

## 不变量

- 以最新已审计结果为本轮观察，并使用 `research_context` 中有准确血缘的历史实验。
  明确区分最新结果与保留的最佳方案，不把两者的计划、代码或指标混用。
- 保存完整诊断、检索证据和方向决策后才能调度下一轮。
- 同一个结果不得重复评审；下一轮必须产生新的实验结果。
- 保留已支持主张和有效控制，只改变被点名的弱点。
- 原假设被证伪时不得降低原阈值；可以诚实终止，或把新方向明确标为新的 `PIVOT` 假设。
- 验证型问题可在 `supported` 时停止。冻结策略为优化型时，`supported` 仅说明
  当前假设得到支持；综合独立科学分析之后再决定是否存在值得执行的下一步。
  不可行动的失败、无安全最小实验、预算耗尽或后端无进展限制均应停止。
  不得为凑轮次继续，也不得保证最终一定获得正向结果。
- 发生计划与实际执行冲突时，事实优先级为：实际实验结果 > 运行清单与参数 >
  当前计划 > 当前假设 > 历史版本 > 模型推测。向下一轮和报告只传递解析后的当前事实。

## 串行优化的证据边界

优化方向必须在 `selected_direction` 中填写 `problem_addressed`、`result_basis`、
`source_result_ids`，以及改变变量、固定控制、指标和成败规则。引用的结果 ID 必须
来自实验记忆；选择方向必须是已比较候选之一。使用 `feedback.scientific_synthesis`
的分歧、混杂因素和不确定性，不得只照搬初始反馈的继续/停止结论。

最佳方案由后端按可比协议下的配对测量维护，不能由模型凭叙述改写。
不同划分、数据、指标、对照或执行预算不得混排；没有稳定增益时保留原方案。
失败试验是知识，不必成为下一轮代码基础。高分或换划分后降分只能触发泄漏假设，
不能单独证明泄漏。搜索所得最佳结果仍需独立确认；重复使用验证数据不是独立检验。

## 输出语言

当研究问题为中文时，所有面向用户的叙述字段必须使用简体中文，包括原因分析、知识缺口、查询原因、候选方向、选择理由和下一步动作。仅机器枚举、JSON 字段名、错误码、原始指标键和英文学术检索式保留英文。
## Step 6 scientific evolution contract

After a runtime-validated result, distinguish scientific interpretation from code
repair. Scientific statuses are exactly `SUPPORTED`, `CONTRADICTED`,
`INCONCLUSIVE`, and `REFINEMENT_REQUIRED`. A result that is merely unsupported must
never trigger training-code repair. Preserve the initial/user hypothesis Artifact;
any working revision must be a new lineage node with `parent_hypothesis_id`,
`derived_from`, and `revision_reason` grounded in a validated result and evidence.

When the failed claim changes the intervention, mechanism, placement, or target
comparison, declare a `PIVOT` rather than editing the parent hypothesis in place.
The PIVOT must state one exact new claim, its parent hypothesis/result IDs, and a
minimal `change_set`: what is inherited (verified loader, split, baseline,
controls, metrics) and what must change in implementation. Historical parent
results are read-only evidence; they are not a current experimental arm unless
the new claim explicitly requires rerunning that comparator.

For an adverse or null result, emit `CONTRADICTED` or `INCONCLUSIVE` as appropriate;
never emit `FAILED`, `UNSUPPORTED`, or any other status synonym.
