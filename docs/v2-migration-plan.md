# AI Scientist V1 -> V2 分阶段迁移计划

> Phase 0 交付物。本计划描述后续工作，不授权本轮实现 Phase 1 或更后阶段。

## 1. 迁移策略

采用 strangler-style migration：先在 V1 旁边建立 V2 领域模型和图入口，以适配器复用可靠基础设施；每阶段都必须可运行、可测试、可回退。只有 V2 integration tests 覆盖同等安全不变量后，才把旧路径从主路由移除。

2026-08-11 至 2026-08-13 的 Beta Sprint 改用 Vertical Slice + End-to-End Integration 顺序：先闭合 `State -> Decision -> Experiment -> Evidence -> State Update -> New Decision`，再补齐各子系统。该调整只改变实现顺序，不改变本文的目标模块边界；尤其不得向 V1 `WorkflowEngine` 添加 V2 控制逻辑，也不得退化为另一条静态 Pipeline。

本轮明确不做：V2 Python 模型、LangGraph 依赖、数据库迁移、Git worktree 执行、Provider 扩展、前端改造、旧代码删除。

## 2. 迁移分类

### KEEP

- `models/experiment.py` 的 Bundle、Manifest、file hash 和 result identity 约束；
- `providers/experiment_runtime.py` 的结果文件验证和 CUDA probe；
- `providers/experiment.py` 的 Local GPU、SSH、取消、日志、恢复和 dataset provisioning；
- `workflow/dataset_*` 的数据集契约和检查；
- `workflow/evidence_audit.py` 与 Writer fact audit；
- `storage/literature.py`、`storage/research_wiki.py` 的本地资产能力；
- SkillLoader/SkillRuntime 的版本、哈希和最小工具权限机制；
- report、DOCX、LaTeX、ZIP 导出；
- Run interruption/recovery 与原子 JSON 写入的现有行为。

KEEP 表示冻结行为，不表示文件永远不移动。

### REFACTOR

- `workflow/research_state.py` -> `EvidenceLedger`；
- Research/Idea/Experiment/Critic/Writer 的输入输出改为 V2 contracts；
- literature provider -> multi-source search + PaperCard/EvidenceUnit；
- LLM provider -> ModelRegistry/ModelRouter；
- Repository -> project/research state/frontier/experiment/evidence stores；
- paper writing -> PaperGraph + Claim-Evidence Graph；
- Artifact lineage -> multi-parent DAG。

### DEPRECATE

- `WorkflowEngine` 作为科研主控制器；
- `WorkflowOrchestrator._next_step()`；
- `workflow/steps.py::ORDER`；
- Supervisor static routing 作为顶层路线选择；
- mandatory hypothesis selection；
- Pipeline-step-driven API 和 UI 主叙事。

Deprecated 组件在 Phase 7 前继续可用，且不得在旧文件中新增 V2 决策逻辑。

### DELETE

Phase 0–7 不删除上述旧组件。删除门槛：V2 end-to-end scenario、恢复/中断、实验审计、报告事实门禁和数据迁移测试全部通过；同时保留一个版本周期的兼容读取。具体删除清单应在达到门槛时由代码引用图重新生成。

### NEW

按阶段创建 Research domain、controller policies、LangGraph graphs、repository workspace、experiment registry、literature evidence graph、model router、claim graph 和新前端。禁止提前创建未使用的空模块。

## 3. 全阶段质量门禁

每个 Phase 必须：

1. 运行新增单元测试；
2. 运行相关 V1 回归测试；
3. 运行全量 `tests/backend`；
4. 若 API contract 有变化，运行前端 `ui-contract.test.mjs` 和生产 build；
5. 记录架构决策、兼容策略和可回滚点；
6. 使用小而明确的 scope commit；
7. 不因测试方便伪造科研行为、论文、数据、指标或引用；
8. 不将 CIFAR/MNIST 硬编码成最终领域抽象；
9. 对外部文献标记实际获取层级；
10. 对每个实验保存可重现 provenance。

Phase 0 基线（2026-08-11）：后端 413 passed/3 skipped，前端契约 32 passed，前端 build 成功。

## 4. Phase 0 - Architecture Baseline（本轮）

交付：

- `docs/v2-current-architecture.md`；
- `docs/v2-architecture.md`；
- `docs/v2-migration-plan.md`。

验收：三份文档与实际代码一致；包含 Architecture Map、目标边界、KEEP/REFACTOR/DEPRECATE/DELETE/NEW、现有测试处置和 Phase 1 文件级计划；没有业务代码变化。

## 5. Phase 1 - Research Domain Model

### 5.1 目标

建立独立、可序列化、与 FastAPI/Provider/Git/LangGraph 解耦的领域层。只定义状态、不接管 V1 运行路径、不改前端。

### 5.2 具体文件级修改

