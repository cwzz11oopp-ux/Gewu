# AI Scientist V1 当前真实架构

> Phase 0 架构基线。审计对象为 `v2-rearchitecture` 分支创建时的代码（基线提交 `e75947d`）以及工作区中已存在但尚未提交的环境安装改动。本文以代码和测试为准，不以 README 的自述替代实现事实。

## 1. 审计结论

当前系统是一个以 Artifact 是否存在为主要推进条件、带最多四轮反馈的九步科研 Pipeline。真正的控制核心不是 Agent 自主搜索，而是：

```text
FastAPI API
  -> WorkflowOrchestrator._drive()
  -> WorkflowOrchestrator._next_step()
  -> static ORDER
  -> WorkflowEngine.run_step()
  -> fixed Agent + fixed Skill route
  -> Artifact append
  -> next missing Artifact
```

因此 V1 的准确抽象是 `Pipeline + bounded feedback loop`。它已经具备较强的执行安全、实验结果审计、制品谱系、失败恢复和报告事实约束，但尚不具备多研究分支、基于信息增益的动作选择、代码仓库内实验工作区、基线复现、科研预算策略或真正的 Research Frontier。

## 2. 运行时 Architecture Map

```text
React/Vite Workbench
  | REST + polling
  v
FastAPI create_app()
  |-- /api/runs                 run/step/pipeline/hypothesis/feedback
  |-- /api/literature           upload/verify/attach/wiki
  |-- /api/settings             providers/runtime configuration
  |-- /api/runs/.../report      report generation/export
  `-- /api/runs/.../paper-writing
          |
          v
Dependencies container
  |-- Repository (runs.json)
  |-- RuntimeConfigStore
  |-- LiteratureLibrary / ResearchWikiStore
  |-- QwenLLMProvider or MockLLMProvider
  |-- ArxivSemanticScholarProvider or mock literature provider
  |-- LocalGpu / RemoteGpu / Mock experiment provider
  |-- SupervisorAgent + ReviewerAgent
  |-- WorkflowEngine
  |-- WorkflowOrchestrator
  `-- PaperWritingManager

WorkflowOrchestrator
  -> checks latest Artifact by type
  -> chooses one of nine fixed steps
  -> pauses for mandatory hypothesis selection
  -> repeats experiment/feedback up to configured bound
  -> marks completed/failed/paused and supports restart recovery

WorkflowEngine
  -> prepares static SkillRuntime package
  -> invokes fixed domain Agent
  -> deterministic validation
  -> optional independent Reviewer validation
  -> appends Artifact and Event records
```

## 3. 九步主流程的真实控制方式

`backend/app/workflow/steps.py` 定义固定 `ORDER`：

```text
problem_understanding
knowledge_integration
hypothesis_generation
evidence_reasoning
research_plan
experiment_task
experiment_run_analysis
feedback_revision
report_export
```

`backend/app/workflow/orchestrator.py` 的 `_next_step()` 根据最新 Artifact 类型是否存在选择下一步。它在 `reasoning` 产生后、`hypothesis_selection` 缺失时强制暂停；实验反馈可产生子 plan、task 和 result，但仍沿固定阶段前进。它没有表达并行或竞争研究分支，也不计算信息增益、预期提升、成本、风险或不确定性。

`backend/app/workflow/engine.py` 是 2764 行的集中式实现，承担步骤分派、Agent 调用、Skill 授权、候选修订、实验生成与修复、结果分析、审计、反馈迭代、报告准备、重跑和事件追踪。它既是应用服务，又包含大量领域规则，是迁移时最大的耦合点。

## 4. 当前领域数据与状态

### 4.1 Run 与 Artifact

- `RunRecord` 保存主题、约束、Pipeline 状态、九个 `StepRecord`、Artifacts、Events、反馈轮次和 paper-writing 状态。
- `Artifact` 只有一个 `parent_artifact_id`，谱系是单父指针；部分代码递归查找后代，因此已经把它当作树使用，但不能原生表达多父 DAG。
- Artifact 的 `type` 和 `content` 是字符串加自由字典，领域约束大多散落在 Engine、Agent 和测试中。
- `Repository` 把全部 Run 序列化到 `runs.json`，以进程内 `RLock` 保证同进程写入串行化。

### 4.2 名为 Research State 的现有实现

`backend/app/workflow/research_state.py` 的 `build_research_state()`：

- 汇总 Artifact 生命周期、当前版本和父子关系；
- 判定 verified/unverified/conflicted/superseded；
- 解析计划参数与实际执行参数冲突；
- 提取 canonical hypothesis、parameters、metrics 和 terminal verdict；
- 为 Writer 和 report export 提供可信事实快照。

该模块回答的是“过去产生了哪些制品，哪些事实可用”，而不是“当前科研问题、信念、Frontier、预算和下一动作是什么”。其真实职责应在 V2 中迁移为 `EvidenceLedger`，不能直接把现有函数重命名为新的 `ResearchState`。

