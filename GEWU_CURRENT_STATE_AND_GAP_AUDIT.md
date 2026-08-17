# Gewu 当前状态与差距审计

审计日期：2026-08-16  
审计方式：严格只读（源码、路由、前端调用、Skill、测试与已有运行记录的静态对照）  
范围：生产研究主线以当前前端实际调用为准；目标限定为 ML/DL 的 classification、forecasting、anomaly detection。

## 1. Executive Summary

Gewu 已经具备一条可工作的、以 Artifact 为中心的九步研究流水线：本地数据目录可在首个 AI 步骤前被结构化扫描；证据、候选假设、计划、实验 Bundle、实验结果、审计和报告均可持久化；本地 GPU/SSH 执行、停止、结果下载、报告下载和部分诊断均已有实现。生产前端实际使用的是 `/api/runs`。

但它尚未达到本轮目标定义的稳定 ML/DL 研究工作台。最关键的原因不是缺少更多通用领域规则，而是生产状态机与输入契约不完整：研究创建只保存一段 `constraints` 文本，没有结构化的数据说明、任务类型、baseline、epoch 或不可变约束；通用 `rerun_from` 仍会删除未锁定的历史 Artifact；第一步 Qwen 请求的 HTTP 400 会丢失服务端正文并将 Run 直接标记失败；结果分析与科学路由仍大量依赖 LLM，而非完整的确定性 baseline-vs-Idea 统计比较。

此外，应用同时注册了两套科研状态机：九步 `/api/runs` 与未被前端调用的 `/api/v2/research/sessions`。后者有自己的 session、frontier、budget 和 bootstrap 模型。这不是当前用户路径的一部分，但会造成“后端已有能力”的维护误判。

### 审计口径与计数

下表包含 28 个可实施的原子需求。分类统计：

| A 已有且基本正确 | B 已有但不完整 | C 已有但与目标冲突 | D 当前缺失 |
| ---: | ---: | ---: | ---: |
| 0 | 14 | 10 | 4 |

优先级问题统计（同一问题只计最高优先级）：P0 6、P1 6、P2 4、P3 3。

## 2. Current Production Architecture

### Backend、Workflow、API 与 Storage

当前生产 API 是 `backend/app/api/runs.py` 的 `/api/runs`。`frontend/src/App.tsx` 创建 Run 后调用 `POST /api/runs/{id}/pipeline/start`，并轮询 `GET /api/runs/{id}` 与实验进度；该调用不经过 v2 session API。

真实路径为：

```text
ResearchPage / App.tsx
  -> POST /api/runs
  -> POST /api/runs/{run_id}/pipeline/start
  -> WorkflowOrchestrator._drive
  -> WorkflowEngine.run_step / _run_step
  -> Agent + SkillRuntime + Provider
  -> Repository (runs.json, append Event/Artifact)
  -> GET /api/runs/{run_id} / report download / experiment-files
  -> React view-model 和工作台页面
```

九个生产步骤由 `backend/app/workflow/steps.py` 固定：`problem_understanding`、`knowledge_integration`、`hypothesis_generation`、`evidence_reasoning`、`research_plan`、`experiment_task`、`experiment_run_analysis`、`feedback_revision`、`report_export`。各分支实现在 `backend/app/workflow/engine.py`；后台串行推进器是 `backend/app/workflow/orchestrator.py`；Run、Step、Artifact、Event 的持久化为 `backend/app/storage/repository.py` 和 JSON store。

`/api/v2/research/sessions` 由 `backend/app/api/v2_research.py` 注册，具备 session/frontier/evidence/trajectory/parameter-sweep API，但 `frontend/src/api/client.ts` 没有任何 v2 调用，生产工作台不展示其状态。这应标注为 **NOT ACTIVE FOR THE CURRENT FRONTEND**，不能计入当前生产能力。

### Agent 与 Skill 的实际路由

`backend/app/workflow/skills.py` 的静态 `_ASSIGNMENTS` 是生产路由真源，`SkillRuntime.prepare()` 在每一步加载完整 `SKILL.md`、交集授权工具并记录指令哈希。核心映射是：

