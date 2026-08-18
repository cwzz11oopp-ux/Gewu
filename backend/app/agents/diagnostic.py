from __future__ import annotations

import re

from backend.app.providers.llm import LLMProvider


_KNOWN_FAILURES = {
    "EXPERIMENT_DATASET_DOWNLOAD_FAILED": {
        "category": "dataset",
        "root_cause": "数据集下载中断或下载文件未通过完整性校验。",
        "retryable": True,
        "auto_repairable": True,
        "repair_action": "quarantine_corrupt_dataset_download",
        "repair_scope": "configured dataset cache",
        "user_message": "数据集下载不完整；诊断专家将隔离损坏缓存并重新下载。",
        "next_action": "隔离当前数据集的已知下载文件，然后重新执行数据集预下载。",
    },
    "EXPERIMENT_DATASET_DOWNLOAD_TIMEOUT": {
        "category": "dataset",
        "root_cause": "数据集下载超过实验运行超时时间。",
        "retryable": True,
        "auto_repairable": True,
        "repair_action": "quarantine_corrupt_dataset_download",
        "repair_scope": "configured dataset cache",
        "user_message": "数据集下载超时；诊断专家将隔离未完成下载并重试。",
        "next_action": "隔离未完成下载并重试；重复失败时改为人工放置数据集。",
    },
    "EXPERIMENT_DATASET_LOCAL_MISSING": {
        "category": "dataset",
        "root_cause": "当前为离线数据集模式，但配置的缓存中没有所需数据集。",
        "retryable": False,
        "auto_repairable": False,
        "repair_action": "none",
        "repair_scope": "none",
        "user_message": "离线缓存缺少数据集，需要人工放置数据或切换为 online。",
        "next_action": "将数据集放入提示的缓存目录，或在项目设置中启用 online 数据源。",
    },
    "LOCAL_EXPERIMENT_DEPENDENCY_MISSING": {
        "category": "dependency",
        "root_cause": "实验 Python 环境缺少生成代码声明的依赖。",
        "retryable": False,
        "auto_repairable": False,
        "repair_action": "none",
        "repair_scope": "none",
        "user_message": "实验依赖缺失；为避免修改环境，诊断专家不会自动安装依赖。",
        "next_action": "按错误中的安装命令补齐依赖后重试实验运行。",
    },
    "LOCAL_EXPERIMENT_CUDA_UNAVAILABLE": {
        "category": "gpu",
        "root_cause": "配置的实验 Python 未检测到可用 CUDA 设备。",
        "retryable": False,
        "auto_repairable": False,
        "repair_action": "none",
        "repair_scope": "none",
        "user_message": "CUDA 不可用；诊断专家不会自动修改驱动或系统环境。",
        "next_action": "检查实验 Python、PyTorch CUDA 构建和 CUDA_VISIBLE_DEVICES。",
    },
    "LOCAL_EXPERIMENT_CUDA_PROBE_FAILED": {
        "category": "gpu",
        "root_cause": "CUDA 环境探测命令执行失败。",
        "retryable": False,
        "auto_repairable": False,
        "repair_action": "none",
        "repair_scope": "none",
        "user_message": "CUDA 探测失败，需要检查本机实验环境。",
        "next_action": "在配置的实验 Python 中运行 CUDA 检查命令并修复环境。",
    },
    "LOCAL_EXPERIMENT_RUN_FAILED": {
        "category": "generated_code",
        "root_cause": "生成的实验代码在真实运行时抛出异常。",
        "retryable": True,
        "auto_repairable": True,
        "repair_action": "repair_experiment_code",
        "repair_scope": "current experiment bundle only",
        "user_message": "实验代码运行失败；诊断专家将依据异常定点修复当前 Bundle。",
        "next_action": "冻结实验参数，把错误证据交给代码修复模型，通过快速校验后重试。",
    },
    "EXPERIMENT_BUNDLE_SMOKE_TEST_FAILED": {
        "category": "generated_code",
        "root_cause": "Generated experiment code failed the bounded preflight execution.",
        "retryable": True,
        "auto_repairable": True,
        "repair_action": "repair_experiment_code",
        "repair_scope": "current experiment bundle only",
        "user_message": "The generated code failed smoke testing and will be returned to Qwen for repair before a formal run.",
        "next_action": "Repair the complete Bundle from the smoke traceback, validate it, and repeat smoke testing.",
    },
    "EXPERIMENT_SMOKE_DATA_REDUCTION_FORBIDDEN": {
        "category": "generated_code",
        "root_cause": "The persisted Bundle contains a legacy smoke-only dataset reduction.",
        "retryable": True,
        "auto_repairable": True,
        "repair_action": "repair_experiment_code",
        "repair_scope": "current experiment bundle only",
        "user_message": "旧实验 Bundle 在 Smoke 中缩减了数据；将仅修复当前 Bundle 后重试。",
        "next_action": "删除 Smoke 数据截断并保持完整数据和冻结 split，然后重新执行 Smoke。",
    },
    "EXPERIMENT_AUDIT_FAILED": {
        "category": "generated_code",
        "root_cause": "The executed Bundle did not satisfy one or more independently audited scientific-result requirements.",
        "retryable": True,
        "auto_repairable": True,
        "repair_action": "repair_experiment_code",
        "repair_scope": "current experiment bundle only",
        "user_message": "The formal result failed independent audit; Qwen will repair the cited Bundle requirements before a bounded retry.",
        "next_action": "Repair the cited source/result gaps, preserve the scientific contract, then run smoke and the formal result again.",
    },
    "QWEN_REQUEST_TIMEOUT": {
        "category": "analysis",
        "root_cause": "模型分析或审计请求发生瞬态超时。",
        "retryable": True,
        "auto_repairable": True,
        "repair_action": "retry_stage",
        "repair_scope": "current analysis operation",
        "user_message": "分析模型请求超时；诊断专家将重试当前阶段。",
        "next_action": "保持实验输入不变并重试当前阶段。",
    },
}


