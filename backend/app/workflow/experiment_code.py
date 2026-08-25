from __future__ import annotations

import ast
import hashlib
import json
import posixpath
import re
import sys
from typing import Any

from pydantic import ValidationError

from backend.app.models.experiment import ExperimentBundle, ExperimentFile, ExperimentManifest
from backend.app.workflow.dataset_catalog import normalize_dataset_name, supported_dataset_names
from backend.app.workflow.dataset_inspection import contract_canonical_name
from backend.app.workflow.experiment_bundle import result_id_for


STANDARD_MODULES = {
    "__future__",
    "argparse",
    "collections",
    "csv",
    "dataclasses",
    "datetime",
    "functools",
    "hashlib",
    "itertools",
    "json",
    "math",
    "os",
    "pathlib",
    "platform",
    "random",
    "re",
    "shutil",
    "statistics",
    "subprocess",
    "sys",
    "time",
    "typing",
} | set(getattr(sys, "stdlib_module_names", ()))

# System-owned modules that the runtime Harness provides next to train.py.
# They are part of the deterministic protocol surface, never third-party pip
# dependencies, so an ``import gewu_runtime`` must not trigger a requirement.
SYSTEM_OWNED_MODULES = {"gewu_runtime"}

REQUIREMENT_IMPORT_ALIASES = {
    "pillow": "PIL",
    "scikit-learn": "sklearn",
}


class ExperimentBundleValidationError(ValueError):
    """Static Bundle validation failure containing every detectable issue."""

    def __init__(self, issues: list[str]) -> None:
        self.issues = list(dict.fromkeys(issue for issue in issues if issue))
        super().__init__("; ".join(self.issues))


def validate_relative_file_path(path: str) -> str:
    normalized = posixpath.normpath(str(path).replace("\\", "/"))
    if normalized in {"", "."} or normalized == ".." or normalized.startswith("../") or posixpath.isabs(normalized):
        raise ValueError(f"EXPERIMENT_CODE_PATH_INVALID:{path}")
    return normalized


def normalize_experiment_code(raw: dict[str, Any], task: dict[str, Any], python_command: str) -> dict[str, Any]:
    entrypoint = validate_relative_file_path(str(raw.get("entrypoint") or "train.py"))
    if entrypoint != "train.py":
        raise ValueError(f"EXPERIMENT_CODE_ENTRYPOINT_INVALID:{entrypoint}")

    raw_files = raw.get("files") or []
    if not raw_files:
        source = raw.get("code")
        if isinstance(source, str) and source.strip():
            raw_files = [{"path": entrypoint, "content": source}]

    files = []
    for item in raw_files:
        path = validate_relative_file_path(str(item.get("path") or ""))
        content = str(item.get("content") or "")
        if not content.strip():
            raise ValueError(f"EXPERIMENT_CODE_FILE_EMPTY:{path}")
        files.append({"path": path, "content": content})

    if not any(item["path"] == entrypoint for item in files):
        raise ValueError(f"EXPERIMENT_CODE_ENTRYPOINT_MISSING:{entrypoint}")

    seed = int(task.get("seed") or 7)
    metrics_path = str(raw.get("metrics_path") or task.get("metrics_path") or f"results/run_seed_{seed}.json")
    log_path = str(raw.get("log_path") or task.get("log_path") or f"logs/run_seed_{seed}.log")
    return {
        "entrypoint": entrypoint,
        "files": files,
        "command": str(raw.get("command") or f"{python_command} {entrypoint} --seed {seed} --output {metrics_path}"),
        "metrics_path": metrics_path,
        "log_path": log_path,
        "assumptions": list(raw.get("assumptions") or []),
        "validation": dict(raw.get("validation") or {}),
    }