| Step | Agent | 实际路由 Skill |
| --- | --- | --- |
| problem_understanding | research | problem-framing |
| knowledge_integration | research | research-lit, research-wiki |
| hypothesis_generation | idea | idea-creator, hypothesis-evidence |
| evidence_reasoning | critic | evidence-recovery, idea-selection, novelty-check, research-review |
| research_plan | planning | research-refine, hypothesis-experiment-gate, experiment-plan |
| experiment_task | experiment | experiment-implementation |
| experiment_run_analysis | experiment | run-experiment, analyze-results, experiment-audit；可条件加 monitor-experiment |
| experiment_diagnosis（内部失败路由） | diagnostic | experiment-diagnosis |
| feedback_revision | critic | experiment-iteration, result-to-claim；可条件加 research-refine、experiment-plan、ablation-planner |
| report_export | writer | competition-report, report-quality-audit |

生产 `skills/` 目录同时含有大量未路由、面向 Codex/Claude/Gemini 或拥有 `Bash(*)`、`WebSearch`、MCP/Agent 权限的 Skill。静态生产路由本身不会自动选择它们，但 `SkillCatalog` 会枚举大多数一级目录；因此目录并不是一个干净的生产 Skill 边界。

### Frontend

前端使用 React，状态入口在 `frontend/src/App.tsx`；主要页面为 `ResearchPage`、`IdeaPage`、`ExperimentPage`、`ResultsPage`，并通过 `researchViewModel.ts` 从真实 Artifact 建模。配置弹窗可编辑模型 provider/角色与实验运行时，但不承担完整研究输入表单。

## 3. Current End-to-End Flow

1. 用户在前端输入问题、领域、自由文本 constraints 和可选 GitHub URL；`App.tsx` 调 `api.createRun()`。
2. `WorkflowOrchestrator.start()` 开启后台线程；`_drive()` 按 Artifact 是否存在决定下一步骤。
3. `problem_understanding` 内部会先在已配置为本地数据源时执行 `inspect_dataset_directory()`，保存并锁定 `dataset_profile`，随后才调用 Research Agent。
4. `knowledge_integration` 走本地/外部文献、Wiki、证据审计和 synthesis；之后产生 hypotheses、evidence reasoning/selection。
5. `research_plan` 创建可审查的 plan candidate，调用 DeepSeek 计划审查；审查耗尽在当前代码中已转为 `NEEDS_PLAN_REVISION`，不是旧版的直接异常失败。
6. `experiment_task` 生成并验证 provider-neutral Bundle；`experiment_run_analysis` 执行 Bundle、分析、审计，故障可进入 diagnosis/repair 路径。
7. `feedback_revision` 基于结果给出下一轮；完成后 `report_export` 由 Writer 生成报告 Artifact。报告与实验包下载走 `/api/runs/{id}/report/download`、`/experiment-package/download`。

实际运行记录表明第一步仍可因模型请求失败而中断：`run_d4e051ed022e` 已成功生成并锁定 IPIX17 DatasetProfile，随后 `research.structure_problem` 对 Qwen 返回两次 HTTP 400，Run 在 `problem_understanding` 标为 failed。该事实说明预检并未覆盖最小模型请求与模型/参数兼容性，且错误正文未进入 Run Artifact。

## 4. Requirements Coverage Matrix