class ExperimentDiagnosticAgent:
    name = "Experiment Diagnostic Agent"

    def __init__(self, llm_provider: LLMProvider) -> None:
        self.llm_provider = llm_provider

    def diagnose(
        self,
        error: Exception | str,
        *,
        task: dict,
        bundle: dict,
        attempts: list[dict],
        instructions: str = "",
    ) -> dict:
        message = str(error)
        error_code = message.split(":", 1)[0].strip() or "UNKNOWN_EXPERIMENT_FAILURE"
        known = _KNOWN_FAILURES.get(error_code)
        if known is not None:
            return {
                "error_code": error_code,
                **known,
                "evidence": _error_evidence(message),
            }

        raw = self.llm_provider.generate_json(
            "diagnostic.diagnose_experiment",
            {
                "error_code": error_code,
                "error": _error_evidence(message),
                "task": task,
                "bundle": bundle,
                "attempts": attempts,
            },
            {
                "category": "dataset|dependency|gpu|generated_code|timeout|analysis|audit|configuration|unknown",
                "root_cause": "string",
                "evidence": ["string"],
                "retryable": "boolean",
                "auto_repairable": "boolean",
                "repair_action": "none",
                "repair_scope": "string",
                "user_message": "string",
                "next_action": "string",
            },
            instructions=instructions,
        )
        # Unknown failures are advisory only. A model cannot grant itself a
        # mutation capability that is not backed by a deterministic classifier.
        return {
            "category": str(raw.get("category") or "unknown"),
            "error_code": error_code,
            "root_cause": str(raw.get("root_cause") or "尚未识别到确定根因。"),
            "evidence": _string_list(raw.get("evidence")) or _error_evidence(message),
            "retryable": bool(raw.get("retryable")),
            "auto_repairable": False,
            "repair_action": "none",
            "repair_scope": "none",
            "user_message": str(raw.get("user_message") or "实验失败，诊断专家未找到可安全自动执行的修复。"),
            "next_action": str(raw.get("next_action") or "查看错误证据并人工处理。"),
        }


def _error_evidence(message: str) -> list[str]:
    lines = []
    for raw_line in message.splitlines():
        line = raw_line.strip()
        if not line or re.fullmatch(r"\d+(?:\.\d+)?%", line):
            continue
        lines.append(line[:500])
    if not lines:
        return [message[:500]] if message else []
    return list(dict.fromkeys([lines[0], *lines[-4:]]))


def _string_list(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]
