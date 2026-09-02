# IPIX 试运行：失败诊断与优化改动回退记录

日期：2026-08-27。当前状态：已按用户要求撤回本次“迭代优化”相关代码、界面、运行时 Skill 及测试改动，恢复原来的 supported → REPORT 规则。失败 run 和请求文件保留为历史证据；没有重跑，也没有真实实验结果。

## 当前 run 的失败原因

Run `run_485312383332` 的问题理解、数据预检和文献整合已完成（40 条文献记录），失败在 `hypothesis_generation`；没有进入实验、反馈或续轮决策。

1. `backend/app/providers/llm.py` 为 `hypothesis.generate` 硬编码 `max_tokens=4000`；`_max_tokens_payload` 优先使用任务级上限。诊断调用验证：全局值设为 0 或 16000，该任务的有效上限均为 4000。
2. 该任务需生成 3–5 个候选，每个包含多个论证和证据字段。本次 `qwen3.7-plus` 响应被截断；根据报错分支可确定 `finish_reason=length`，已返回 10023 个字符（字符数不是 token 数）。系统拒绝把不完整输出当作有效候选。
3. `failure_state_for` 未将 `QWEN_OUTPUT_TRUNCATED` 匹配为可恢复的模型错误，分类结果为 `FAILED_SYSTEM`，因此停止且未自动重试。其报错建议“增大 QWEN_MAX_TOKENS”忽略了任务级覆盖，单改全局值无效。

直接原因是模型输出长度限制，停止方式则由错误分类决定。刚才新增的 supported 续轮逻辑尚未执行，不能把本次失败归因于该逻辑。输入问题的复杂程度可能影响输出长度，但尚未作对照实验，不将它断言为唯一原因。

按用户要求仅诊断并回退，未修复任务级上限或错误分类，也未修改模型配置。

## 回退范围与可恢复性

精确撤回17个文件中的本次改动，包括模式字段与校验、反馈分支、条件技能加载、两份 Skill 文本、前端模式入口及相关测试。README、engine、research_constraints 及相关测试中的原有数据集、训练预算、种子和报告改动保留。另23个未纳入回退的已修改文件 SHA256 全部一致。

本次撤回的补丁保存在 `D:\Gewu\tmp\optimization-mode-20260827.reapply.patch`，`git apply --check` 已通过，仅作为可恢复备份，不自动重新应用。研究数据库和失败记录不删除、不改写；历史请求及冻结 Artifact 中的 optimization 字段只保留为当时配置证据，已回退的程序不再赋予它特殊续轮行为。

回退后的后端回归：`test_workflow_engine`、`test_workflow_orchestrator`、`test_research_constraints`、`test_skill_runtime` 共141项通过。前端构建通过；两份 Skill 校验通过。前端测试恢复到原始测试状态：32 passed、4 failed（两项旧知识库字段/接口断言，两项数据目录标签断言），没有为全绿而重新修改用户要求回退的测试。

后端已于17:32重启加载回退代码（PID 8632），运行时与磁盘 workflow_hash 一致：`c1061ad216b4dcc20fcd2e85ee17f13d7a2b5ed5ac24be478575e59555a85aaa`。研究数据库重启前后 SHA256 均为 `F9C012D631728195ADE51487F1B1C607542505F0CEDD5318D80DABE493260AA7`，确认没有改写研究历史。当前 run 仍为 FAILED_SYSTEM / hypothesis_generation，实验结果0。

## 回退后的独立调整：候选输出上限

用户随后要求适当提高输出上限。仅将 `hypothesis.generate` 的任务级上限从4000提高到8000 tokens，其他任务上限、思考开关、错误分类和 supported 后收尾规则不变。对应的请求载荷/上限/截断相关测试7项通过。

首次重启命令被环境策略拒绝，未执行。用户随后明确要求“重启后端”，已于17:46成功重启（新服务PID 4052），8000端口正常响应，加载8000 tokens的新上限。失败 run 未自动重跑，研究数据库哈希不变。该调整不是恢复已撤回的优化模式。

## 以下为回退前的历史验证记录

下述优化功能说明、模拟测试数字和服务启动记录均为撤回前的历史情况，不代表当前代码仍启用这些功能。

## 设计与改动

- 新研究显式选择 `research_constraints.research_mode`：`hypothesis_validation`（默认，兼容历史研究）或 `optimization`。创建 API 拒绝非法模式；预检后的约束快照决定实际模式，不能通过修改原始字段改变已冻结的模式。
- 验证模式保留“supported 后收尾”的原规则。优化模式允许 `supported + REVISE`；科学判断不为延长研究而降级。
- Critic 接收研究目标、当前轮次、剩余预算及已有结果指标。继续迭代必须具备具体修订，并从有实测依据的候选方向中选出改动变量、固定条件、目标指标、成功/失败/停止规则。
- 沿用现有计划差异检查、独立审查、冻结约束与预算上限。无实质计划改动、无合法后续方向、模型选择 REPORT 或预算耗尽时停止，不强制最低轮数。
- 两份运行时 Skill（`experiment-iteration`、`result-to-claim`）同步调整；仅允许继续时加载修订/消融技能，避免技能文本仍要求 supported 无条件停止。
- 前端增加模式选择，加载历史研究时回显；已创建研究禁用模式切换，模式不同的草稿不能误复用现有研究。