| # | Requirement | Status | Current implementation and real behavior | Backend / API / Frontend / Skill / Test | Gap and future handling | Risk |
| ---: | --- | --- | --- | --- | --- | --- |
| 1 | 唯一生产主线 | C | `/api/runs` 是 UI 主线；`/api/v2/research/sessions` 同时注册另一套状态、预算、frontier。 | Yes / Yes / only runs / partial / v2 tests only | 明确九步链为 production，v2 隔离为 experimental 或完成迁移。 | HIGH |
| 2 | ML/DL 三任务范围 | C | `scientific_stability.py` 推断 empirical、computational、simulation、literature、mathematical 等广义 profile。 | Yes / indirect / no task selector / generic / partial | 收窄生产入口为 classification/forecasting/anomaly detection。 | MEDIUM |
| 3 | 创建时结构化研究输入 | D | `CreateRunRequest` 只有 title、problem_input、domain、constraints、GitHub URL。 | Yes / Yes / text fields only / no / API tests partial | 新增结构化 ResearchConstraints、DatasetInput、TaskType、baseline、epoch。 | HIGH |
| 4 | 用户数据只读检查且先于 Idea | B | 本地数据时 `problem_understanding` 先检查、保存并锁 DatasetProfile；但这是 pipeline 启动后，不是创建 Run 前。 | Yes / settings test / no per-run dataset picker / partial / dataset tests | 保留现有检查；把数据/说明作为 Run 输入并扩展 profile。 | HIGH |
| 5 | DatasetProfile 的完整语义 | B | 文件清单、类型、大小、哈希、CSV/JSON/NPY/NPZ/MAT 样本结构可得；语义事实明确 unknown。 | Yes / indirect / limited / no / inspection tests | 缺样本总数、标签/目标、类别分布、缺失/异常、时间顺序、官方 split、任务兼容性解析。 | HIGH |
| 6 | 可选原仓库只读理解与实验工作区 | C | `github_source.py` 只读 HTTPS 抓取至多 40 文件、提取代码证据；并不 clone/索引完整 repo，也不创建基于 repo 的独立实验工作区。 | Partial / no repo workspace API / URL only / no / github tests | 不能把“GitHub 代码证据”误称为 baseline/repo integration；未来需 worktree、commit、diff lineage。 | HIGH |
| 7 | 冻结的结构化 ResearchConstraints | C | Run 仅保存字符串 `constraints`；部分 plan/experiment prompt 读取它，但无 schema 或不可变 contract。 | Partial / string / text only / partial / no end-to-end constraint test | 引入单一约束 Artifact，所有 plan/code/repair 从同一 ID 读取。 | HIGH |
| 8 | 广泛文献、去重、synthesis | B | `knowledge.py`、evidence audit、research synthesis、Research Wiki 已实现来源与去重路径。 | Yes / literature API / EvidenceTable / research-lit/wiki / tests | 外源实质上是 arXiv/Crossref；未实现目标的可配置广检索与分层阅读。 | MEDIUM |
| 9 | 显式上下文预算及遗漏 ID | B | `PromptContextBudget`、`select_units_bounded`、plan-review telemetry/selection 已存在。 | Yes / artifact only / no complete UI / partial / prompt-context tests | 仍有若干调用依赖列表切片/压缩；需逐 prompt 验证“全部 gap 是否可达”。 | HIGH |
| 10 | 二阶段全文/Conclusion/Future Work/PaperProfile | D | 本地上传可保存 PDF/TXT/MD 元数据；生产外检索卡主要是标题/摘要。 | Partial / literature document API / basic library view / research-lit only / library tests | 缺 PaperProfile、全文按需读取和 conclusion/future-work 的标准化进入 synthesis。 | MEDIUM |
| 11 | 固定四个 Idea 与正向概率排序 | C | hypothesis 数量由模型/validation 决定，非固定 4；评分偏证据、可行性、科学性。 | Yes / hypothesis API / list all returned / idea skills / tests partial | 目标要求固定 4、排序偏创新性+正向可能性，当前设计不同。 | HIGH |
| 12 | Idea 证据与最小验证可追溯 | B | hypothesis provenance、evidence registry、candidate assessments、selection Artifact 已有。 | Yes / implicit / IdeaPage partial / hypothesis-evidence etc. / tests | UI 不完整展示“baseline问题、机制预期现象、最小验证、全部来源”。 | MEDIUM |
| 13 | v1-v3 科学演化并自动换 Idea | B | 假设修订有 append-only special path；feedback iteration 上限为 4。 | Partial / rerun/continue / no version view / iteration skills / evolution tests | 未实现严格每 Idea 最多 v3；没有明确 v3 封存后自动进入下一候选。 | HIGH |
| 14 | Qwen 主模型、DeepSeek 独立审查 | B | role router/plan review 使用 DeepSeek；诊断与结果审查含 Critic Agent。 | Yes / provider config / roles UI / reviewer skills / provider tests | 不能证明每个“负趋势”都由 DeepSeek独立读取完整 diff/log/design；UI无完整诊断页。 | MEDIUM |
| 15 | Baseline 选择与论文复现 10% 门槛 | D | plan 可含 comparisons/baselines，实验可执行计划内容。 | Partial / no dedicated API / no baseline editor / partial / no reproduction test | 缺 structured baseline selection、paper-vs-local comparison、10% gate、Approximate Reproduction。 | HIGH |
| 16 | 公平实验冻结 | B | Bundle/runtime contract 绑定数据 fingerprint、root、seed、parameters，计划有 split/scientific contract 验证。 | Yes / artifact-bound / experiment presentation partial / experiment-plan/audit / bundle tests | baseline/Idea 间的同 protocol 与 repair 禁止所有关键变量漂移未形成单一不可变合同。 | HIGH |
| 17 | 渐进实验（smoke/small/full） | C | harness 有 `--smoke-test` 且 smoke 只跑第一个 seed；没有明确 small/full budget 状态机。 | Partial / no explicit API / no stage controls / run-experiment / harness tests | 不应把 smoke 等同 small/full；缺动态 seed、epoch 扩大和正式对照策略。 | MEDIUM |
| 18 | 确定性 Result Analyzer | B | harness 计算 seed metrics mean/std；结果 analysis/critic 仍由 LLM 解释。 | Partial / artifacts / metric widgets / analyze-results/result-to-claim / tests partial | 缺 paired delta、median、CI、effect size、noise screening、curve summary 的统一确定性模块。 | HIGH |
| 19 | Primary/secondary metric policy | B | plan/contract 可携带 metrics；Writer 有 grounded report checks。 | Partial / no metric policy API / display partial / planning/writer skills / report tests | 未见“未指定时固定一主多副”的全局选择与禁止泛化结论的硬 gate。 | MEDIUM |
| 20 | 小结果后的四类决策路由 | C | diagnosis、feedback、follow-up 共存，但 Run/Step 仍普遍使用 completed/failed/interrupted，且路径受 LLM verdict 驱动。 | Partial / implicit endpoints / status simplified / iteration/diagnosis / tests partial | 需明确 positive、ambiguous、negative、engineering abnormal 四分路由。 | HIGH |
| 21 | 工程修复 3+3 与完整尝试证据 | B | Bundle candidate attempt、诊断、重试、日志与运行状态有持久化；若干修复上限存在。 | Yes / experiment file APIs / log UI partial / diagnosis skill / provider+harness tests | 没有统一保证“Qwen 3 次 + DeepSeek 3 次”；traceback/diff/attempt 一览不完整。 | MEDIUM |
| 22 | API/GPU/environment preflight 在正式 Run 前 | B | provider、模型角色、实验设置测试 API 已有；ProjectSettingsModal 能触发。 | Yes / Yes / manual settings / no / config/provider tests | 不会在 `POST /api/runs`/start 前强制执行最小模型请求、GPU/VRAM/disk/repo write 的 Run-scoped preflight。 | HIGH |
| 23 | 单一生产虚拟环境 | B | README/默认本地配置指向 `.venv`，但 settings 允许 `LOCAL_GPU_PYTHON` 任意路径，子进程使用该值。 | Yes / settings API / editable / run skill / provider tests | “单环境”是约定不是强约束；依赖安装变更没有 Artifact 级账本。 | MEDIUM |
| 24 | Append-only Artifact/checkpoint/resume | C | `add_artifact` 追加；但 `_rerun_from()` 会移除未锁定、受影响步骤的 Artifact。 | Partial / rerun API / rerun button / partial / rerun tests | 与“历史不得覆盖/删除”直接冲突；special hypothesis path虽追加但不是普遍语义。 | HIGH |
| 25 | Pause / stop / terminate / resume | B | orchestrator stop、LLM cancellation、实验 terminate API、前端按钮和状态轮询均存在。 | Yes / Yes / Yes / monitor skill / orchestrator/API tests | 没有独立 terminate Run 状态；stop 语义混合 pause/terminate；恢复可触发 rerun cleanup。 | MEDIUM |
| 26 | 仅正/负科学结果自动报告 | C | report readiness 要求 verified evidence、result、feedback；并不按 completed_positive/completed_negative 明确建模。 | Yes / download APIs / results page / report skills / report tests | 负结果报告的 Idea v1-v3/DeepSeek/原因结构无专门合同；受限状态与科学失败混杂。 | MEDIUM |
| 27 | 前端研究创建与数据/约束配置 | D | 创建页仅问题、领域、自由文本约束、GitHub URL；数据在全局设置中配置。 | Partial / create schema limited / no required controls / no / UI tests partial | 缺 per-run dataset、说明、task、baseline、epoch、约束冻结、preflight 状态。 | HIGH |
| 28 | 前端真实交互、错误与结果展示 | C | RunControls、ExperimentPanel、报告下载等确有 API 绑定；ResearchPage 失败仅显示 step ID，view-model 还截取最近 6 个实验。 | Yes / Yes / partial/misleading / partial / UI contract tests | 缺真实错误类别、DeepSeek诊断、版本谱系、完整实验/文献可见性；“后端有而前端无”较多。 | HIGH |

