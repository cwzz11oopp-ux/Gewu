# AI Scientist 本地文献、Research Wiki 与真实实验运行时 TDD

日期：2026-07-14

状态：设计已确认，等待实现计划

部署边界：GitHub 本地部署 + 本机 GPU + 通用 SSH 远程 GPU

## 1. 文档目的

本文同时承担两项职责：

1. 作为 Technical Design Document，记录本轮讨论发现的问题、确认的决策、系统边界、数据契约和验收标准。
2. 作为后续 Test-Driven Development 的测试依据。实现必须先添加失败测试，再修改生产代码。

本文是本轮功能的唯一设计基线，并取代尚未完成的 `2026-07-13-supervisor-agent-static-skills-design.md` 中不正确的扁平 Skill 分配。

## 2. 已发现问题与确认结果

| 编号 | 问题 | 当前事实 | 确认结果 |
| --- | --- | --- | --- |
| P1 | Skill 是否像 Superpowers 一样真正参与运行 | 当前主要把 Skill 文本注入 Qwen，上层仍由 `WorkflowEngine` 固定调用 Provider；`allowed-tools` 只是文档 | 增加可审计的 SkillRuntime，静态分配 Skill，并执行工具权限交集 |
| P2 | Supervisor 是否是独立 Agent | 当前 Supervisor 改造未完成，已有 3 个 workflow 路由测试失败 | Supervisor 只负责规划、分配、验收、退回和提交，不执行领域任务 |
| P3 | 九步流程的 Skill 分配不合理 | `ablation-planner`、`paper-writing`、复合 pipeline 等被放在错误步骤 | 使用本文第 5 节的静态分配；复合编排 Skill 不作为普通步骤 Skill |
| P4 | `research-wiki` 未成为文献检索能力 | 仓库只有 Skill 文档，没有应用层 Wiki 存储或调用接口 | 文献检索前查询 Wiki，验收后写回 Wiki |
| P5 | 空 Wiki 是否报错 | 现有应用没有定义 | 空 Wiki 返回正常空结果和 `WIKI_EMPTY` 警告，继续外部检索 |
| P6 | 本地文献上传 | 当前没有上传、解析、去重、列表或加入 Wiki 的接口 | 新增项目级 LiteratureLibrary 和前端上传能力 |
| P7 | 本地文件与已验证引用混淆 | `EvidenceCard.exportable` 只看现有验证字段，没有本地文献生命周期 | 本地文件可供阅读和进入 Wiki；只有 DOI/arXiv 等完成验证后才可导出为比赛引用 |
| P8 | 实验代码是否真实生成并运行 | 当前已生成 `train.py` 并由本地子进程或 SSH 执行 | 保留真实执行，改为统一实验包和结构化 manifest |
| P9 | 实验与结果 ID 关系 | 当前 Artifact ID 随机，缺少稳定的实验组标识 | Run 内使用 `experiment_1` 和 `experiment_1_result`，参数不进入 ID |
| P10 | 本地工作目录 | 当前改造可以创建目录，但不同 Run 会共享同一根目录 | 在配置根目录下按 `run_id/experiment_id` 隔离 |
| P11 | 本地 CUDA 配置 | 当前 Provider 没有把 `LOCAL_GPU_CUDA_VISIBLE_DEVICES` 传给子进程 | 使用显式环境变量；配置值必须是 `0` 或 `0,1` 等设备索引 |
| P12 | GPU 实验真实性 | 当前返回码成功就可能被标记为真实实验，没有证明 CUDA 被使用 | manifest 要求 GPU 时必须完成 CUDA 预检并记录实际设备 |
| P13 | 结果读取不可靠 | 当前解析 stdout 最后一行；已有生成脚本只写 JSON 文件而不打印 | 以 manifest 指定的结果文件为唯一结果来源，stdout 仅作为日志 |
| P14 | 依赖环境 | 当前不生成可靠依赖契约，也不自动准备本地或远程环境 | 每个实验包包含 `requirements.txt`；默认不自动安装，缺失时返回准确命令 |
| P15 | SSH 能力边界 | 用户不采用 ECS，但可能使用 AutoDL 或自建服务器 | 保留通用 SSH Provider，不包含任何 ECS 或云厂商专用逻辑 |
| P16 | GitHub 分发缺失 Skill | `.gitignore` 忽略 `skills/`，且当前 Git 跟踪的 Skill 数量为 0 | 正式 Skill 必须进入版本控制；运行时数据和密钥继续忽略 |
| P17 | `.agents` 的用途 | 当前 `.agents` 为空且不参与应用运行 | 应用 Agent 位于 `backend/app/agents`；`.agents` 仅在未来需要外部 Codex 配置时使用 |
| P18 | 当前本机环境损坏 | `.venv` 指向已删除的 Python，原 PyTorch 是 CPU 构建 | 本机已修复为 Python 3.12.13 和 CUDA 13.2 PyTorch，并完成 RTX 5070 实算验证 |