def default_mock_experiment_code(task: dict[str, Any], python_command: str) -> dict[str, Any]:
    seed = int(task.get("seed") or 7)
    metrics_path = str(task.get("metrics_path") or f"results/run_seed_{seed}.json")
    log_path = str(task.get("log_path") or f"logs/run_seed_{seed}.log")
    content = '''from __future__ import annotations

import argparse
import json
import random
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    random.seed(args.seed)
    baseline = [0, 1, 1, 0, 1, 0]
    predictions = [(value if random.random() > 0.1 else 1 - value) for value in baseline]
    accuracy = sum(int(a == b) for a, b in zip(baseline, predictions)) / len(baseline)
    metrics = {"accuracy": accuracy, "seed": args.seed}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(metrics), encoding="utf-8")
    print(json.dumps(metrics))


if __name__ == "__main__":
    main()
'''
    return normalize_experiment_code(
        {
            "entrypoint": "train.py",
            "files": [{"path": "train.py", "content": content}],
            "metrics_path": metrics_path,
            "log_path": log_path,
            "assumptions": ["Development fallback code computes metrics from a deterministic toy baseline."],
            "validation": {"requires_network": False, "expected_metrics": ["accuracy"]},
        },
        task,
        python_command,
    )


def normalize_experiment_bundle(
    raw: dict[str, Any],
    run_id: str,
    experiment_id: str,
    task: dict[str, Any],
    *,
    validate_source: bool = True,
) -> ExperimentBundle:
    result_id = result_id_for(experiment_id)
    entrypoint = validate_relative_file_path(str(raw.get("entrypoint") or "train.py"))
    raw_files = raw.get("files") or []
    if isinstance(raw_files, dict):
        raw_files = [{"path": path, "content": content} for path, content in raw_files.items()]
    if not raw_files:
        for key in ("code", "source", "content"):
            value = raw.get(key)
            if isinstance(value, str) and value.strip():
                raw_files = [{"path": entrypoint, "content": value}]
                break
    if not raw_files:
        raise ValueError(
            "EXPERIMENT_CODE_FILES_MISSING: return a files array containing an object with "
            f"path '{entrypoint}' and the complete Python source as a content_lines array."
        )
    files = [ExperimentFile.model_validate(_normalize_bundle_file(item)) for item in raw_files]
    args = [str(item) for item in raw.get("python_args") or []]
    output_path = f"results/{result_id}.json"
    for flag, value in (
        ("--run-id", run_id),
        ("--experiment-id", experiment_id),
        ("--result-id", result_id),
        ("--output", output_path),
    ):
        args = _set_argument(args, flag, value)
    requirements = [str(item).strip() for item in raw.get("requirements") or []]
    if any(not _valid_requirement(item) for item in requirements):
        raise ValueError("EXPERIMENT_REQUIREMENT_INVALID")
    manifest = ExperimentManifest(
        run_id=run_id,
        experiment_id=experiment_id,
        result_id=result_id,
        entrypoint=entrypoint,
        python_args=args,
        requires_gpu=bool(raw.get("requires_gpu")),
        dataset=_declared_dataset(raw, task, files),
        dataset_contract_id=str(
            ((task.get("plan") or {}).get("dataset") or {}).get("contract_id") or ""
        ),
        dataset_fingerprint=str(
            ((task.get("plan") or {}).get("dataset") or {}).get(
                "content_fingerprint"
            )
            or ""
        ),
        expected_metrics=[str(item) for item in raw.get("expected_metrics") or []],
        parameters=dict(raw.get("parameters") or {}),
        seeds=[int(item) for item in raw.get("seeds") or []],
        supports_smoke_test=bool(raw.get("supports_smoke_test")),
    )
    bundle = ExperimentBundle(
        manifest=manifest,
        files=files,
        requirements=requirements,
    )
    if validate_source:
        validate_experiment_bundle_source(bundle)
    return bundle