## 5. Backend Gaps

1. **Run 输入模型不足（P0）**：`RunRecord` 的 `constraints: str` 无法承载并冻结 baseline、可变模块、禁止项、metrics、epoch、数据协议。数据目录是全局 experiment setting，不是 Run 输入。
2. **模型预检不足（P0）**：当前 provider test 是用户在设置页手动操作；创建/开始 Run 不发送最小结构化请求。IPIX17 Run 的实际 HTTP 400 已证明此缺口。
3. **模型错误证据丢失（P0）**：Qwen provider 对 HTTP 400 进行重试，但只有设置 `QWEN_DIAGNOSTIC_LOG` 时才保存脱敏正文；Run/Event 只保留 `http_400` 汇总，无法操作性诊断。
4. **仓库支持仅是只读证据（P0）**：GitHub inspector 不建立实验 worktree，不识别真实 baseline train/eval loader 的可执行合同，也无法保存 repo diff。
5. **baseline/reproduction gate 缺失（P1）**：没有论文数值与本地 baseline 的 10% 容差规则，也没有公共 baseline 异常阻止所有 Idea 的状态。
6. **结果统计缺口（P0）**：平均/标准差存在于 harness，但尚无完整确定性 paired/CI/effect-size 决策组件。
7. **领域过度泛化（P1）**：当前 profile/resolver 引入数学、文献综合等非目标领域分支，增加生产状态复杂度。