## 3. 目标和非目标

### 3.1 目标

- GitHub 克隆后，用户安装依赖即可在本地启动前后端。
- Skill 以明确的运行时契约参与九步流程，而不只是名称标签。
- Supervisor 对每步输入、Agent、Skill、工具和输出进行审计。
- 用户可上传本地 PDF、Markdown 和 TXT 文献。
- 本地文献可加入当前 Run，也可在验收后加入 Research Wiki。
- Wiki 为空、未初始化或暂时不可用时，外部检索仍可继续。
- 本地 GPU 和通用 SSH 使用同一个实验包契约。
- 实验代码、参数、环境、日志和结果可复现并能稳定关联。
- 比赛报告只能使用已验证引用和真实实验结果。

### 3.2 非目标

- 不部署 ECS，不调用任何云厂商实例管理 API。
- 不开发用户电脑常驻 Worker、消息队列或公网反向任务通道。
- 不在首版加入向量数据库或嵌入模型。
- 不在首版对扫描 PDF 执行 OCR。
- 不默认自动执行 `pip install`，尤其不在远程服务器上擅自修改环境。
- 不让 ResearchAgent 或其他领域 Agent 直接提交长期 Wiki 变更。
- 不把论文上传成功等同于引用真实性验证成功。

## 4. 部署与运行边界

项目以 GitHub 仓库分发。典型使用方式如下：

```text
GitHub repository
  -> clone to user machine
  -> install backend/frontend dependencies
  -> start frontend and backend locally
  -> choose one execution provider
       local_gpu  -> GPU on the backend machine
       remote_gpu -> generic SSH target such as AutoDL or a self-hosted server
       mock       -> development only
```

`local_gpu` 的“本地”永远是后端进程所在机器。如果用户在远程服务器上启动后端，`local_gpu` 使用该服务器设备；它不会跨网络使用另一台电脑的 GPU。

`remote_gpu` 只依赖标准 SSH、可写项目目录、Python 和目标环境中的实验依赖。它不假定服务器来自哪家厂商。

## 5. Supervisor、Agent 和 SkillRuntime

### 5.1 Supervisor 职责

新增独立 Supervisor Skill：

```text
skills/ai-scientist-supervisor/SKILL.md
```

SupervisorAgent 只拥有应用级调度工具：

- `read_run`
- `read_artifact`
- `load_skill`
- `dispatch_agent`
- `validate_artifact`
- `request_revision`
- `update_step`
- `append_event`
- `commit_wiki_changes`

Supervisor 不拥有文献网络检索、实验子进程、SSH 或报告写作等领域工具。

### 5.2 静态 Skill 分配