## 5. Agent 与 SkillRuntime

### 5.1 实际 Agent 集合

应用代码包含 Research、Idea、IdeaSelection、Planning、Experiment、Diagnostic、Critic、Reviewer、Writer 和 Supervisor。`HypothesisAgent` 是 IdeaAgent 的兼容别名。

`SupervisorAgent` 不是开放式科研决策者。它根据 `SkillRegistry` 的静态映射完成委派、确定性校验、修订次数控制和 Reviewer 调用。`WorkflowEngine` 仍掌握流程与状态变更。

### 5.2 Skill 机制

- `SkillLoader` 读取一级 `skills/<skill-id>/SKILL.md`，校验路径并计算完整指令哈希。
- `SkillRegistry` 静态绑定 Pipeline step、Agent 和 Skills。
- `SkillRuntime` 对 Skill 声明工具、Agent policy、后端注册工具和配置工具取交集。
- 原子 Skill 指令在具体操作前加载，Provider 调用仍由应用代码发起。

该安全边界和 Prompt/Protocol 能力可保留；静态 Skill 路由不能继续作为 V2 科研动作选择器。

## 6. 模型 Provider

### 6.1 LLM

`backend/app/providers/llm.py` 只有统一 `LLMProvider.generate_json()` 协议、开发用 Mock 和 Qwen 实现。Qwen 内部已有按任务选择模型、取消请求、JSON 修复、输出归一化和调用元数据能力，但没有跨 Qwen/DeepSeek/GPT 的 `ModelRegistry` 与能力路由。

### 6.2 文献

当前外部实现是 `ArxivSemanticScholarProvider`：搜索以 arXiv 为主，Semantic Scholar 用于标识验证。`KnowledgeIntegrationService` 合并 Research Wiki、本地文献和外部结果。数据模型仍是扁平 `EvidenceCard`，没有 PaperCard、段落/位置级 EvidenceUnit、证据关系图、全文获取状态或混合检索索引。

本地 LiteratureLibrary 支持 PDF/TXT/Markdown 上传、哈希去重、元数据和验证；ResearchWikiStore 支持节点存储与简单检索。它们是 V2 文献基础设施的可复用起点，但不是目标 LiteratureGraph。

## 7. 实验系统

### 7.1 当前主模式

当前 ExperimentAgent 由 LLM 生成独立 `ExperimentBundle`，核心内容是 manifest、若干代码文件、requirements 和通常作为入口的 `train.py`。LocalGpu 和 RemoteGpu Provider 将 Bundle 部署到运行目录，执行后从 manifest 指定的 JSON 文件读取结果。

### 7.2 已有强项

- manifest 对 run/experiment/result ID、入口、数据集契约、参数、seeds、预期 metrics 有确定性约束；
- 文件路径和 SHA-256 校验；
- GPU preflight，拒绝 GPU 必需实验静默降级；
- 本地子进程和通用 SSH 两类真实执行；
- 结果文件权威性、数值与有限性校验；
- attempt、环境、日志、运行状态和恢复；
- 缺依赖仅诊断，不擅自安装；
- 有界的已知数据下载修复与生成代码修复；
- ExperimentAudit 决定 `is_real_experiment`，下游不能用 stdout 冒充结果。

### 7.3 与 V2 的差距

它不打开用户提供的既有 ML 仓库，不创建 Git worktree，不记录 base/code commit 和 diff，也没有 baseline reproduction、静态代码验证、仓库内修改、相对基线比较和研究变体提交。因此只能保留为 `Synthetic / Standalone Experiment Mode`，不能继续作为 Repository Research Mode 的主实验抽象。

## 8. 存储、恢复与审计

- `JsonStore` 提供 JSON 文件读写；`Repository` 保存 Run/Artifact/Event。
- Orchestrator 在进程重启时把运行中步骤标为 interrupted 并恢复自动 Run。
- Local experiment provider 维护运行状态、PID/锁、日志与已完成结果恢复。
- `evidence_audit.py`、`research_state.py`、ExperimentAudit 和 Writer fact audit 共同限制不可验证证据与虚构指标进入报告。
- 当前存储适合单机原型；没有数据库事务、Graph checkpoint store、跨进程调度锁或面向 Frontier/Experiment Registry 的查询模型。

## 9. 报告与论文写作

存在两条相关但不同的路径：

1. `report_export`：WriterAgent 基于经审计 Artifacts 生成结构化 report，支持 HTML、DOCX 和 ZIP。
2. 可选 `PaperWritingManager`：在 report 后执行计划、章节写作、审计、修订，并导出 DOCX 与 LaTeX package。