def _normalize_bundle_file(raw_file: Any) -> dict[str, Any]:
    if not isinstance(raw_file, dict):
        raise ValueError("EXPERIMENT_CODE_FILE_INVALID: each files item must be an object")
    item = dict(raw_file)
    if "content_lines" in item:
        if item.get("content") not in (None, ""):
            raise ValueError(
                "EXPERIMENT_CODE_CONTENT_AMBIGUOUS: return content_lines without content"
            )
        lines = item.pop("content_lines")
        if not isinstance(lines, list) or not lines or any(
            not isinstance(line, str) for line in lines
        ):
            raise ValueError(
                "EXPERIMENT_CODE_LINES_INVALID: content_lines must be a non-empty array of strings"
            )
        if any("\n" in line or "\r" in line for line in lines):
            raise ValueError(
                "EXPERIMENT_CODE_LINES_INVALID: each content_lines item must contain exactly one "
                "physical source line without newline characters"
            )
        item["content"] = "\n".join(lines).rstrip("\n") + "\n"
    if isinstance(item.get("content"), str):
        item["sha256"] = hashlib.sha256(item["content"].encode("utf-8")).hexdigest()
    return item


def default_mock_experiment_bundle(
    run_id: str,
    experiment_id: str,
    task: dict[str, Any],
) -> ExperimentBundle:
    seed = int(task.get("seed") or 7)
    source = '''from __future__ import annotations

import argparse
import json
import random
from pathlib import Path


parser = argparse.ArgumentParser()
parser.add_argument("--seed", type=int, default=7)
parser.add_argument("--run-id", required=True)
parser.add_argument("--experiment-id", required=True)
parser.add_argument("--result-id", required=True)
parser.add_argument("--output", required=True)
args = parser.parse_args()
random.seed(args.seed)
metrics = {"accuracy": 0.5 + (args.seed % 10) / 100}
result = {
    "run_id": args.run_id,
    "experiment_id": args.experiment_id,
    "result_id": args.result_id,
    "metrics": metrics,
}
output = Path(args.output)
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps(result), encoding="utf-8")
'''
    return normalize_experiment_bundle(
        {
            "entrypoint": "train.py",
            "files": [{"path": "train.py", "content": source}],
            "python_args": ["--seed", str(seed)],
            "requirements": [],
            "requires_gpu": False,
            "expected_metrics": ["accuracy"],
            "parameters": {"seed": seed},
            "seeds": [seed],
        },
        run_id,
        experiment_id,
        task,
    )


def experiment_validation_issues(exc: Exception) -> list[str]:
    """Convert code-generation failures into supervisor revision issues.

    Returns an empty list when the exception is not a correctable generation
    problem and should propagate instead.
    """
    if isinstance(exc, ExperimentBundleValidationError):
        return list(exc.issues)
    if isinstance(exc, ValidationError):
        issues = []
        for error in exc.errors():
            message = str(error.get("msg") or "")
            message = message.removeprefix("Value error, ")
            if message.startswith("EXPERIMENT_"):
                issues.append(message)
        if issues:
            return issues
        first = str(exc.errors()[0].get("msg", "")) if exc.errors() else str(exc)
        return [f"EXPERIMENT_BUNDLE_INVALID:{first}"]
    if isinstance(exc, json.JSONDecodeError):
        return [
            "EXPERIMENT_CODE_GENERATION_INVALID: the provider response was not valid JSON. "
            "Return one complete JSON object matching the schema hint."
        ]
    message = str(exc)
    if message.startswith("STRUCTURED_OUTPUT_NOT_OBJECT"):
        # The provider returned a valid JSON value that is not an object (e.g. a
        # bare files array or scalar).  This is a correctable generation problem:
        # feed it back and let the repair/regenerate loop retry, so one malformed
        # draw cannot hard-fail the whole step.
        return [
            "EXPERIMENT_CODE_GENERATION_INVALID: the provider returned a JSON value "
            "that is not a single object (e.g. a bare array or scalar) instead of one "
            "complete object matching the schema hint; return the full object with all "
            "top-level keys."
        ]
    if message.startswith("EXPERIMENT_"):
        return [message]
    return []