| 工作流步骤 | Agent | 主 Skill | 固定能力或条件 Skill |
| --- | --- | --- | --- |
| `problem_understanding` | ResearchAgent | `problem-framing` | 无 |
| `knowledge_integration` | ResearchAgent | `research-lit` | `research-wiki`、本地文献库、外部文献 Provider |
| `hypothesis_generation` | IdeaAgent | `idea-creator` | Wiki 只读 query pack |
| `evidence_reasoning` | CriticAgent | `novelty-check` | `research-review` |
| `research_plan` | PlanningAgent | `research-refine` | `experiment-plan` |
| `experiment_task` | ExperimentAgent | `experiment-implementation` | 实验包构建工具 |
| `experiment_run_analysis` | ExperimentAgent | `run-experiment` | `analyze-results`、`experiment-audit`；训练监控为条件能力 |
| `feedback_revision` | CriticAgent | `result-to-claim` | 仅在失败或部分成立时使用 `ablation-planner` |
| `report_export` | WriterAgent | `competition-report` | 引用审计和结果审计 |

以下复合编排 Skill 不作为普通步骤 Skill 注入：

- `idea-discovery`
- `research-pipeline`
- `research-refine-pipeline`
- `experiment-bridge`
- `auto-review-loop*`
- `paper-writing`
- `patent-pipeline`
- `dse-loop`

如静态分配依赖的原子 Skill 不存在，则在实现阶段创建最小、单一职责的 Skill，不复用语义不匹配的复合 Skill。

### 5.3 SkillRuntime

SkillRuntime 负责：

1. 从 `skills/<skill_id>/SKILL.md` 加载完整 frontmatter 和指令。
2. 校验 Skill ID、Agent 和步骤的静态关系。
3. 将 Skill 声明的工具名映射到后端注册工具。
4. 计算实际权限交集。
5. 在事件中记录 Skill 版本、指令哈希、工具调用和输出摘要。
6. 在超过上下文预算时按明确章节裁剪并记录，不静默截断。

实际工具权限为：

```text
Skill declared tools
intersection Agent allowed tools
intersection registered backend tools
intersection configured and approved tools
```

### 5.4 输出验收与重试

每步执行顺序：

```text
Supervisor reads state
-> selects fixed Agent and Skills
-> dispatches domain work
-> validates deterministic output contract
-> runs isolated semantic review when required
-> commits Artifact and proposed Wiki changes
-> otherwise requests a targeted revision
```

- 内容输出最多修订 2 次。
- 实验运行故障最多进行 3 次诊断尝试。
- 超过限制后步骤进入 `blocked`，保留完整诊断记录。
- Reviewer 需要独立上下文时只接收 Artifact/Wiki 文件路径，不接收 Supervisor 的预先总结。

## 6. 本地文献库

### 6.1 存储结构

LiteratureLibrary 使用项目 `DATA_DIR`，默认结构：

```text
backend/data/literature/
  index.json
  files/
    paper_<sha256-prefix>.pdf
  text/
    paper_<sha256-prefix>.txt
```

运行时数据继续被 Git 忽略。仓库可提供不含用户论文的空目录初始化逻辑。

### 6.2 文献模型

每个 LocalDocument 至少包含：

```json
{
  "id": "paper_ab12cd34ef56",
  "filename": "paper.pdf",
  "media_type": "application/pdf",
  "sha256": "...",
  "size_bytes": 1234,
  "title": "...",
  "authors": [],
  "year": null,
  "abstract": "...",
  "identifiers": {"doi": "", "arxiv": ""},
  "source": "local_upload",
  "statuses": ["uploaded", "parsed"],
  "verification": {
    "verified": false,
    "provider": "",
    "verified_at": null
  },
  "wiki_node_id": null,
  "run_ids": []
}
```

状态含义：

- `uploaded`：文件已安全写入。
- `parsed`：提取出可用文本。
- `metadata_ready`：核心元数据完整。
- `verified`：DOI/arXiv 等经外部来源验证。
- `wiki_ingested`：Supervisor 已提交 Wiki 节点。

### 6.3 上传与安全

首版支持 PDF、Markdown 和 TXT。上传必须：