## 6. Skill Gaps / Conflicts

### 已有且应保留

- 生产 Skill 路由静态、每步可审计，且 `SkillRuntime` 以“声明工具 ∩ Agent policy ∩ 注册工具 ∩ 配置工具”授权；这是应保留的安全边界。
- `experiment-diagnosis`、`experiment-audit`、`result-to-claim`、`hypothesis-experiment-gate` 已表达重要的科研/工程分离意图。

### 差距与冲突

1. `skills/` 同时是 production runtime root 和大量外来/手工 Skill 的容器。虽然 `_ASSIGNMENTS` 不会动态选择它们，目录层级仍使 catalog 与维护者误以为其可用于生产（P0）。
2. 文档的“24 unique Skills”与实际静态路由并不完全同构，例如 `hypothesis-evidence`、`evidence-recovery` 由代码路由但文档表未等价呈现（P2，**DOCUMENTATION ONLY / NOT ACTIVE 不能反向推导生产行为**）。
3. 缺 ML/DL common core + classification/forecasting/anomaly 三个任务专用的生产 Skill 合同（P1）。当前 Skills 多为通用科研文本协议，任务差异主要留给模型猜测。
4. Python validator、Skill 文字规则、plan prompt 分别承担约束，但没有一个版本化的“规则归属表”；同一公平性规则可能出现漂移（P1）。
5. 多个未路由 Skill 声明 `Bash(*)`、`WebSearch`、MCP/Agent 等权限。它们不能被纳入生产 runtime allow-list，且应与 production Skill 物理或逻辑隔离（P0）。

## 7. Experiment & Result Analyzer Gaps

### 已有能力

- Bundle 是 provider-neutral；本地 GPU 与 SSH 复用结果合同。
- Runtime contract 绑定数据集合同 ID、fingerprint、根目录、seed、参数、结果 ID。
- harness 在 smoke 时仅执行一个 seed，在正式运行执行 contract 内所有 seed，并产生 mean/std。
- Local provider 记录 runtime status、日志、attempt 目录、结果文件；API 可读日志/代码/manifest/environment。

### 未完成或冲突