def _set_argument(args: list[str], flag: str, value: str) -> list[str]:
    cleaned = list(args)
    while flag in cleaned:
        index = cleaned.index(flag)
        del cleaned[index : min(index + 2, len(cleaned))]
    cleaned.extend([flag, value])
    return cleaned


def _valid_requirement(value: str) -> bool:
    return bool(value) and bool(re.fullmatch(r"[A-Za-z0-9_.-]+(?:\[[A-Za-z0-9_,.-]+\])?(?:[<>=!~]+[A-Za-z0-9.*+!-]+)?", value))


def _declared_dataset(raw: dict[str, Any], task: dict[str, Any], files: list[ExperimentFile]) -> str:
    plan_dataset = (task.get("plan") or {}).get("dataset") or {}
    if plan_dataset.get("contract_id"):
        declared = str(raw.get("dataset") or "").strip()
        planned_name = contract_canonical_name(plan_dataset)
        if not planned_name and declared == str(plan_dataset["contract_id"]):
            declared = ""
        declared_name = normalize_dataset_name(declared) if declared else ""
        if declared and (not planned_name or declared_name != planned_name):
            raise ValueError(
                "EXPERIMENT_LOCAL_DATASET_SUBSTITUTION_FORBIDDEN:"
                f"expected={planned_name or '(generic-local)'}:declared={declared_name or declared}:"
                f"contract={plan_dataset['contract_id']}:root={plan_dataset.get('root', '')}"
            )
        # A supported public dataset may also be a verified local binding. Keep
        # its canonical name as provenance while the contract id and DATA_ROOT
        # remain authoritative for the actual files.
        return declared_name or planned_name
    declared = raw.get("dataset")
    if declared:
        name = normalize_dataset_name(declared)
        if not name:
            raise ValueError(
                f"EXPERIMENT_DATASET_UNSUPPORTED:{declared}. Supported datasets: "
                f"{', '.join(supported_dataset_names())}. Declare one of these or use synthetic data."
            )
        return name
    # The accepted plan is authoritative even when generated source does not
    # contain a recognizable torchvision constructor.  This prevents an
    # omitted model field from erasing provenance or enabling a silent dataset
    # substitution later in the runtime.
    plan = task.get("plan") or {}
    for candidate in ((plan.get("dataset") or {}).get("canonical_name"), plan.get("dataset"), task.get("dataset")):
        name = normalize_dataset_name(candidate)
        if name:
            return name
    return ""