- 使用流式读取和大小上限，默认 30 MB。
- 依据实际内容和允许类型校验，不只相信扩展名。
- 使用 SHA-256 去重。
- 使用服务端生成的 ID 和路径，禁止客户端路径进入文件系统。
- PDF 使用结构化解析库提取文本。
- 扫描 PDF 可保存，但返回 `LITERATURE_TEXT_EXTRACTION_EMPTY`，不伪造全文。
- 文本送入模型前按确定性预算切片，不把整篇长论文直接塞入上下文。

### 6.4 API

```text
POST   /api/literature/documents
GET    /api/literature/documents
GET    /api/literature/documents/{paper_id}
DELETE /api/literature/documents/{paper_id}

POST /api/literature/documents/{paper_id}/verify
POST /api/literature/documents/{paper_id}/wiki
POST /api/runs/{run_id}/literature/{paper_id}/attach
```

上传接口为 `multipart/form-data`，可同时提交用户修正的元数据、当前 `run_id` 和 `add_to_wiki` 意图。

删除本地文件不会隐式删除 Wiki 节点。Wiki 历史只能显式归档，避免破坏旧 Run 的证据关系。

### 6.5 前端

“B 文献检索与验证”区域增加：

- 上传图标按钮和文件选择器。
- 本地、Wiki、外部来源标识。
- 解析状态和验证状态。
- “加入当前研究”和“加入 Wiki”命令。
- 上传进度、重复文件和解析错误提示。

未验证本地论文不得显示为“已验证文献”。

## 7. Research Wiki

### 7.1 定位

`research-wiki` 是 ResearchAgent 在文献整合步骤中的固定能力，不是独立 Agent，也不是额外流程步骤。

Wiki 是项目级长期知识库，Paper、Idea、Experiment、Claim 和 Gap 节点可跨 Run 复用；每个节点必须记录来源 Run、主题标签和来源 Skill，查询时按主题相关性过滤。

### 7.2 查询顺序

```text
query Research Wiki
-> query Local LiteratureLibrary
-> search external literature providers
-> deduplicate by hash, DOI, arXiv and normalized title
-> rank and synthesize evidence candidates
-> Supervisor validates
-> attach evidence to Run
-> Supervisor commits Wiki changes
```

Wiki 和本地库不能替代最近 3 至 6 个月的外部检索。

### 7.3 空库与降级契约

Wiki 目录不存在时自动初始化。Wiki 没有论文或没有主题匹配时返回：

```json
{
  "status": "empty",
  "papers": [],
  "gaps": [],
  "failed_ideas": [],
  "query_pack": null,
  "warnings": ["WIKI_EMPTY"]
}
```

`WIKI_EMPTY` 是状态，不是异常。ResearchAgent 必须继续外部检索。

Wiki 文件损坏、权限失败或图关系冲突时返回 `WIKI_DEGRADED`，记录诊断并继续外部检索。只有用户明确要求 Wiki 操作本身必须成功时，才阻塞该命令。

### 7.4 写入边界

ResearchAgent 返回 `wiki_changes` 提案，包含待写 Paper、Gap 和 Edge。Supervisor 验证引用真实性、重复项、节点 ID 和关系后执行提交。

关系唯一事实源为 `graph/edges.jsonl`。所有变更追加审计日志。`query_pack.md` 采用确定性生成并限制在 8000 字符以内。失败或部分成立的 Idea 不得被自动清理。

检索完成后最多写入 8 至 12 篇最高相关且通过验收的论文。

## 8. 文献工作流整合

`knowledge_integration` 不再只执行一次 `literature_provider.search(limit=5)`。新流程必须：

1. 读取结构化研究问题和全部检索查询。
2. 查询 Wiki 和本地库。
3. 对每个关键查询执行外部搜索。
4. 对候选引用调用验证工具。
5. 去重、排序并保存来源解释。
6. 输出包含 verified 与 local-only 条目的 evidence artifact。
7. 只有 verified 条目进入可导出引用集合。
8. 由 Supervisor 提交 Wiki 更新。

