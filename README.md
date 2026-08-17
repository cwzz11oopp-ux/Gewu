# Gewu

Official project root: `D:\Gewu`.

这是一个以 Qwen 为推理模型、以 Supervisor 为独立调度 Agent 的九步科研工作台。项目面向 GitHub 拉取后的本地部署，也保留通用 SSH 远程 GPU，未加入 ECS 专用接口。

Supervisor 静态分配仓库中的 `skills/<skill-id>/SKILL.md`，检查每一步输出并记录 Skill、工具权限和指令哈希。Research、Idea、Planning、Experiment、Diagnostic、Critic、Reviewer、Writer Agent 只执行被授权的任务；具体分配见 [Agent architecture](docs/agent_architecture.md)。

完整的逐步 Skill 名单见 [Runtime Skill Map](docs/skills.md)。

Skill 是运行时执行协议，不只是轨迹标签。一个 Pipeline step 包含多个原子 Skill 时，后端会在对应操作前只加载该原子 Skill 的完整指令。例如实验阶段依次执行 `run-experiment`、`analyze-results` 和 `experiment-audit`；反馈阶段先执行 `result-to-claim`，只有结论为 `partial` 或 `failed` 且仍允许跟进时，才加载 `research-refine`、`experiment-plan` 和 `ablation-planner` 生成下一轮计划。所有模型输出仍须通过确定性契约和配置的 Reviewer 审查。

## 快速体验

要求 Python 3.12，以及 Node.js 20.19+ 或 22.12+。首次克隆后执行：

```powershell
git clone <repository-url> D:\Gewu
cd D:\Gewu
.\scripts\setup.ps1
.\scripts\start.ps1
```

Linux 或 macOS：

```bash
git clone <repository-url>
cd <repository-directory>
chmod +x scripts/setup.sh scripts/start.sh
./scripts/setup.sh
./scripts/start.sh
```

首次安装会从 `.env.demo.example` 创建 `.env`，使用无需 API Key 和 GPU
的演示 Provider。浏览器打开 `http://127.0.0.1:5173`。

## 真实 Qwen 与 GPU 配置

需要真实科研流程时，将 `.env.example` 复制为 `.env`，填写 Qwen API Key
和实验 Provider。`.env` 会在后端启动时自动加载，并且不会提交到 Git。

GPU 实验依赖必须安装到实际运行实验的同一个 Python 环境：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements\experiment.txt
.\.venv\Scripts\python.exe -c "import torch; print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else '')"
```

如果 PyTorch 安装包与显卡驱动不匹配，请按目标机器的平台和 CUDA 条件安装对应的 PyTorch 构建，再执行上面的检查。

## 启动

终端 1 启动后端：

```powershell
cd D:\Gewu
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

终端 2 启动前端：

```powershell
cd frontend
pnpm install --frozen-lockfile
pnpm run dev
```

后端启动后会占用当前终端，这是正常状态。安装依赖或运行其他命令时请打开第二个终端。

## 实验运行

本机 GPU 指运行后端的那台机器上的 GPU。设置中填写：

```text
EXPERIMENT_PROVIDER=local_gpu
LOCAL_GPU_ENABLED=true
LOCAL_EXPERIMENT_WORKDIR=experiments
LOCAL_GPU_PYTHON=D:\path\to\project\.venv\Scripts\python.exe
LOCAL_GPU_CUDA_VISIBLE_DEVICES=0
```

`CUDA_VISIBLE_DEVICES` 填设备索引，例如 `0` 或 `0,1`，不能填显卡型号 `5070`。配置检查会使用所填 Python 返回 Python、PyTorch、CUDA 和设备名称。

远程运行只依赖通用 SSH，适用于 AutoDL 或自建 Linux GPU 服务器：

```text
EXPERIMENT_PROVIDER=remote_gpu
REMOTE_GPU_HOST=host
REMOTE_GPU_USER=user
REMOTE_GPU_PORT=22
REMOTE_GPU_SSH_KEY_PATH=C:\path\to\id_ed25519
REMOTE_GPU_PROJECT_DIR=/workspace/ai-scientist-experiments
REMOTE_GPU_PYTHON=/workspace/venv/bin/python
REMOTE_GPU_CUDA_VISIBLE_DEVICES=0
```

本机与 SSH 使用同一个 `ExperimentBundle`，部署到 `<project>/<run_id>/<experiment_id>`。实验 ID 为 `experiment_1`，对应结果 ID 为 `experiment_1_result`；参数、seed 和重试记录保存在制品内容中，不进入 ID。运行器不会自动安装依赖，缺少依赖时会返回针对所配置 Python 的安装命令。

### 数据集来源

实验计划使用公开数据集（当前支持 CIFAR-10、CIFAR-100、MNIST、Fashion-MNIST）时，生成的代码从环境变量 `DATA_ROOT` 指向的共享缓存加载数据，代码本身永远不联网（`download=True` 会被拒绝）。缓存的填充方式二选一：

```text
EXPERIMENT_DATASET_SOURCE=online   # 默认。缺失时由后端受控下载一次到缓存目录
EXPERIMENT_DATASET_DIR=datasets    # 共享缓存目录，本机为后端相对路径，SSH 为 <project_dir>/_datasets
```

- `online`：运行前后端检查缓存，缺失时用实验 Python 环境执行一次 torchvision 下载，之后所有 Run 复用同一份缓存。
- `local`：完全离线。你需要提前把数据集放到缓存目录（例如 CIFAR-10 需存在 `datasets/cifar-10-batches-py/`）；缺失时实验会以 `EXPERIMENT_DATASET_LOCAL_MISSING` 失败并提示放置路径。

实验执行、分析或审计失败时，独立的 Diagnostic Agent 会输出错误类型、错误代码、根因、证据、修复动作和下一步。对于已知的下载中断或校验失败，它只把该数据集目录下白名单中的未完成下载移动到 `.failed-downloads` 隔离区，再自动重新下载；初次执行后最多自动修复两次。依赖缺失、CUDA/驱动、凭据及未知错误只给出诊断，不会自动修改系统环境。该流程是故障处理，不属于 Reviewer 审核。

两个选项也可以在前端「服务器配置」面板中切换。

实验设计（research_plan）开始前，后端会先生成一份数据集可用性清单（`cached` / `downloadable` / `missing`）连同每个数据集的数据卡（输入形状、类别数、划分大小、归一化统计）一起提供给规划模型；`local` 模式下选择了缺失数据集的计划会在规划阶段被直接驳回。数据卡随计划传递到代码生成阶段，生成的模型结构必须与数据形状、类别数一致。

## 本地文献

文献区可上传 PDF、TXT 或 Markdown，支持哈希查重、解析状态、DOI/arXiv 验证、关联当前研究和加入 Research Wiki。没有可检索论文时 Wiki 查询返回空结果和状态码，不会伪造论文或使流程崩溃。

## 验证

```powershell
.\.venv\Scripts\python.exe -m pytest tests\backend -q
node --test frontend\tests\ui-contract.test.mjs
pnpm --dir frontend run build
```

真实 GPU 冒烟测试为可选项，见 [Runbook](docs/runbook.md)。

## 仓库边界

`datasets/`、`backend/data/`、`outputs/`、`experiments/`、`tmp/`、`.env`
以及本地依赖目录均不会提交。克隆者使用在线数据源时由后端按需填充
数据集缓存；离线模式的目录格式见 [Runbook](docs/runbook.md)。