def validate_experiment_bundle_source(bundle: ExperimentBundle) -> None:
    entrypoint = next(
        item for item in bundle.files if item.path == bundle.manifest.entrypoint
    )
    source = entrypoint.content
    combined = "\n".join(item.content for item in bundle.files)
    issues: list[str] = []
    for item in bundle.files:
        if item.path.endswith(".py"):
            issues.extend(_python_source_issues(item.path, item.content))
    if re.search(r"\bdownload\s*=\s*True\b", combined):
        issues.append("EXPERIMENT_BUNDLE_RUNTIME_DOWNLOAD_FORBIDDEN")
    if _uses_external_torchvision_dataset(combined):
        if not bundle.manifest.dataset:
            issues.append(
                "EXPERIMENT_BUNDLE_EXTERNAL_DATASET_FORBIDDEN: declare one supported dataset "
                f"({', '.join(supported_dataset_names())}) in the bundle's dataset field, "
                "or generate deterministic synthetic data instead."
            )
        if not _uses_data_root_env(combined):
            issues.append(
                "EXPERIMENT_BUNDLE_DATASET_ROOT_INVALID: load the declared dataset with "
                "root=os.environ['DATA_ROOT'] and download=False; the runtime provisions "
                "the dataset under DATA_ROOT before execution."
            )

    declared_modules = {_requirement_module(item) for item in bundle.requirements}
    imported_modules = _imported_modules(source)
    missing = sorted(
        module
        for module in imported_modules
        if module not in STANDARD_MODULES
        and module not in SYSTEM_OWNED_MODULES
        and module not in declared_modules
    )
    if missing:
        issues.extend(f"EXPERIMENT_REQUIREMENT_MISSING:{module}" for module in missing)

    # Metric names may be built dynamically.  Static validation only establishes
    # that the program can serialize a metrics/result object; the runtime result
    # contract verifies expected_metrics against actual output.
    if bundle.manifest.supports_smoke_test and not _has_metrics_result_mechanism(source):
        issues.append(
            "EXPERIMENT_METRIC_RESULT_MECHANISM_MISSING:"
            "train.py must construct metrics and serialize a result payload"
        )
    if _has_nested_literal_metric_values(source):
        issues.append(
            "EXPERIMENT_METRIC_VALUE_INVALID:"
            "metrics values must be finite scalar numbers, not nested mappings or booleans"
        )

    if bundle.manifest.requires_gpu and not _uses_cuda_execution(source):
        issues.append("EXPERIMENT_BUNDLE_CUDA_USAGE_MISSING")

    if bundle.manifest.supports_smoke_test:
        hand_rolled_smoke = "--smoke-test" in source and re.search(
            r"\b[A-Za-z_][A-Za-z0-9_]*\.smoke_test\b", source
        )
        # The scaffold owns the --smoke-test flag: gr.get_args() parses it and
        # gr.is_smoke()/gr.epoch_budget() drive the smoke branch, so generated
        # code needs no literal ``args.smoke_test`` reference.
        if not (hand_rolled_smoke or _uses_gewu_scaffold(source)):
            issues.append(
                "EXPERIMENT_SMOKE_TEST_PROTOCOL_INVALID:"
                "train.py must accept --smoke-test and branch on args.smoke_test"
            )
        issues.extend(smoke_data_reduction_issues(source))
    if issues:
        raise ExperimentBundleValidationError(issues)


def smoke_data_reduction_issues(source: str) -> list[str]:
    """Reject the legacy smoke-only head slice without a broader code analyzer."""
    issues = []
    for variable in ("X", "y", "dataset", "data"):
        if re.search(
            rf"\b{variable}\s*=\s*{variable}\s*\[\s*:\s*[^\]]+\]",
            source,
        ):
            issues.append(
                f"EXPERIMENT_SMOKE_DATA_REDUCTION_FORBIDDEN:{variable}"
            )
    return issues


def _has_metrics_result_mechanism(source: str) -> bool:
    hand_rolled = bool(
        re.search(r"\bmetrics\b", source)
        and re.search(r"json\.(?:dump|dumps)\s*\(", source)
        and re.search(r"\b(?:output|metrics_path)\b", source)
    )
    # The system scaffold owns serialization; generated code only needs to call
    # gr.write_result(...).  Static source cannot see inside it, so its use
    # satisfies the result-payload mechanism check.
    scaffold = bool(re.search(r"\b(?:gewu_runtime|gr)\.\s*write_result\s*\(", source))
    return hand_rolled or scaffold


def _uses_gewu_scaffold(source: str) -> bool:
    """True when generated code imports and drives the deterministic scaffold."""
    if "gewu_runtime" not in source:
        return False
    return bool(
        re.search(
            r"\b(?:gewu_runtime|gr)\.\s*(?:get_args|is_smoke|epoch_budget|write_result|data_root|expected_metrics|seeds)\s*\(",
            source,
        )
    )