上传发生在文献步骤之后时，用户可附加到当前 Run 并从 `knowledge_integration` 重新运行。其他 Run 不受影响。

## 9. 统一实验包和 ID

### 9.1 目录与 ID

每个 Run 内实验编号从 1 开始：

```text
<experiment_workdir>/
  <run_id>/
    experiment_1/
      manifest.json
      requirements.txt
      train.py
      environment.json
      logs/
        experiment_1.log
      results/
        experiment_1_result.json
```

稳定关系：

```json
{
  "run_id": "run_xxx",
  "experiment_id": "experiment_1",
  "result_id": "experiment_1_result"
}
```

参数、种子、比较组和重复次数存入 manifest 和结果内容，不进入 ID。同一实验的重新运行追加 attempt 记录；不同研究 Run 的编号和目录完全隔离。

### 9.2 manifest 契约

```json
{
  "schema_version": 1,
  "run_id": "run_xxx",
  "experiment_id": "experiment_1",
  "result_id": "experiment_1_result",
  "entrypoint": "train.py",
  "python_args": [
    "--seed", "42",
    "--init", "lecun",
    "--output", "results/experiment_1_result.json"
  ],
  "requirements_file": "requirements.txt",
  "requires_gpu": true,
  "expected_metrics": ["test_accuracy"],
  "parameters": {},
  "seeds": [42]
}
```

Provider 使用参数数组启动进程，不再使用 `command.split()`。所有相对路径必须归一化并限制在实验目录内。

### 9.3 依赖策略

ExperimentAgent 生成 `requirements.txt`，但 Provider 默认不自动安装。

预检失败时返回结构化错误和准确命令：

```text
<configured-python> -m pip install -r requirements.txt
```

用户安装后可重新运行同一个 `experiment_id`。安装动作不能由模型在无授权情况下执行。

## 10. LocalGpuRunner

本地运行步骤：

1. 创建 `<run_id>/<experiment_id>`。
2. 安全写入实验包并计算代码哈希。
3. 校验 Python 可执行文件和工作目录。
4. 检查 requirements 的关键导入。
5. 检查 `torch.cuda.is_available()`、设备数量和设备名称。
6. 将配置的 `CUDA_VISIBLE_DEVICES` 写入子进程环境。
7. 使用参数数组执行入口文件。
8. 将 stdout/stderr 写入日志。
9. 从 manifest 指定的结果文件读取 JSON。
10. 校验预期指标、数值合法性和实验 ID。
11. 写入环境、代码哈希、时间、退出码和 attempt。

配置中的 CUDA 设备必须是设备索引，如 `0` 或 `0,1`，不能是型号 `5070`。

manifest 声明 `requires_gpu=true` 时，如果 CUDA 不可用，返回 `LOCAL_GPU_CUDA_UNAVAILABLE`，不得降级为 CPU 后仍标记为 GPU 实验。

## 11. SshExperimentRunner

SSH Runner 与本地 Runner 消费相同实验包。运行步骤：

1. 使用超时和非交互参数检查 SSH 连接。
2. 校验远程目录、Python 和写权限。
3. 检查 `nvidia-smi`、PyTorch CUDA 和目标依赖。
4. 创建 `<remote_project_dir>/<run_id>/<experiment_id>`。
5. 上传实验包并校验每个文件哈希。
6. 设置远程 `CUDA_VISIBLE_DEVICES` 并执行参数数组对应的安全命令。
7. 读取远程结果文件和日志。
8. 将结果、日志和环境信息回收到本地 Run Artifact。
9. 保留远程目录以支持复现。

远程 Python 应允许指向独立环境，例如：

```text
/root/autodl-tmp/ai-scientist/.venv/bin/python
```

SSH 实现不得包含 ECS 专用字段或 API。主机可以是 AutoDL、实验室服务器或用户自建服务器。

## 12. 真实实验判定

只有以下条件全部成立时，结果才能设置 `is_real_experiment=true`：

