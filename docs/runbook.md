# AI Scientist Runbook

## 运行边界

- GitHub 拉取后在本地启动前端和后端。
- 本机 GPU 是运行后端进程的机器上的 GPU。
- 通用 SSH 可连接 AutoDL 或自建 GPU 服务器。
- 不使用 ECS API，也不要求网页服务器回连个人电脑。
- 实验代码由 Qwen 生成 `ExperimentBundle`，运行器负责部署、预检、执行和验证结果文件。

## 启动与终端

后端：

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

看到 `Uvicorn running on http://127.0.0.1:8000` 后，该终端正在运行服务器，不能继续输入普通命令。另开 PowerShell 终端执行安装或测试。

前端：

```powershell
cd frontend
pnpm install --frozen-lockfile
pnpm run dev
```

## 本机 GPU 配置

1. 将 `LOCAL_GPU_PYTHON` 或设置页的 Python 命令指向实际安装实验依赖的解释器。
2. 将工作目录设为 `experiments` 或其他可写路径；目录可以不存在，保存或运行时会创建。
3. CUDA 设备索引填写 `0`、`1` 或 `0,1`。
4. 点击“测试配置”，核对 Python 版本、PyTorch 版本、CUDA 可用性和设备名称。

依赖必须安装到同一个解释器：

```powershell
D:\path\to\.venv\Scripts\python.exe -m pip install -r requirements\experiment.txt
D:\path\to\.venv\Scripts\python.exe -c "import numpy, torch, torchvision, torchinfo; print(torch.__version__, torch.cuda.is_available())"
```

运行器不会自动安装实验依赖。`LOCAL_EXPERIMENT_DEPENDENCY_MISSING` 后附带的命令应在第二个终端执行，完成后重新测试配置并重新运行实验步骤。

## 通用 SSH 配置

远端必须能通过非交互 SSH 登录，项目目录可写，并且配置的远程 Python 已安装实验依赖。配置检查使用 `BatchMode=yes`，因此密码登录应改为 SSH Key，或先在系统 SSH 配置中完成认证。

远端 Bundle 目录：

```text
<REMOTE_GPU_PROJECT_DIR>/<run_id>/<experiment_id>/
  manifest.json
  requirements.txt
  environment.json
  train.py
  logs/<experiment_id>.log
  results/<experiment_id>_result.json
```

依赖缺失时，执行错误信息给出的命令；其中 `-r` 后是该实验 Bundle 的绝对 `requirements.txt` 路径。SSH Provider 不自动安装。

## 结果

stdout/stderr 只写日志。成功结果必须来自 JSON 文件，并包含：

```json
{
  "run_id": "run_x",
  "experiment_id": "experiment_1",
  "result_id": "experiment_1_result",
  "metrics": {"accuracy": 0.9}
}
```

ID 不一致、缺少预期指标、非数值或非有限数值都会拒绝该结果。GPU 必需的 Bundle 在 CUDA 不可用时不会静默降级为 CPU 实验。

每次运行前都会删除同 ID 的旧结果文件，避免脚本未写新结果时误用历史指标。旧版自由命令或仅有 `experiment_code` 的 Artifact 不再执行；出现 `EXPERIMENT_BUNDLE_REQUIRED` 时，从“实验任务”步骤重新生成 Bundle。

## 文献与 Wiki

本地文献支持 PDF、TXT、MD。上传后可以：

1. 使用 DOI 或 arXiv 标识验证；
2. 关联当前 Run，作为带本地来源信息的 Evidence；
3. 加入 Research Wiki；
4. 保留“未验证”状态，避免被当作竞赛报告中的已验证引用。

检索顺序是 Wiki、本地文献、外部提供方。Wiki 为空或无匹配论文时返回 `WIKI_EMPTY` 或空结果，流程继续查询其他来源。

## 常见错误

- `Failed to fetch`：前端无法访问 `127.0.0.1:8000`。检查后端终端、端口和防火墙。
- `LOCAL_EXPERIMENT_WORKDIR_INVALID`：工作目录为空、不可创建或不可写。
- `LOCAL_GPU_PYTHON_PROBE_FAILED`：设置中的 Python 不存在，或该环境未安装可导入的 PyTorch。
- `LOCAL_GPU_DEVICE_INDEX_INVALID`：设备索引格式错误或超出探测到的设备数量。
- `LOCAL_EXPERIMENT_DEPENDENCY_MISSING`：生成的 Bundle 声明了当前实验 Python 中缺少的包。
- `EXPERIMENT_CODE_ENTRYPOINT_MISSING`：Bundle 未提供 Manifest 指定的入口文件，应让 Experiment Agent 重新生成。
- `EXPERIMENT_RESULT_MISSING`：脚本没有按 Manifest 的输出路径写结果 JSON。
- `REMOTE_GPU_SSH_FAILED`：SSH 认证、Host、端口、Key 或远程目录检查失败。

## 验证命令

```powershell
.\.venv\Scripts\python.exe -m pytest tests\backend -q
node --test frontend\tests\ui-contract.test.mjs
pnpm --dir frontend run build
```

可选真实 CUDA 冒烟测试：

```powershell
$env:RUN_GPU_SMOKE="1"
.\.venv\Scripts\python.exe -m pytest tests\backend\test_gpu_smoke.py -q
```
