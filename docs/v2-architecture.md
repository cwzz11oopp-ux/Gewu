# AI Scientist V2 目标架构

> 本文定义目标方向和边界，不表示 Phase 0 已实现任何 V2 代码。迁移顺序见 `docs/v2-migration-plan.md`。

## 1. 核心原则

V2 的控制范式是：

```text
Research is search, not pipeline.
```

系统不再以“下一个缺失 Artifact”为核心问题，而以以下决策为核心：

- 当前最重要的信息缺口是什么；
- 哪个 Research Branch 最值得投入下一单位预算；
- 哪种 ResearchOperator 能产生最高的预期信息增益；
- 哪些结果足以更新信念、拒绝分支或进入最终验证；
- 在预算、风险和证据约束下何时停止。

## 2. 目标上下文与主循环

```text
User: Question + Repository + Dataset + Constraints
  -> Problem Formulation
  -> Repository Intake
  -> Baseline Reproduction and Validation
  -> ResearchState initialization
  -> ResearchController
       -> select ResearchBranch + ResearchOperator
       -> LiteratureSubGraph | Ideation | ExperimentSubGraph | Critic
       -> EvidenceLedger / EvidenceGraph / ExperimentRegistry update
       -> Belief update
       -> Frontier reprioritization
       -> budget and stop policy
       -> repeat
  -> Final Validation
  -> Claim-Evidence Graph
  -> PaperGraph
  -> Independent Scientific Review
  -> Paper + Code + Experiment Records + Evidence
```

ResearchController 不是普通 LLM Agent。它是确定性策略与可审计模型判断的组合，拥有调度权；Agent 只执行被选中的研究操作。

## 3. 分层架构

### 3.1 Domain Layer

纯领域模型，不依赖 FastAPI、文件系统、Git、Provider 或 LangGraph：

- `ProblemProfile`：研究问题、任务、数据、约束和成功标准；
- `BaselineProfile`：仓库、commit、入口、环境、数据、方法、指标、seeds、复现状态和报告差异；
- `ResearchState`：当前科研状态的规范快照；
- `ResearchBranch` / `ResearchFrontier`：假设搜索树与候选优先队列；
- `BeliefState`：对可检验命题的支持度、不确定性和依据；
- `BudgetState`：计算、时间、模型调用、实验次数与风险额度；
- `ResearchAction`：Controller 选择的 branch + operator + reason + cost；
- `ExperimentRecord`：代码、配置、运行和结果的完整 provenance；
- `PaperCard` / `EvidenceUnit` / `EvidenceGraph`；
- `Claim` / `ClaimEvidenceLink` / `ClaimEvidenceGraph`。

### 3.2 Policy and Controller Layer

`ResearchController` 只读一个一致的 ResearchState 快照，返回可验证的 `ResearchAction`。它不直接执行 LLM、Git 或训练命令。

策略拆分为：

- `FrontierPolicy`：best-first branch selection；
- `BudgetPolicy`：动作成本可行性与额度扣减；
- `StopPolicy`：收敛、预算耗尽、证据充分、风险或人工中止；
- `ActionPolicy`：在 literature、ideation、experiment、replication、ablation、robustness、failure investigation 等 operator 中选择；
- `HumanPolicy`：Autonomous / Guided / Manual 的 interrupt 规则。

首版不实现复杂 MCTS。优先分数应保存分解项而非只存最终标量：

```text
priority =
    w_info * expected_information_gain
  + w_science * scientific_potential
  + w_support * evidence_support
  + w_novelty * novelty_potential
  + w_improve * expected_improvement
  + w_uncertainty * reducible_uncertainty
  - w_cost * normalized_compute_cost
  - w_risk * risk
  - w_redundancy * redundancy
```

每个分量必须归一化、可解释并记录来源。缺失估计不能伪装成 0；应记录 `unknown` 与估计方法。

### 3.3 Graph Orchestration Layer

LangGraph 负责 durable execution，ResearchController 负责科研决策。图不把控制权交给某个 Agent。

- `ResearchGraph`：状态装载、Controller 决策、子图调用、belief/frontier 更新、停止与人工中断；
- `LiteratureSubGraph`：查询分解、多源搜索、去重、全文/摘要获取、解析、抽取、证据图更新；
- `ExperimentSubGraph`：contract、workspace、实现、验证、运行、诊断修复、指标比较、局部追加实验和提交；
- `PaperSubGraph`：claim selection、narrative、outline、sections、claim/citation audit、critic、revision、export。

所有图必须支持 conditional edges、cycles、checkpoint、interrupt/resume、failure recovery 和可观测事件。

## 4. ResearchState 与 EvidenceLedger 分离

`ResearchState` 回答“科研现在处于什么状态”：

```text
project
problem
baseline
literature_summary
evidence_graph_ref
frontier
experiments_summary
beliefs
budget
best_branch_id
current_action
paper_state
ledger_ref
```