- 实际本地子进程或 SSH 命令已成功完成。
- 部署文件哈希与 manifest 一致。
- 结果文件存在且是合法 JSON 对象。
- `run_id`、`experiment_id` 和 `result_id` 匹配。
- manifest 要求的指标存在且数值合法。
- 环境和执行时间已记录。
- manifest 要求 GPU 时，实际环境确认 CUDA 可用并记录设备。

stdout 文本、LLM 总结或手工填写的指标不能单独证明实验真实运行。

## 13. GitHub 分发与依赖

必须修改 `.gitignore`，使正式 `skills/` 进入版本控制。继续忽略：

- `.env` 和 API 密钥。
- `backend/data/` 中的用户论文、Wiki、Run 和本地设置。
- `outputs/`、实验日志、结果和缓存。
- `.venv/`、`node_modules/` 和构建产物。

依赖文件按职责拆分：

```text
requirements/base.txt
requirements/literature.txt
requirements/experiment-common.txt
```

根 README 提供 Windows 和 Linux 的创建虚拟环境、安装后端、安装前端、配置本机 GPU 和配置通用 SSH 的步骤。

PyTorch GPU wheel 与操作系统、Python、驱动和 CUDA 平台相关，不在跨平台基础 requirements 中硬编码唯一 wheel。配置测试接口应显示检测结果和建议的安装命令。PyTorch 官方建议按目标平台选择 CUDA 构建并使用 `torch.cuda.is_available()` 验证。

上传论文、Wiki 和实验结果默认不提交 Git。需要分享时使用后续显式导出能力，而不是意外提交用户数据。

## 14. 错误契约

新增或稳定以下错误码：

### 文献

- `LITERATURE_FILE_UNSUPPORTED`
- `LITERATURE_FILE_TOO_LARGE`
- `LITERATURE_DUPLICATE`
- `LITERATURE_PARSE_FAILED`
- `LITERATURE_TEXT_EXTRACTION_EMPTY`
- `LITERATURE_METADATA_INVALID`
- `LITERATURE_NOT_FOUND`
- `LITERATURE_REFERENCE_UNVERIFIED`

### Wiki

- `WIKI_EMPTY`，警告，不阻塞外部检索。
- `WIKI_DEGRADED`，警告并降级。
- `WIKI_NODE_CONFLICT`
- `WIKI_EDGE_INVALID`
- `WIKI_COMMIT_REJECTED`

### 实验

- `EXPERIMENT_MANIFEST_INVALID`
- `EXPERIMENT_CODE_PATH_INVALID`
- `EXPERIMENT_DEPENDENCY_MISSING`
- `EXPERIMENT_RESULT_MISSING`
- `EXPERIMENT_RESULT_INVALID`
- `EXPERIMENT_RESULT_ID_MISMATCH`
- `EXPERIMENT_METRIC_MISSING`
- `LOCAL_GPU_CUDA_UNAVAILABLE`
- `REMOTE_GPU_SSH_FAILED`
- `REMOTE_GPU_CUDA_UNAVAILABLE`
- `REMOTE_EXPERIMENT_DEPLOY_FAILED`
- `REMOTE_EXPERIMENT_RESULT_FETCH_FAILED`

API 将稳定错误放入结构化 `detail`，前端显示可操作信息，不直接显示无法阅读的长 traceback。

## 15. Test-Driven Development 计划

实现顺序必须先测试后生产代码。

### 15.1 基线修复

- 修复当前 3 个失败的 workflow Skill 路由测试。
- 删除或重写与错误扁平映射绑定的测试。
- 保证现有用户未提交改动不被覆盖。

### 15.2 Supervisor 和 SkillRuntime 测试

- 九步静态 Agent/Skill 分配。
- 复合 Skill 不进入普通步骤。
- Skill 不存在时在领域工作前失败。
- 工具权限交集。
- Skill 指令哈希和工具调用进入 Event。
- 输出契约失败时退回修改。
- 内容最多 2 次、实验诊断最多 3 次。
- Wiki 写入只有 Supervisor 能提交。