- 没有明确 small-scale 与 full-scale 两种不同预算合同，smoke 不是小规模科学筛选。
- 没有基线复现阶段，无法保证 Idea 不在异常 baseline 上启动。
- 没有 canonical 的 baseline-vs-Idea paired comparison，也未看到 effect size、CI、median、seed direction consistency、noise magnitude 或训练曲线确定性摘要。
- 当前 ExperimentPanel 可展示 metrics、诊断与日志，但没有完整 baseline/Idea/version/attempt 关系、repair diff、DeepSeek recovery 或 small/full 标识。
- 计划/repair 具备部分合同校验，但冻结边界没有集中在一个不可变 ResearchConstraints Artifact；无法在审计时证明 repair 从未更改 baseline、epoch、split 或 primary metric。

## 8. Artifact / Resume / Recovery Gaps

### 已有能力

- Artifact 有版本、父 Artifact ID、创建者、来源步骤和锁定标志；Event 也持久化。
- 本地实验 attempt 目录、运行状态、日志和结果可在进程异常后读取。
- 服务启动时会把运行中的 Run 调整为 interrupted，并对自动 Run 调度恢复。
- 假设无可选结果时的特殊重跑路径是 append-only。

### 高风险差距

`WorkflowEngine._rerun_from()` 会计算受影响步骤，并从 `run.artifacts` 中删除所有未锁定 Artifact，随后递归删除其子项。这与目标“Run -> Idea -> Version -> Attempt，任何历史不得覆盖”冲突，也是恢复语义的 P0 问题。锁定不是普遍、自动且细粒度的 lineage 保护机制；一般 rerun 不能被视为取证安全的 resume。

另一个缺口是状态等级：Run 只有通用 status 字符串，未形成 `completed_positive`、`completed_negative`、`engineering_unresolved`、`terminated_by_user`、`baseline_diagnosis` 等明确、互斥且前端可展示的状态模型。

## 9. Frontend Interaction Gaps

### 研究创建页

- 有：research question、domain、自由文本 constraints、可选 GitHub URL。
- 无：每 Run 数据集路径/上传/说明、task type、baseline、允许/禁止修改、指标、epoch、数据协议、开始前 preflight 总结。

### 文献与 Idea

- EvidenceTable 支持本地文献上传、验证、附加、加入 Wiki、删除；ResearchPage 可在 graph 中查看部分 gap 与关联 paper。
- 缺：完整有效文献列表的角色层（Foundational/Core/Supporting）、结论/未来工作、全文入口、固定 4 Idea、Idea v1-v3、完整 ranking reason。
- `researchViewModel.ts` 的 `visibleExperiments = experiments.slice(-6)` 明确使研究地图只显示最近六个实验；这不是数据丢失，但会造成“全部实验都已显示”的 UI 误导。

### 实验与错误

- 有：真实 `terminateExperiment`、stop pipeline、rerun、continue、日志/API 下载，按钮不是纯视觉。
- 缺：暂停、终止、恢复的独立语义与 loading/failure reconciliation；错误显示在 ResearchPage 中大多只是失败 `step_id`，并未把后端的 Qwen/DeepSeek/CUDA/dataset 等 code 和 detail 可靠映射出来。
- 缺：DeepSeek 独立诊断、代码 diff、Idea revision reason、下一轮设计的专用展示。

### 最终结果

- 有：报告 Artifact 检查、Word/ZIP 下载链接，以及基于 Artifact 的部分 reproducibility UI。
- 缺：正/负报告的不同结构化视图；negative report 所需的完整 Idea 演化与独立审查证据展示。

## 10. Backend ↔ API ↔ Frontend Disconnects

| Capability | Backend | API | Frontend | Skill | Test | Result |
| --- | --- | --- | --- | --- | --- |
| v2 sessions/frontier/trajectory | Yes | Yes | No | separate v2 architecture | v2 tests | NOT ACTIVE FOR CURRENT FRONTEND |
| Dataset inspection | Yes | settings/bootstrap only, not Run create | global settings only | none dedicated | dataset tests | Backend capability not exposed per Run |
| GitHub code evidence | Yes | stored only through Run input | URL input exists | none | github tests | No repo baseline/workspace UI |
| Skill invocation hash/tool audit | Yes | embedded in Event/Artifact only | No dedicated view | Yes | skill tests | Backend-only observability |
| Plan review ledger/context telemetry | Yes | embedded Artifact only | No dedicated view | plan Skills | plan tests | Backend-only observability |
| Experiment log/code/manifest/environment | Yes | Yes | log shown; other files not fully surfaced | run/diagnosis | provider tests | Partial UI exposure |
| Experiment termination | Yes | Yes | Yes | monitor/run | API/orchestrator tests | Active production feature |
| DeepSeek reviewer/diagnostic detail | Partial | indirect Artifact only | No dedicated panel | reviewer/diagnosis | reviewer tests | Hidden/partial |
| Local literature attachment/verification | Yes | Yes | Yes | research-lit/wiki | library tests | Active production feature |
| Report/ZIP download | Yes | Yes | Yes | competition-report | report/API tests | Active production feature |