可复用能力包括事实表、验证引用过滤、指标/配置一致性检查、独立审查、Word/LaTeX 导出和停止/恢复。当前不足是输入仍以“最新 Artifact 集合”为中心，没有显式 Claim-Evidence Graph，也没有 Claim -> Evidence -> Experiment -> Config -> Commit 的完整追溯链。

## 10. 前端现状

React/Vite 前端以 `WorkbenchPage` 为中心：研究主题、EvidenceTable、九步 `PipelineTimeline`、HypothesisBoard、ArtifactEditor、ExperimentPanel、ReportPreview、PaperWritingPanel 和 AgentTrace 同屏展示。

前端通过轮询 Run 与 ExperimentProgress 跟踪后端；类型系统直接暴露 `steps`、`artifacts`、`events` 和 `parent_artifact_id`。UI 与 V1 Pipeline、强制 hypothesis selection 和 Artifact 类型高度耦合。按要求 Phase 0–9 不改前端；V2 后端稳定后再把主页改为 Research State/Frontier/Current Action/Belief/Budget 视图。

## 11. 现有测试与当前基线

2026-08-11 在当前工作区实测：

- `python -m pytest tests/backend -q`：413 passed，3 skipped；
- `node --test frontend/tests/ui-contract.test.mjs`：32 passed；
- `pnpm --dir frontend run build`：成功。

测试最密集处是 `test_workflow_engine.py`（62 个测试）和 API、实验代码/Provider。它们既提供重要回归保护，也证明大量规则耦合在 V1 Engine。

迁移期间必须持续保留的测试资产：

- Artifact/Repository、研究事实账本与 evidence audit；
- ExperimentBundle、experiment runtime、Local/SSH provider、dataset inspection/provisioning；
- LLM 输出契约、SkillLoader/SkillRuntime 工具授权；
- report export、paper writing 和 Reviewer 独立性；
- API、停止/恢复、失败诊断和端到端开发模式；
- V2 并存期内全部 WorkflowEngine/Orchestrator 与前端契约测试。

可在替换完成后退役而非立即删除的测试：

- 静态 `ORDER` 的精确顺序断言；
- `WorkflowOrchestrator._next_step()` 的“缺哪个 Artifact 就跑哪一步”断言；
- 强制等待用户选择 hypothesis 的断言；
- Supervisor 静态 Skill 分配作为主路由的断言；
- 前端 PipelineTimeline 作为主视图的结构断言。

## 12. README 与代码的一致性判断

README 对 V1 的自述大体准确：Qwen、Supervisor、九步 Pipeline、SkillRuntime、Local/SSH GPU、Artifact 和报告审计确实存在。需要避免的误读是：

- “Supervisor 调度”不等于动态科研决策；实际下一步由 Orchestrator 的固定规则决定。
- “反馈循环”不是 Research Search；它只在同一主路径上有界地产生后续 plan/task/result。
- “Experiment”主要是生成并执行 Bundle，不是修改既有仓库。
- “Research State”是可信事实账本快照，不是 V2 所需科研状态。
- “文献研究”尚未形成多源全文、EvidenceUnit 与 Evidence Graph。

## 13. Phase 0 分类

| 分类 | 当前组件 | 决策 |
| --- | --- | --- |
| KEEP | Artifact lineage、manifest、metric authority、dataset inspection、Local/SSH execution、run recovery、fact audit、Word/LaTeX export | 保留行为并用回归测试冻结 |
| REFACTOR | research_state、Research/Idea/Experiment/Literature/Writer、LLM provider、Repository storage | 将领域职责拆到 V2 模块，旧入口先适配 |
| DEPRECATE | WorkflowEngine、WorkflowOrchestrator、static ORDER、Supervisor static routing、mandatory hypothesis selection | 新旧并存；V2 integration tests 通过后才退出主路径 |
| DELETE | 本 Phase 无 | 不在 Phase 0 或早期迁移中删除旧实现 |
| NEW | ResearchState、EvidenceLedger、ResearchFrontier、ResearchController、RepositoryWorkspace、ExperimentGraph、LiteratureGraph、ModelRouter、Claim-Evidence Graph、PaperGraph | 按阶段增加，禁止一次性铺空目录 |

## 14. 已识别迁移风险

1. `WorkflowEngine` 规则密集，直接拆除会同时破坏验证、重跑、诊断和报告门禁。
2. Artifact 单父谱系被多个查询依赖，升级 `parent_ids[]` 需要兼容读取和数据迁移。
3. V1 Run/Step API 与前端紧耦合，过早换 UI 会掩盖后端语义错误。
4. LangGraph 尚未出现在依赖中，必须在实际图实现阶段引入并锁定版本，Phase 0 不添加。
5. Git worktree 与实验执行会扩大安全边界，必须先定义受控命令和路径约束。
6. 文献全文可得性必须显式建模，不能用摘要补写不可访问内容。
7. 当前工作区已有独立的 GPU 依赖安装改动；V2 commits 必须选择性暂存，避免混入。