### 15.3 文献与 Wiki 测试

- PDF、Markdown、TXT 上传。
- 不支持类型和超大文件拒绝。
- SHA-256、DOI、arXiv 和标题去重。
- PDF 文本提取和空文本状态。
- 本地文献列表、详情、附加 Run 和删除。
- 未验证本地文献不能进入可导出引用。
- Wiki 目录不存在时自动初始化。
- 空 Wiki 返回 `WIKI_EMPTY` 并继续外部检索。
- 损坏 Wiki 返回 `WIKI_DEGRADED` 并继续外部检索。
- Supervisor 验收后写入 Paper、Gap、Edge 和日志。
- `query_pack.md` 不超过 8000 字符。
- 当前 Run 的本地文献不污染无关 Run。

### 15.4 实验测试

- `experiment_1` 和 `experiment_1_result` 稳定关联。
- 不同 Run 目录隔离。
- 参数和种子保存在 manifest，不进入 ID。
- 参数数组保留包含空格的 Python 路径。
- 本地 Runner 传递 `CUDA_VISIBLE_DEVICES`。
- GPU 必需但 CUDA 不可用时阻止执行。
- 从结果文件读取 JSON，不依赖 stdout 最后一行。
- 结果 ID 和预期指标校验。
- 缺失依赖返回准确安装命令。
- SSH 预检、上传、哈希、命令、结果和日志回收使用 mock subprocess 测试。
- SSH 配置中不出现 ECS 专用字段。
- 真实 GPU smoke test 使用显式 marker，默认单元测试不要求 GPU。
- SSH 集成测试只有用户提供目标服务器配置时运行。

### 15.5 前端测试

- 上传按钮使用文件图标和正确的 multipart 请求。
- 显示本地/Wiki/外部来源和验证状态。
- 未验证论文不会计入“已验证”数量。
- 上传、重复、解析和 Wiki 错误可见。
- 实验区域显示 experiment/result 关系、Provider、设备和结果来源。
- 本地 CUDA 字段提示设备索引而不是型号。

## 16. 验收标准

功能完成必须同时满足：

1. GitHub 仓库包含运行所需的正式 Skills。
2. 新克隆环境可按 README 完成依赖安装和本地启动。
3. Supervisor 对九步执行产生可审计的 Agent、Skill 和工具记录。
4. 空 Wiki 不会使文献步骤失败。
5. 用户可上传本地文献、附加到 Run，并在验收后加入 Wiki。
6. 未验证本地论文不会进入比赛可导出引用。
7. 本机 GPU 能执行统一实验包并产生文件型结果。
8. 通用 SSH 能向配置的 AutoDL 或自建服务器部署同一实验包并回收结果。
9. `experiment_1` 与 `experiment_1_result` 在目录、manifest、Artifact 和 UI 中一致。
10. 报告只使用已验证引用和满足真实实验判定的结果。
11. 所有后端、前端和构建测试通过。
12. GPU 与 SSH 无配置时，普通开发测试仍可运行。

## 17. 当前本机修复记录

本轮设计期间已经完成环境级修复，作为后续真实实验验证基线：

- `.venv` 基解释器从已删除的 Python 3.12.10 切换到可用的 Python 3.12.13。
- `torch` 从 CPU 构建替换为 `2.13.0+cu132`。
- `torchvision` 从 CPU 构建替换为 `0.28.0+cu132`。
- `torch.cuda.is_available()` 返回 `True`。
- 检测设备为 NVIDIA GeForce RTX 5070。
- 已在 `cuda:0` 上执行矩阵乘法并验证结果为有限值。
- 本地 `cuda_visible_devices` 从错误的 `5070` 修正为 `0`。
- 相关后端配置/API 测试通过 21 项。

这些环境变更不提交到 Git，也不能替代 GitHub 用户自己的 Python/PyTorch 安装步骤。