仅在开始 Phase 1 后创建以下实际需要文件：

| 文件 | 修改 |
| --- | --- |
| `backend/app/research/__init__.py` | 导出稳定的领域公共 API，避免调用方依赖内部文件布局 |
| `backend/app/research/actions.py` | 定义 `ResearchOperator` enum、`ResearchAction`、动作前置条件和预算估计字段 |
| `backend/app/research/branch.py` | 定义 `BranchStatus`、`ResearchBranch`、合法状态迁移与 hypothesis/机制/证伪字段 |
| `backend/app/research/frontier.py` | 定义 `ResearchFrontier`，实现添加分支、父子校验、best-first 选择、状态过滤和稳定 tie-break |
| `backend/app/research/belief.py` | 定义 `BeliefState` 与 belief item；保存支持/反对证据、不确定性和更新时间 |
| `backend/app/research/budget.py` | 定义 `BudgetState`、资源维度、reserve/consume/release 和不可超支不变量 |
| `backend/app/research/evidence.py` | 定义 `EvidenceUnit`、EvidenceRelation、获取层级与来源定位；不实现联网检索 |
| `backend/app/research/experiment.py` | 定义 V2 `ExperimentRecord` provenance 模型；复用而不替代 V1 ExperimentBundle |
| `backend/app/state/research.py` | 定义聚合 `ResearchState`，引用 problem/baseline/frontier/beliefs/budget/ledger，不承载 Artifact history |
| `backend/app/state/__init__.py` | 导出 state API |
| `tests/backend/test_v2_research_actions.py` | operator、action 必填字段、branch 绑定和成本校验 |
| `tests/backend/test_v2_research_branch.py` | Branch 字段、状态迁移、父子关系和不可证伪 proposal 拒绝 |
| `tests/backend/test_v2_research_frontier.py` | priority 分解、best-first、tie-break、过滤与重复分支规则 |
| `tests/backend/test_v2_budget.py` | 预算 reserve/consume/release、幂等和超支失败 |
| `tests/backend/test_v2_evidence.py` | abstract_only/full_text、location、关系类型和强度约束 |
| `tests/backend/test_v2_experiment_record.py` | commit/diff/config/metrics/environment/provenance 完整性 |
| `tests/backend/test_v2_research_state.py` | ResearchState 与 ledger/history 分离、序列化和引用一致性 |

### 5.3 Phase 1 设计约束

- Pydantic v2 与当前依赖一致；禁止为了领域模型先引入 LangGraph。
- priority 分数由显式分量计算，禁止只接受模型给出的不可解释总分。
- 时间、ID 和浮点边界有确定性验证。
- `ResearchState` 不导入 `backend.app.workflow.research_state`。
- 暂不修改 `RunRecord`、Artifact schema、Repository 或 API。
- 暂不创建 Controller、Graph、Workspace 或 ModelRouter 空壳。
- `ExperimentProtocol` 必须生成确定性 fingerprint；baseline/variant 的 task、dataset identity/version、split、preprocessing、metrics、training budget、evaluation、seed policy 和关键训练控制不兼容时，统一返回 `COMPARISON_NOT_ALLOWED`。
- 直接 improvement claim 必须同时满足 protocol compatible 和 experiment audit passed；不兼容结果仍作为 Observation 保存，但不得进入直接优越性结论。

### 5.4 Phase 1 必须继续通过的旧测试

除所有新增测试外，至少重点运行：

```text
test_research_state.py
test_repository.py
test_evidence_audit.py
test_experiment_bundle.py
test_experiment_runtime.py
test_workflow_engine.py
test_workflow_orchestrator.py
test_api.py
```

随后运行全量后端、前端契约和 build，证明新增领域层未改变 V1。

## 6. Phase 2 - Repository Workspace

实现 `workspace/repository.py`、`git.py`、`worktree.py`、`code_index.py` 和受控 command runner。先用临时 Git repositories 测试：打开仓库、检查、创建 branch worktree、记录 dirty state、运行允许命令、收集 diff、提交实验、验证路径边界和安全清理。

不得让 LLM 直接拼接任意 shell。高层操作生成结构化命令计划，经 allowlist、cwd 和路径校验后执行。用户仓库初始 dirty 时默认拒绝创建不明确的实验基线或要求显式策略，不能偷偷包含未提交内容。

## 7. Phase 3 - ExperimentGraph

引入 LangGraph 的首个实际子图和 checkpoint store。新增 ExperimentContract、Workspace backend 适配、static validation、smoke/formal run、diagnosis/repair cycle、metrics、baseline comparison、local follow-up experiments、commit 和 provenance nodes。

复用 V1 Local/SSH Provider 和 ExperimentBundle standalone mode，通过适配器隔离，不向 WorkflowEngine 塞入新逻辑。测试覆盖 conditional edge、恢复、取消、失败上限、重复 resume 幂等和结果审计。