`EvidenceLedger` 回答“发生过什么以及哪些事实可信”：

- Artifact lifecycle；
- verified/unverified/conflicted/superseded；
- 参数冲突与 canonical metrics；
- experiment facts、claims 和 provenance；
- append-only 事件与内容哈希。

V1 `workflow/research_state.py` 的逻辑迁移到 EvidenceLedger；新 ResearchState 只引用账本，不复制其全部历史。

## 5. Research Frontier

每个 `ResearchBranch` 至少包含：

```text
id, parent_id
hypothesis, mechanism, proposed_change
expected_observation, falsification_condition
closest_prior_work, novelty_risk
status
evidence_ids, experiment_ids, observations
base_commit, code_commit
confidence, uncertainty
priority and priority_components
estimated_cost, risk, redundancy
next_actions
created_at, updated_at
```

状态至少为 `proposed / queued / running / promising / inconclusive / rejected / validated / archived`。状态迁移必须显式校验，例如 rejected 不得直接变 running，除非产生新子分支或经过人工 reopen 事件。

Frontier 支持：添加根/子分支、best-first selection、标记执行占用、更新证据、拒绝、归档、验证和生成后续动作。不同 Branch 的代码变体位于独立 Git worktree。

## 6. ResearchAction 与 Operator

Action 是选择结果，不是 Agent 名称。初始 operator 集合：

```text
SEARCH_LITERATURE
REPRODUCE_BASELINE
EXPLORE_NEW_MECHANISM
EXPAND_BRANCH
REFINE_HYPOTHESIS
RUN_EXPERIMENT
RUN_REPLICATION
RUN_ABLATION
INVESTIGATE_FAILURE
CHALLENGE_HYPOTHESIS
RUN_ROBUSTNESS
STOP_BRANCH
FINAL_VALIDATION
WRITE_PAPER
```

每个 Action 必须绑定 branch、决策理由、目标信息缺口、预期信息增益、估计成本、预算检查、前置条件和完成判据。Agent 只能执行已批准 Action 的 Contract。

## 7. Repository Research 与 Baseline

Repository Research Mode 是 CS 研究主模式：

```text
Repository Intake
  -> safe open and identity check
  -> code/config/test/entrypoint inspection
  -> environment reconstruction
  -> dataset contract
  -> baseline command
  -> smoke reproduction
  -> formal reproduction across required seeds
  -> metric parsing and reported-result comparison
  -> BaselineProfile
```

没有 `reproduction_status=validated` 的 BaselineProfile，不允许把后续结果描述成“改进”。如果复现失败，应进入 baseline diagnosis 或明确终止，而不是以合成占位实验替代。

## 8. ExperimentWorkspace 与 ExperimentSubGraph

### 8.1 Workspace

`RepositoryWorkspace` 封装：

- 打开并验证现有 Git repository；
- 读取 base commit 与 dirty state；
- 为每个 ResearchBranch 创建受控 worktree；
- 仓库检查与代码索引；
- 允许列表内的文件编辑和命令执行；
- static validation、smoke test、formal command；
- diff、changed files 和 commit 记录；
- 清理只作用于已验证的 branch worktree 路径。

### 8.2 Experiment small loop

```text
ExperimentContract
  -> inspect repository/worktree
  -> implementation plan
  -> edit existing repository
  -> static validation
  -> smoke test
  -> formal run
  -> runtime failure? diagnosis -> bounded repair -> retry
  -> parse authoritative metrics
  -> compare baseline
  -> scientific analysis
  -> need local evidence? seed / hyperparameter / ablation / robustness
  -> commit variant
  -> Experiment Evidence Package
```

Experiment loop 只处理实现、运行、参数和局部验证；是否改变研究方向由 ResearchController 的 scientific loop 决定。

V1 ExperimentBundle 保留为 standalone mode，并通过统一 `ExperimentBackend` 接口与 workspace mode 共存。

## 9. Git provenance

每个 ExperimentRecord 必须记录：

```text
experiment_id, branch_id, purpose
repository_url, base_commit, worktree_branch, code_commit
changed_files, diff_summary, config
dataset, seeds, metrics
environment, logs, figures
result_status, audit_status
started_at, completed_at
```

Claim 不能只链接一个结果字典；必须可追到 Branch、Hypothesis、ExperimentRecord、config、metrics 和 code commit。

## 10. LiteratureSubGraph

目标数据流：

```text
Research Question
  -> Query Decomposition
  -> parallel arXiv / Semantic Scholar / OpenAlex / Crossref / OpenReview
  -> canonical identifier deduplication
  -> relevance ranking
  -> full-text acquisition with explicit access status
  -> section/chunk parsing
  -> PaperCard and EvidenceUnit extraction
  -> EvidenceGraph
  -> gap/conflict/novelty analysis
```