主要实现：`backend/app/workflow/engine.py`、`research_constraints.py`、`skills.py`、`backend/app/agents/critic.py`、`backend/app/api/runs.py`，以及前端 App / WorkbenchPage / ResearchPage。

这些改动解除一轮正结果后的强制停止，并强化续轮条件；并不保证模型一定提出合法的下一轮，也不保证真实指标逐轮提升。原始基线可比性、预测文件、测试隔离仍须在真实计划、执行代码和结果产物中逐项核验，不能仅凭提示词宣称已经落实。

## 验证

针对性后端测试：18 passed，152 deselected。包括三轮均保留 supported、前两轮生成修订、第三轮达到预算后收尾，以及旧模式不续轮、优化模式提前收尾、无实测依据的方向被拒绝、冻结模式不可被原字段覆盖、API 模式校验及技能去重。

```powershell
.venv\Scripts\python.exe -m pytest tests/backend/test_research_constraints.py tests/backend/test_skill_runtime.py tests/backend/test_workflow_engine.py tests/backend/test_api.py -q -k 'optimization or research_mode or passed_feedback or runtime_loads_conditional'
```

三轮用例使用 `MockExperimentProvider`，验证的是编排和产物链，不是 IPIX 实验成绩。

扩大后端回归：251 passed，6 failed。失败为两项报告测试缺少训练 epoch、在线文献返回缺少旧测试期待的 claim、运行时版本 v1/v2 不一致、报告技能列表新增 paper-write 与旧断言不一致、计划 schema 的 seeds 字段与旧断言不一致。本次没有为消除这些失败修改对应规则；整体回归不能标记为全绿。

前端构建通过。UI 契约测试 35 passed，2 failed，均为数据目录标签的旧断言 `/数据集父目录/` 与现有“数据集目录（具体目录或父目录）”不一致。浏览器实测新建时可切换模式；从真实 API 读取已保存的 IPIX 草稿后，正确显示“迭代优化”且选择框禁用。

两份 Skill 通过 UTF-8 模式的 quick_validate；项目 SkillLoader 加载成功。`git diff --check` 无空白错误。

## 真实试验

- 名称：IPIX17：低虚警目标检测的多轮诊断与优化。
- Run ID：`run_485312383332`。
- 最终 API 状态：`FAILED_SYSTEM`，`automatic=false`，失败阶段 `hypothesis_generation`；反馈轮数0，真实实验结果0。
- 预检：所有配置模型、数据和本地执行环境均通过。冻结约束 `art_2451722ff10f` 的模式为 `optimization`。
- 请求文件：`D:\Gewu\experiments\ipix_iterative_20260827\request.json`（本地实验目录，未假定它会被 Git 跟踪）。
- 数据：本地 IPIX17，clu 20410×512、mubiao 2041×512 复数样本；完整数据，不下采样。
- 固定种子：101/202/303；每种子固定 60/20/20 分层划分并全轮次复用。
- 主指标：验证集标准化 partial ROC-AUC，max_fpr=0.01；最多4轮，无最低轮数。测试集在配置冻结后单独复核，未完成时如实标记。
- 每轮要求：上一轮证据 → 单一主要改动 → 与原始基线及上一轮保留方案比较 → 保留/拒绝理由。负结果和退步也保留。

## 服务恢复记录

首次尝试时，8000 端口后端没有热重载，仍是改动前加载的代码。环境策略拒绝了服务重启及临时服务启动，两次命令均未执行。没有向旧后端提交 pipeline/start，避免草稿被旧规则冻结成验证模式。

随后用户明确授权“你可以重启”。确认没有正在运行的研究后，已于 2026-08-27 17:05 成功重启后端（新服务 PID 25216），历史研究和同一 IPIX 草稿保留。运行中的 workflow_hash 与磁盘一致：`a89fb0abdee29fc8cf6489fddcce69c134103e15cfdb0f13ebe5b6615d790808`。使用的后端启动入口为：

```powershell
.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

回退前曾通过 pipeline/start 启动上述研究，核对当时冻结模式为 optimization。随后假设生成输出截断，流程停止；未运行到 supported + REVISE 分支，也未获得多轮累计收益。用户要求回退后，不再继续本次优化试验。

未绕过人工假设选择、独立审查或任何冻结约束；未生成或篡改实验指标。