def _has_nested_literal_metric_values(source: str) -> bool:
    """Reject only statically provable non-scalar metric values.

    Dynamic metric keys remain supported; runtime validation remains authoritative
    for all values that cannot be determined from the source AST.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "metrics" for target in node.targets):
            continue
        if not isinstance(node.value, ast.Dict):
            continue
        for value in node.value.values:
            if isinstance(value, (ast.Dict, ast.List, ast.Set)):
                return True
            if isinstance(value, ast.Constant) and isinstance(value.value, bool):
                return True
    return False


def _validate_python_source(path: str, source: str) -> None:
    """Compatibility wrapper for direct callers of the former fail-fast helper."""
    issues = _python_source_issues(path, source)
    if issues:
        raise ExperimentBundleValidationError(issues)


def _python_source_issues(path: str, source: str) -> list[str]:
    issues: list[str] = []
    if "\ufffd" in source or r"\ufffd" in source:
        issues.append(
            f"EXPERIMENT_CODE_ENCODING_INVALID:{path}: source contains a Unicode "
            "replacement marker; regenerate the complete file"
        )
    if source.count("\n") == 0 and source.count(r"\n") >= 2:
        issues.append(
            f"EXPERIMENT_CODE_ENCODING_INVALID:{path}: source contains literal \\n "
            "separators instead of real line breaks; encode JSON newlines exactly once"
        )
    try:
        tree = ast.parse(source, path)
    except SyntaxError as exc:
        issues.append(
            f"EXPERIMENT_CODE_SYNTAX_INVALID:{path}:line={exc.lineno}:column={exc.offset}:"
            f"{exc.msg}"
        )
        return issues
    invalid_api_patterns = {
        r"\btorch\.softplus\s*\(": "torch.nn.functional.softplus",
    }
    for pattern, replacement in invalid_api_patterns.items():
        if re.search(pattern, source):
            issues.append(
                f"EXPERIMENT_CODE_API_INVALID:{path}:torch.softplus does not exist; use {replacement}"
            )
    compact_source = re.sub(r"\s+", " ", source)
    unique_probability = re.search(
        r"(?P<name>p_[A-Za-z0-9_]+)\s*=\s*"
        r"(?:[A-Za-z0-9_]+\.astype\([^)]*\)|[A-Za-z0-9_]+)\s*/",
        compact_source,
    )
    if (
        "np.unique(" in source
        and "return_counts=True" in source
        and unique_probability
        and re.search(
            rf"for\s+\w+\s+in\s+range\([^)]*\).*"
            rf"{re.escape(unique_probability.group('name'))}\s*\[\s*\w+\s*\]",
            compact_source,
        )
    ):
        issues.append(
            "EXPERIMENT_CODE_PROBABILITY_SHAPE_UNSAFE:"
            f"{path}: probabilities derived from np.unique counts cannot be indexed by a "
            "full bin/class range; build fixed-size marginals from the joint table instead"
        )
    if re.search(
        r"(?P<clone>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*"
        r"[A-Za-z_][A-Za-z0-9_]*\([^;\n]*\).*?"
        r"(?P=clone)\.load_state_dict\(\s*"
        r"[A-Za-z_][A-Za-z0-9_]*\.state_dict\(\)\s*\)",
        source,
        re.DOTALL,
    ):
        issues.append(
            "EXPERIMENT_CODE_MODEL_CLONE_UNSAFE:"
            f"{path}: do not reconstruct a trained model with positional constructor "
            "arguments before load_state_dict; use copy.deepcopy(model) for ablation clones"
        )
    unsafe_numpy_lines = _unsafe_tensor_numpy_lines(tree)
    if unsafe_numpy_lines:
        issues.append(
            "EXPERIMENT_CODE_TENSOR_NUMPY_UNSAFE:"
            f"{path}:line={unsafe_numpy_lines[0]}: detach tensors before NumPy conversion "
            "with tensor.detach().cpu().numpy()"
        )
    unsafe_reversed_argsort = re.search(
        r"np\.argsort\([^\n]+\)\s*\[\s*::\s*-1\s*\](?!\.copy\(\))",
        source,
    )
    if unsafe_reversed_argsort:
        line = source.count("\n", 0, unsafe_reversed_argsort.start()) + 1
        issues.append(
            "EXPERIMENT_CODE_NUMPY_STRIDE_UNSAFE:"
            f"{path}:line={line}: reversed NumPy index arrays passed to PyTorch must "
            "be made contiguous with [::-1].copy()"
        )
    if re.search(r"\bfor\s+\w*epoch\w*\s+in\s+range\s*\(", source, re.IGNORECASE):
        has_epoch_event = bool(re.search(r"['\"]epoch_end['\"]", source))
        has_total_epochs = bool(re.search(r"['\"]total_epochs['\"]", source))
        if not (has_epoch_event and has_total_epochs and "flush=True" in source):
            issues.append(
                "EXPERIMENT_CODE_PROGRESS_MISSING:"
                f"{path}: epoch loops must emit JSON epoch_end progress with total_epochs and flush=True"
            )
    return issues


def _unsafe_tensor_numpy_lines(tree: ast.AST) -> list[int]:
    """Accept semantic detach-to-CPU-to-NumPy chains, including safe temporaries."""
    safe_names: set[str] = set()
    unsafe_lines: list[int] = []

    def is_safe(value: ast.AST) -> bool:
        if isinstance(value, ast.Name):
            return value.id in safe_names
        if isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute):
            if value.func.attr == "detach":
                return True
            if value.func.attr in {"cpu", "to"}:
                return is_safe(value.func.value)
        return False

    def assigned_names(target: ast.AST) -> list[str]:
        if isinstance(target, ast.Name):
            return [target.id]
        if isinstance(target, (ast.Tuple, ast.List)):
            return [name for item in target.elts for name in assigned_names(item)]
        return []

    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = [name for target in targets for name in assigned_names(target)]
            if value is not None and is_safe(value):
                safe_names.update(names)
            else:
                safe_names.difference_update(names)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "numpy":
            if not is_safe(node.func.value):
                unsafe_lines.append(node.lineno)
    return sorted(set(unsafe_lines))


def _imported_modules(source: str) -> set[str]:
    modules: set[str] = set()
    for match in re.finditer(
        r"^\s*(?:import|from)\s+([A-Za-z_][A-Za-z0-9_.]*)",
        source,
        re.MULTILINE,
    ):
        modules.add(match.group(1).split(".", 1)[0])
    return modules


def _requirement_module(requirement: str) -> str:
    name = re.split(r"[<>=!~\[]", requirement, maxsplit=1)[0].strip()
    return REQUIREMENT_IMPORT_ALIASES.get(name.lower(), name.replace("-", "_"))


def _uses_cuda_execution(source: str) -> bool:
    lower = source.lower().replace(" ", "")
    has_cuda_device = (
        "torch.device('cuda" in lower
        or 'torch.device("cuda' in lower
        or ".cuda(" in lower
        or ".to('cuda" in lower
        or '.to("cuda' in lower
    )
    has_cuda_runtime = "torch.cuda" in lower
    moves_work_to_device = (
        "device=device" in lower
        or ".to(device" in lower
        or ".cuda(" in lower
        or ".to('cuda" in lower
        or '.to("cuda' in lower
    )
    return has_cuda_runtime and (has_cuda_device or moves_work_to_device) and moves_work_to_device


def _uses_data_root_env(source: str) -> bool:
    return bool(
        re.search(
            r"os\s*\.\s*(?:environ\s*\[\s*['\"]DATA_ROOT['\"]\s*\]"
            r"|environ\s*\.\s*get\s*\(\s*['\"]DATA_ROOT['\"]"
            r"|getenv\s*\(\s*['\"]DATA_ROOT['\"])",
            source,
        )
    )


def _uses_external_torchvision_dataset(source: str) -> bool:
    forbidden = (
        "datasets.CIFAR",
        "datasets.MNIST",
        "datasets.FashionMNIST",
        "datasets.ImageNet",
        "datasets.ImageFolder",
        "torchvision.datasets.CIFAR",
        "torchvision.datasets.MNIST",
        "torchvision.datasets.FashionMNIST",
        "torchvision.datasets.ImageNet",
        "torchvision.datasets.ImageFolder",
    )
    return any(token in source for token in forbidden)
