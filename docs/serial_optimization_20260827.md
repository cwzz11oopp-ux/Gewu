# 串行实验优化闭环（2026-08-27）

## 范围

本次在现有流程上增加串行保优机制，不恢复之前回退的 UI 优化模式，不引入
并行代理、树搜索或数据库迁移。没有启动付费模型调用、训练新实验或改写历史 run。

## 新行为

1. 问题理解返回 `research_intent.kind`、原文 `goal_quote` 和理由。
   普通改善型问题可被识别为 `optimization`；验证指定主张、缺失或不明确的目标
   按 `verification` 处理。后端检查引用来自用户原文，并保存一次性、锁定且校验摘要的 `iteration_policy`。
2. 验证型和没有该策略的旧 run 保留原先 supported 后结束的路由。
   优化型保留真实 verdict，在综合两路科学分析后由方向选择决定 REPORT/REVISE/PIVOT。
   REPORT 可在任何一轮发生，没有最低轮数。
3. `serial_iteration.py` 从 result → bundle → task → plan 血缘重建实验记忆。
   每项记录包含执行方法、参数、指标、诊断、精确版本和保留/淘汰原因。
   决策提示只带最近八项摘要，不附全部历史代码；完整记录仍保存在 artifacts 中。
4. 只有真实、审计通过、种子完整配对、身份和协议齐全的结果参与保优。
   模拟结果、工程失败、缺失配对、非有限值和 Smoke 不参与排名。
   同一任务的重新分析不增加科学轮数。
5. 数据指纹、划分、预处理、指标方向、种子、预算和对照等协议维度不同则分组，
   不生成跨协议总排名。相同协议却出现基线漂移时要求重新验证。
   新候选与 incumbent 的逐种子差异须满足现有 `positive_stable` 统计规则才能替换它；
   原始均值略高但证据不足也保留 incumbent。最佳候选不等于已胜过基线。
6. 后续计划以保留的最佳计划为修改基础，最新失败实验仍作为观察输入。
   `implementation_reference` 绑定 plan/task/bundle/result ID 和 Bundle 摘要；执行时核对
   血缘、摘要和文件哈希。计划审核不得替换或丢弃该引用。
   代码模型返回局部 old/new 修改，后端应用于指定 train.py；修改不匹配时有界重试，
   不退回无依据的整文件生成。有效修改后的工程错误仍可走原有修复流程。
   如果运行参数改变且代码已经支持读取它们，允许空代码修改。
7. 方向必须引用真实历史 result ID，提供观察依据、问题、改变变量、固定控制、
   指标及成败/停止规则，并来自比较过的候选。精确重复的计划不再执行。
8. 默认最多四轮，连续两轮没有稳定保优进展即停止；换协议本身不算进展。
   不可执行方向、版本问题和预算边界均不能由模型的自然语言绕过。
9. 保存 `optimization_state`；报告事实包区分 latest result 与 best candidate，
   明确标记 `independent_confirmation_required`。

## 边界

- 这不是收益保证，也不是已经完成独立确认的科研结论。当前版本没有自动创造
  缺失的独立测试集、组别元数据，或突破冻结种子/训练预算来追加确认实验。
  反复使用同一验证数据存在选择偏差，最终结果需要另行独立复核。
- 问题意图、科学方向和因果解释仍依赖模型；确定性检查不能证明模型解释正确。
- 保优依赖执行协议和受审计指标；代码局部修改不等于自动证明所有实验控制完全相同。
- 精确计划指纹可拦截同配置重试，但不是语义上所有等价实验的判定器。
- 需要后端加载新代码后，新建 run 才会冻结新策略。不会静默迁移旧 run 的研究目标。

## 验证

新增测试覆盖目标冻结、supported 后续轮/主动停止/预算结束、精确结果引用、
重复反馈幂等、保优与退化、最小化指标、不确定增益、跨协议隔离、基线漂移、
缺失/非法测量、代码快照与哈希、局部修改及重试、无意义修改避免、报告证据边界。

串行集成测试模拟 A → B 退化 → 从 A 生成 C，核对规划输入、代码输入与执行任务引用，
而不是仅检查提示词中出现“最佳方案”。测试中的指标是合成夹具，不是科研成果。

历史 IPIX `run_eacb97f39067` 只读内存回放得到四项结果、三个可比协议组；其中一项
因无法完整配对而不参与保优。未向该 run 写入新策略或状态。

扩展回归发现以下既有测试不一致，未修改无关逻辑来消除它们：

- `test_planning_agent_requests_the_stable_chinese_blueprint_fields` 仍要求模型返回后端拥有的 seeds。
- `test_skill_loader_loads_structured_policy_and_engine_has_no_blocker_class_copy`：既有 engine 中存在 CLAIM_PLAN_MISMATCH 字符串。
- `test_legacy_final_plan_must_migrate_and_pass_governance_before_experiment`：旧夹具缺少现有代码要求的训练预算。
- `test_migration_payload_corruption_fails_closed_before_llm_side_effect`：同样先被缺失训练预算拦截。
- `test_universal_scientific_stability.py` 中的三个旧夹具同样缺少训练预算：
  `test_review_exhaustion_is_recoverable_and_preserves_append_only_lineage`、
  `test_provider_review_failure_is_recoverable_and_checkpoint_is_preserved`、
  `test_plan_review_resume_reuses_the_existing_candidate_before_acceptance`。

上述训练预算门槛和 CLAIM_PLAN_MISMATCH 字符串在修改前的 HEAD 中已经存在；
本次保留这些门槛和既有测试，单独记录其失败。

本次测试记录（各组有重叠，不相加）：

- 全部新增串行优化用例：27 passed。
- 核心 engine/orchestrator/research_state 与串行模块：159 passed。
- 扩展相关回归：317 passed，4 deselected；四项先单独确认失败后排除，名单见上。
- 额外 scientific_stability/integrity/runtime 检查：22 passed，3 failed，原因见上。
- 新增 supported → PIVOT 用例补测：对应续轮参数化组 4 passed。
- `git diff --check` 通过。