## 8. Phase 4 - Baseline Reproduction

新增 Repository Intake 与 `BaselineProfile`。流程必须识别 commit、任务、数据集、入口、环境、reported metrics 和 seeds，并区分 `not_started / environment_failed / run_failed / mismatch / validated`。只有 validated baseline 才能作为 improvement denominator。

## 9. Phase 5 - LiteratureGraph

新增 source adapters、query decomposition、canonical dedup、PaperCard、EvidenceUnit、EvidenceGraph、full-text acquisition status、section/chunk parser、BM25 + embedding + metadata ranking 和 gap/conflict/novelty 分析。

先保留 arXiv/Semantic Scholar 适配器，再逐个接 OpenAlex/Crossref/OpenReview。每个来源使用 fixture/contract tests；网络失败不得转成虚构结果。

## 10. Phase 6 - Research Frontier

实现 Ideator branch constructor、FrontierPolicy、best-first selection、branch expand/reject/archive/validate、belief update 与 budget/stop policies。使用离线确定性 fixtures 验证排序和状态迁移，再允许模型填充受 schema 约束的估计。

## 11. Phase 7 - Scientific Big Loop

建立 `ResearchGraph`，闭环连接 LiteratureGraph、Ideation、ExperimentGraph、Critic 和 ResearchController。此阶段完成后才可称为 AI Scientist V2 Core。

重点验收：Controller 选择 branch + operator；科学循环与实验修复循环分离；Guided interrupt 仅在规定情形触发；checkpoint/resume 不重复扣预算或重复提交实验；可拒绝弱分支并保留 promising 分支。

## 12. Phase 8 - MultiModel Router

实现 ModelSpec、ModelRegistry、ModelRouter 及 Qwen/DeepSeek/OpenAI adapters。Qwen 保持 primary；只有 coding/debug 或 independent critic 能力需求触发 specialist。禁止三模型默认投票。加入模型不可用、结构化输出失败、成本策略和确定性 fallback tests。

## 13. Phase 9 - PaperGraph

迁移 Writer 和 PaperWritingManager 的事实审计、章节生成、独立审查及 Word/LaTeX 导出。新增 Claim-Evidence Graph；所有主 Claim 必须追溯到证据或实验记录、metrics、config 和 commit。没有证据链的 Claim 不得导出。

## 14. Phase 10 - Frontend

最后重构前端。先提供 versioned V2 read API，再建立 Research/Literature/Experiments/Evidence/Paper tabs。保留 V1 页面作为兼容视图直到 V2 end-to-end 完成。开发诊断信息移入 Debug，不再以 Agent 运维台或 PipelineTimeline 为首页中心。

## 15. 兼容与数据迁移策略

### Artifact lineage

读取层同时接受旧 `parent_artifact_id` 和新 `parent_ids[]`；写入层在迁移窗口内可双写，但 canonical representation 为 `parent_ids[]`。提供离线迁移与幂等测试，禁止原地不可恢复覆盖。

### Run/API

V2 使用 project/research session 语义，不直接改变旧 `/api/runs`。先增加 versioned endpoint；前端迁移后再标记旧 endpoint deprecated。

### Research ledger

V1 `build_research_state()` 先包为 EvidenceLedger adapter，使用现有 `test_research_state.py` 冻结输出。新 ResearchState 通过 ledger reference 获取 canonical facts。

### Experiments

standalone Bundle 与 repository workspace 使用共同 ExperimentRecord，但各自保留 backend-specific provenance。不得把 standalone 结果伪装为已有仓库 commit 的结果。

## 16. Commit 建议

每个 Phase 至少按“领域模型/实现”“测试”“文档或集成”拆成可审查 commits；但同一原子不变量的实现与测试可同 commit。建议 scope：

```text
docs(v2): record architecture baseline
feat(research): add v2 domain models
test(research): cover frontier and budget invariants
feat(workspace): add isolated git worktrees
feat(graph): add experiment subgraph
```

不得把已有环境安装改动、生成数据、实验输出或密钥混入 V2 commits。

## 17. 最终验收场景

输入 Research Question + existing GitHub/local ML repository + dataset + compute constraints，系统应完成仓库检查、基线复现、多源文献证据、多个 Branch、Frontier 排序、隔离 worktree 修改、smoke/formal experiment、权威指标解析、基线比较、后续 ablation/replication、分支拒绝或保留、最终验证、Claim-Evidence Graph、独立审查和论文/代码/记录/证据导出。

每个结论的最短可审计链为：

```text
ResearchBranch
  -> Hypothesis
  -> ExperimentRecord
  -> Git Commit
  -> Configuration
  -> Metrics
  -> Scientific Analysis
  -> Claim
```