## 11. Existing Features That Should NOT Be Rewritten

1. **Dataset inventory and contract fingerprinting**：`dataset_inspection.py` 已安全地枚举、采样、生成 fingerprint，并在计划/执行阶段已有绑定基础；应扩展而非重写。
2. **Bundle/runtime contract 与 Local/SSH runner**：实验目录、attempt、日志、结果校验、进程树终止和数据 fingerprint 验证是可靠基础。
3. **Artifact/Event 持久化模型**：追加 Artifact、父子关系、事件审计应保留；需要改变的是 destructive rerun，而不是抛弃整个 repository。
4. **静态 Skill routing 与最小权限交集**：应保持静态 production routing，之后加 runtime lock/目录隔离。
5. **报告导出**：DOCX/ZIP 输出和只打包已存在制品的原则应保留；需要增加正/负报告合同而非重写 exporter。
6. **前端真实 API 绑定**：start/stop/rerun/terminate/report download 已有真实调用，修正语义和错误展示即可。

## 12. High-Risk Legacy Rules

| Rule / location | Why high risk for the three target tasks |
| --- | --- |
| 广义 `infer_research_profile()` 与 mathematical/literature branches | 非 classification/forecasting/anomaly 的分支进入生产决策，扩大状态空间并掩盖任务合同缺失。 |
| 仅用自由文本 `constraints` | 分类的类别/损失规则、预测的时间泄漏规则、异常检测的阈值/正负标签规则无法被冻结、验证或公平比较。 |
| DatasetProfile 语义均可能为 unknown | 对 forecasting 的时间顺序、对 classification 的 label、对 anomaly 的 contamination/threshold 都不能仅由模型猜测。 |
| `visibleExperiments = slice(-6)` | 长演化链中旧版 baseline、Idea v1-v3 会在前端消失，尤其误导负结果审计。 |
| 通用 `rerun_from` 删除未锁 Artifact | 任一任务都可能失去初版 plan、baseline 结果、失败代码或 DeepSeek 审查证据。 |
| HTTP 400 被列入 retryable 状态 | 配置/模型/参数错误会浪费重试并丢失正文，首步就把 Run 标 failed。 |

## 13. Recommended Implementation Order

### P0 — 正确性、不中断和公平性

1. 设定单一 production 主线并把 v2 标为 experimental；不迁移前端前不将其能力计入生产。
2. 用结构化、不可变的 Run 输入合同替换自由文本约束：数据目录和说明、任务类型、baseline、可改/不可改项、metrics、epochs、split/seed protocol。
3. 在创建/启动 Run 前增加 Run-scoped preflight：最小 Qwen/DeepSeek JSON 请求、model name、认证、数据路径、Python/Torch/CUDA/VRAM/disk/repo workspace；保存脱敏诊断。
4. 将 HTTP 400 归为配置/请求诊断，保留 response excerpt/request ID，避免无意义重试；不将“模型配置错”展示为“研究失败”。
5. 将 rerun 改为 append-only attempt/supersession，禁止删除历史 Artifact；定义真正 checkpoint resume。
6. 建立 baseline-vs-Idea 的确定性 Result Analyzer 与统一公平性合同。

### P1 — 科研闭环

1. 完成 DatasetProfile 的三类任务语义适配和用户数据说明合并。
2. 固定四个 Idea、定义每个 Idea v1-v3、v3 封存/切换候选的状态机。
3. 实现 baseline selection/reproduction、10% gate 和 Approximate Reproduction 报告字段。
4. 加入 small/full 阶段、动态 seed、四类结果路由和 3+3 工程恢复证据合同。
5. 文献 PaperProfile、全文按需阅读、可追溯 context coverage 及 gap reachability tests。
6. 划分 ML/DL common / classification / forecasting / anomaly production Skills，并建立规则归属表。