检索按 Paper -> Section -> Chunk -> EvidenceUnit 分层。只有摘要时必须标记 `abstract_only`；全文获取失败是可见状态，禁止补造段落位置或实验细节。

Hybrid retrieval 由 BM25、embedding、metadata relevance、recency 和 citation information 组成。不同信号及其缺失状态应保留，便于复核排序。

EvidenceGraph 边至少区分 SUPPORT、CONTRADICT、CONTEXT、ANALOGY。EvidenceUnit 记录 paper_id、claim、section、location、type、strength 和 acquisition provenance。

## 11. Hypothesis / Branch Constructor

Ideator 输入 ProblemProfile、BaselineProfile、EvidenceGraph、Known Failures、Frontier 和 Budget，输出满足门禁的 Branch proposal：

```text
Research Gap
Hypothesis
Mechanism
Proposed Change
Expected Observation
Falsification Condition
Minimal Experiment
Closest Prior Work
Novelty Risk
```

字段不完整、不可证伪、没有最小实验或无法关联已有证据的 proposal 不进入 Experiment Queue。

## 12. 多模型系统

Agent 不绑定 Provider。`ModelRegistry` 保存 ModelSpec，`ModelRouter` 按 capability、成本、上下文、工具和结构化输出要求选择模型。

默认策略：

```text
Qwen: primary scientist and synthesis
DeepSeek: coding/debug specialist when requested
GPT: independent scientific critic when requested
deterministic validators: final contract authority
```

不做所有问题的三模型投票。每次 specialist 调用必须由任务能力缺口触发，并记录 routing reason、model、prompt version、cost class 和输出审计。

## 13. Agent 边界

V2 认知 Agent 收敛为 Researcher、Ideator、Experimenter、Critic、Writer。ResearchController 不属于 Agent。

- Researcher：问题与文献证据抽取；
- Ideator：构造可证伪 Branch；
- Experimenter：实现已批准 ExperimentContract；
- Critic：挑战机制、证据和结论；
- Writer：从 Claim-Evidence Graph 写作。

SkillRuntime 降级为 Prompt/Protocol Module，继续提供版本化指令、工具最小权限和哈希记录，但不决定科研路线。

## 14. Sandbox

统一 `SandboxProvider`：Docker、RemoteDocker、SSH、Apptainer；Local GPU 可作为受控本机 backend 兼容存在。所有命令必须绑定 workspace 根目录、允许策略、资源预算、超时、日志和取消句柄。LLM 不获得无限制宿主机访问。

## 15. Artifact 与 Claim-Evidence Graph

Artifact 从单父升级为 `parent_ids[]`，同时增加 project_id、branch_id、experiment_id、content_uri、metadata、created_by、model、prompt_version、code_commit 和 verified。迁移期间兼容读取 `parent_artifact_id`，新写入以 DAG 模型为准。

PaperGraph 的唯一事实输入是 Claim-Evidence Graph，而不是未经筛选的 Artifact history：

```text
Claim
  -> EvidenceUnit or Experiment Evidence
  -> ExperimentRecord
  -> Metrics and Config
  -> Code Commit
```

Claim audit、citation audit 和 independent review 通过后才能导出。

## 16. Human-in-the-loop

支持 Autonomous、Guided、Manual，默认 Guided。Guided 只在研究方向显著改变、高计算成本、新数据需求、付费外部服务或最终结论时 interrupt。V1 的每次 hypothesis 强制选择不进入 V2 默认主路径。

## 17. 前端最终信息架构

后端 V2 core 稳定后再改前端。Research Workspace 首页显示：研究问题、当前科学信念、Frontier、当前动作及理由、最佳结果、关键发现、开放问题和剩余预算。主 Tab 为 Research、Literature、Experiments、Evidence、Paper；Agent trace、raw artifacts、Skill hashes 和 Provider diagnostics 移入 Developer/Debug。

## 18. 不变量

1. 不伪造论文、全文、实验、指标、引用或代码提交。
2. 只有同协议且审计通过的结果才能声称优于 baseline。
3. 任何 Branch、Action、Experiment、Claim 均可追溯。
4. 科学判断与运行时修复分离。
5. Controller 决策与模型生成均可审计；确定性验证拥有最终契约权。
6. 新旧系统并存期间，V1 可运行性不得因 V2 空壳而下降。
7. 每个 Phase 只添加当期需要的文件，不预建庞大空目录。
8. baseline/variant 比较必须通过 `ProtocolCompatibilityGate`；protocol 不兼容或 audit 未通过时，结果只能作为 Observation，状态码为 `COMPARISON_NOT_ALLOWED`，不得生成直接 improvement claim。
9. 比较分母优先使用当前环境、当前 protocol 下审计通过的 local baseline；论文 reported metric 仅是 reference/provenance。