### P2 — 可解释前端

1. Run 创建页补全结构化输入与 preflight 摘要。
2. 展示 Skill hash、plan-review ledger、DeepSeek 诊断、完整 code diff/log/attempt/version 谱系。
3. 移除或明确标注实验图中的 recent-six 截断；展示文献角色、完整 Idea 与负结果路径。
4. 映射真实错误类别，提供 pause/terminate/resume 的独立 UI 状态。

### P3 — 边界与文档

1. 将 runtime Skill 与外来/手工 Skill 隔离，自动生成 Skill map/lock。
2. 将历史 Round 报告移出生产阅读路径并标明状态，更新 README/architecture docs。
3. 记录环境与依赖变更账本，但不要自动升级核心依赖。

## 14. Files Expected To Change In Future

以下仅为预计修改清单，本审计未修改它们：

```text
backend/app/models/run.py
backend/app/api/runs.py
backend/app/api/providers.py
backend/app/workflow/engine.py
backend/app/workflow/orchestrator.py
backend/app/workflow/dataset_inspection.py
backend/app/workflow/knowledge.py
backend/app/workflow/research_synthesis.py
backend/app/workflow/skills.py
backend/app/workflow/skill_runtime.py
backend/app/providers/llm.py
backend/app/providers/experiment.py
backend/app/storage/repository.py
backend/app/agents/{planner,critic,writer,hypothesis,experiment}.py
frontend/src/{App.tsx,api/client.ts,api/types.ts}
frontend/src/components/{ProjectSettingsModal,ExperimentPanel,ResearchPage,IdeaPage,ReportPreview,researchViewModel}.ts[x]
skills/ (production runtime boundary and task-specific Skill contracts)
tests/backend/ (new preflight, append-only, baseline, analyzer and route tests)
frontend/tests/ (new interaction and error mapping tests)
docs/ (generated runtime Skill map and current production architecture)
```

## 15. Proposed Acceptance Tests

1. **Production-route test**：前端仅通过 `/api/runs` 完成创建、启动、选择 Idea、继续、停止、报告下载；确认 v2 没有隐式参与。
2. **Preflight matrix**：无效 model、HTTP 400、错误 key、DeepSeek unavailable、CUDA unavailable、磁盘不足、数据目录错误、repo 不可读各自生成明确诊断，且不创建半初始化研究步骤。
3. **Dataset matrix**：classification、forecasting、anomaly detection 各一份真实小数据，断言 DatasetProfile 包含并冻结相应 label/time/anomaly/split 语义；后续代码/repair 不得改变指纹或合同。
4. **Constraint propagation test**：每一个 Plan、Bundle、repair、review request 均引用同一 constraints Artifact ID；修改禁止字段必须被拒绝。
5. **Four-Idea evolution test**：固定四候选；每 Idea 完成一次正常负实验才可 v1->v2；至多 v3；工程错误不增加版本；v3 后切换下一 Idea。
6. **Baseline fairness/reproduction test**：相同 split/seed/epoch/protocol 下进行 paired baseline-vs-Idea；论文复现误差大于 10% 时阻止 Idea，最终报告区分 paper/local baseline。
7. **Result Analyzer test**：给定多 seed 原始指标，断言 mean/std/paired delta/median/CI/effect size/direction consistency/noise 皆确定性且不依赖 LLM。
8. **Append-only recovery test**：中断在每个九步节点、每次工程 repair、每个 Idea 版本；恢复后历史 Artifact ID、代码、日志、审查和结果均仍存在。
9. **Skill boundary test**：production route 只能加载 lock 中的 Skill，拒绝 `Bash(*)`/WebSearch/MCP Skill；三个任务只加载对应 common+task Skill。
10. **Frontend contract test**：点击 pause/terminate/resume、查看日志/代码/诊断、全部文献/Idea/版本、Word/ZIP 下载，均有真实 API 请求及错误反馈；下载端点不得 404。

## Evidence Basis and Limitations

结论优先基于 production imports、route decorators、React API 调用、workflow branches、repository semantics 和测试命名/断言。README 与历史 Round 报告未被当作能力证明。未启动新 Run、未调用真实模型或文献服务、未运行训练；因此“运行时真实供应商当前可用性”不在本报告的断言范围内。
