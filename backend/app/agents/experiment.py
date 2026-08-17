import json
import math
import re
from copy import deepcopy

from backend.app.providers.experiment import ExperimentProvider
from backend.app.providers.llm import LLMProvider
from backend.app.models.experiment import ExperimentBundle
from backend.app.workflow.dataset_catalog import dataset_card, normalize_dataset_name
from backend.app.workflow.dataset_inspection import contract_canonical_name
from backend.app.workflow.experiment_harness import compile_bundle_runtime_contract


_BUNDLE_TRANSPORT_INSTRUCTIONS = (
    "Return only a JSON object conforming to the supplied schema. Each files item "
    "must use content_lines as a non-empty array of physical source lines; do not "
    "use a content field, Markdown fence, or explanation. Treat dataset_card as "
    "structured runtime input when it is supplied."
)
from backend.app.workflow.experiment_code import (
    ExperimentBundleValidationError,
    default_mock_experiment_bundle,
    default_mock_experiment_code,
    normalize_experiment_bundle,
    normalize_experiment_code,
    validate_experiment_bundle_source,
)


class ExperimentBundleCandidateError(ValueError):
    """A correctable candidate failure that preserves the model's raw output."""

    def __init__(self, cause: ValueError, candidate: dict) -> None:
        super().__init__(str(cause))
        self.candidate = dict(candidate)


def _validated_comparisons(value, metrics: dict) -> list[dict]:
    runtime_values = [
        float(item)
        for item in metrics.values()
        if isinstance(item, (int, float))
        and not isinstance(item, bool)
        and math.isfinite(float(item))
    ]
    validated = []
    for record in _record_list(value):
        baseline = record.get("baseline_value")
        variant = record.get("variant_value")
        if (
            not isinstance(baseline, (int, float))
            or isinstance(baseline, bool)
            or not math.isfinite(float(baseline))
            or not isinstance(variant, (int, float))
            or isinstance(variant, bool)
            or not math.isfinite(float(variant))
        ):
            continue
        if not any(math.isclose(float(baseline), item, rel_tol=1e-9, abs_tol=1e-12) for item in runtime_values):
            continue
        if not any(math.isclose(float(variant), item, rel_tol=1e-9, abs_tol=1e-12) for item in runtime_values):
            continue
        validated.append({
            "baseline": str(record.get("baseline") or ""),
            "variant": str(record.get("variant") or ""),
            "metric": str(record.get("metric") or ""),
            "baseline_value": baseline,
            "variant_value": variant,
            "difference": float(variant) - float(baseline),
            "interpretation": str(record.get("interpretation") or ""),
        })
    return validated


class ExperimentAgent:
    name = "Experiment Skill"

    def __init__(self, experiment_provider: ExperimentProvider, llm_provider: LLMProvider | None = None) -> None:
        self.experiment_provider = experiment_provider
        self.llm_provider = llm_provider

    def build_task(self, plan: dict) -> dict:
        task = self.experiment_provider.plan(plan, plan.get("experiment_constraints", {"seed": 7}))
        dataset = plan.get("dataset") or {}
        # The plan is authoritative; providers may not select a replacement.
        task["dataset"] = dataset.get("canonical_name") or ""
        task["dataset_contract_id"] = str(dataset.get("contract_id") or "")
        task["dataset_root"] = str(dataset.get("root") or "")
        task.setdefault("plan", plan)
        return task

    def generate_code(self, plan: dict, task: dict, instructions: str, python_command: str) -> dict:
        if self.llm_provider is None:
            return default_mock_experiment_code(task, python_command)
        output_contract = (
            "For experiment.generate_code, return an executable JSON object with entrypoint set to "
            "'train.py' and files set to an array containing exactly one object with path 'train.py' "
            "and content containing the complete Python source. Include command, metrics_path, and log_path. "
            "The command must invoke train.py with --seed, --output, and every required argparse option "
            "declared by the source, using the returned metrics_path and log_path values. Do not return the "
            "source only in a top-level code field, Markdown fence, explanation, or a nested object."
        )
        raw = self.llm_provider.generate_json(
            "experiment.generate_code",
            {"plan": plan, "task": task},
            {
                "entrypoint": "train.py",
                "files": [{"path": "train.py", "content": "python source"}],
                "command": f"{python_command} train.py --seed 7 --output results/run_seed_7.json",
                "metrics_path": "results/run_seed_7.json",
                "log_path": "logs/run_seed_7.log",
            },
            instructions="\n\n".join(part for part in [instructions, output_contract] if part),
        )
        return normalize_experiment_code(raw, task, python_command)

    def generate_bundle(
        self,
        run_id: str,
        experiment_id: str,
        plan: dict,
        task: dict,
        instructions: str,
        python_command: str,
        *,
        require_smoke_test: bool = False,
        validate: bool = True,
        capture: dict | None = None,
    ) -> ExperimentBundle:
        if self.llm_provider is None or self.llm_provider.fallback:
            if (plan.get("dataset") or {}).get("contract_id"):
                raise ValueError(
                    "EXPERIMENT_LOCAL_DATASET_CODE_GENERATION_REQUIRED:"
                    "A bound local dataset cannot fall back to synthetic mock code."
                )
            return compile_bundle_runtime_contract(
                plan, task, default_mock_experiment_bundle(run_id, experiment_id, task)
            )
        card = self._dataset_card_for(plan, task)
        locked_dataset = plan.get("dataset") or {}
        local_contract = locked_dataset.get("contract_id")
        task_summary = {
            key: task[key]
            for key in ("name", "dataset", "dataset_contract_id", "dataset_root", "seed")
            if key in task
        }
        # Agent-local text is limited to the transport representation.  The assigned
        # experiment-implementation Skill is the single source for behavior.
        plan_parameters = dict(plan.get("parameters") or {})
        plan_seeds = [int(item) for item in plan.get("seeds") or []]
        plan_metrics = [
            str(item.get("metric"))
            for item in plan.get("evaluations") or []
            if isinstance(item, dict) and str(item.get("metric") or "").strip()
        ]
        output_contract = "\n\n".join(
            part for part in (
                _BUNDLE_TRANSPORT_INSTRUCTIONS,
                "Accepted Plan fields are immutable runtime inputs. Return these exact "
                f"values in the Bundle: seeds={plan_seeds}; parameters={json.dumps(plan_parameters, ensure_ascii=False, sort_keys=True)}; "
                f"expected_metrics={plan_metrics or ['test_accuracy']}. Do not replace them with schema examples.",
            ) if part
        )
        raw = self.llm_provider.generate_json(
            "experiment.generate_bundle",
            {
                "plan": plan,
                "task": task_summary,
                "locked_dataset": {
                    "canonical_name": contract_canonical_name(locked_dataset),
                    "display_name": locked_dataset.get("display_name") or "",
                    "contract_id": local_contract or "",
                    "data_root": locked_dataset.get("root") or "",
                    "content_fingerprint": locked_dataset.get("content_fingerprint") or "",
                },
                "dataset_card": card,
            },
            {
                "files": [
                    {
                        "path": "train.py",
                        "content_lines": [
                            "from __future__ import annotations",
                            "",
                            "# one JSON string per physical Python line",
                        ],
                    }
                ],
                "requirements": ["numpy", "torch", "torchvision"],
                "requires_gpu": True,
                "dataset": "" if local_contract else "cifar-10",
                "expected_metrics": plan_metrics or ["test_accuracy"],
                "parameters": plan_parameters,
                "seeds": plan_seeds,
                "supports_smoke_test": True,
            },
            instructions="\n\n".join(
                part
                for part in (
                    instructions,
                    self._local_dataset_layout_instructions(plan),
                    (
                        "Locked Dataset\n"
                        f"Canonical Name: {contract_canonical_name(locked_dataset) or '(generic local dataset)'}\n"
                        f"Display Name: {locked_dataset.get('display_name') or ''}\n"
                        f"Contract ID: {local_contract or ''}\n"
                        f"DATA_ROOT: {locked_dataset.get('root') or ''}\n"
                        "This dataset is already locked. Do not substitute or download another dataset; "
                        "use DATA_ROOT as the only data source."
                        if local_contract
                        else ""
                    ),
                    output_contract,
                )
                if part
            ),
        )
        if capture is not None:
            capture["raw_model_output"] = deepcopy(raw)
        try:
            bundle = normalize_experiment_bundle(
                raw,
                run_id,
                experiment_id,
                {**task, "plan": task.get("plan") or plan},
                validate_source=False,
            )
            bundle = compile_bundle_runtime_contract(plan, task, bundle)
        except ValueError as exc:
            raise ExperimentBundleCandidateError(exc, raw) from exc
        if validate:
            self.validate_bundle(
                plan,
                bundle,
                require_smoke_test=require_smoke_test,
            )
        if capture is not None:
            capture["normalized_bundle"] = bundle.model_dump()
        return bundle

    def repair_bundle(
        self,
        plan: dict,
        task: dict,
        bundle: ExperimentBundle | None,
        diagnosis: dict,
        instructions: str,
        validation_feedback: list[str] | None = None,
        repair_history: list[dict] | None = None,
        *,
        validate: bool = True,
        previous_candidate: dict | None = None,
        frozen_contract: dict | None = None,
        capture: dict | None = None,
    ) -> ExperimentBundle:
        """Repair implementation files while freezing the accepted scientific contract."""
        if self.llm_provider is None or self.llm_provider.fallback:
            if (plan.get("dataset") or {}).get("contract_id"):
                raise RuntimeError("EXPERIMENT_CODE_AUTO_REPAIR_UNAVAILABLE")
            return compile_bundle_runtime_contract(
                plan,
                task,
                default_mock_experiment_bundle(
                    str(task.get("run_id") or "run_1"),
                    str(task.get("experiment_id") or "experiment_1"),
                    task,
                ),
            )
        if bundle is None and previous_candidate is None:
            raise ValueError("EXPERIMENT_REPAIR_PREVIOUS_CANDIDATE_REQUIRED")
        feedback = [str(item) for item in validation_feedback or [] if str(item)]
        history = [
            dict(item) if isinstance(item, dict) else {"issue": str(item)}
            for item in repair_history or []
            if item
        ]
        frozen = (
            bundle.manifest.model_dump()
            if bundle is not None
            else dict(frozen_contract or self.frozen_contract_from_candidate(plan, task, previous_candidate or {}))
        )
        previous_files = (
            [item.model_dump() for item in bundle.files]
            if bundle is not None
            else list((previous_candidate or {}).get("files") or [])
        )
        previous_requirements = (
            list(bundle.requirements)
            if bundle is not None
            else [str(item) for item in (previous_candidate or {}).get("requirements") or []]
        )
        raw = self.llm_provider.generate_json(
            "experiment.repair_bundle",
            {
                "plan": plan,
                "task": task,
                "manifest": frozen,
                "files": previous_files,
                "requirements": previous_requirements,
                "previous_candidate": previous_candidate or {},
                "frozen_contract": frozen,
                "diagnosis": diagnosis,
                "validation_feedback": feedback,
                "repair_history": history,
            },
            {
                "files": [
                    {
                        "path": "train.py",
                        "content_lines": [
                            "from __future__ import annotations",
                            "",
                            "# complete repaired source",
                        ],
                    }
                ],
                "requirements": ["numpy", "torch", "torchvision"],
            },
            instructions="\n\n".join(
                part
                for part in (
                    instructions,
                    self._local_dataset_layout_instructions(plan),
                    "Return only the schema-conforming repaired files and requirements. "
                    "The accepted manifest is immutable and will be enforced by the runtime.",
                    (
                        "Previous repair candidates were rejected:\n- " + "\n- ".join(feedback)
                        if feedback
                        else ""
                    ),
                    (
                        "Earlier runtime failures in this experiment iteration:\n- "
                        + "\n- ".join(
                            json.dumps(item, ensure_ascii=False, sort_keys=True)
                            for item in history
                        )
                        if history
                        else ""
                    ),
                )
                if part
            ),
        )
        if capture is not None:
            capture["raw_model_output"] = deepcopy(raw)
        try:
            repaired = normalize_experiment_bundle(
                {
                    "files": raw.get("files"),
                    "requirements": raw.get("requirements") or previous_requirements,
                    "requires_gpu": bool(frozen.get("requires_gpu")),
                    "dataset": str(frozen.get("dataset") or ""),
                    "expected_metrics": list(frozen.get("expected_metrics") or []),
                    "parameters": dict(frozen.get("parameters") or {}),
                    "seeds": list(frozen.get("seeds") or []),
                    "supports_smoke_test": bool(frozen.get("supports_smoke_test")),
                },
                str(frozen.get("run_id") or task.get("run_id") or "run_1"),
                str(frozen.get("experiment_id") or task.get("experiment_id") or "experiment_1"),
                {**task, "plan": task.get("plan") or plan},
                validate_source=False,
            )
            repaired = compile_bundle_runtime_contract(plan, task, repaired)
        except ValueError as exc:
            raise ExperimentBundleCandidateError(exc, raw) from exc
        if validate:
            self.validate_bundle(plan, repaired)
        if capture is not None:
            capture["normalized_bundle"] = repaired.model_dump()
        return repaired

    @staticmethod
    def frozen_contract_from_candidate(plan: dict, task: dict, candidate: dict) -> dict:
        """Freeze the first candidate's scientific fields when it is not normalizable."""
        dataset = plan.get("dataset") or {}
        return {
            "run_id": str(task.get("run_id") or ""),
            "experiment_id": str(task.get("experiment_id") or ""),
            "result_id": str(task.get("result_id") or ""),
            "requires_gpu": bool(candidate.get("requires_gpu")),
            "dataset": str(candidate.get("dataset") or contract_canonical_name(dataset) or ""),
            "dataset_contract_id": str(dataset.get("contract_id") or ""),
            "dataset_fingerprint": str(dataset.get("content_fingerprint") or ""),
            "expected_metrics": [str(item) for item in candidate.get("expected_metrics") or []],
            # A rejected model candidate has no authority to redefine the
            # accepted Plan.  Freezing its schema-example defaults poisons every
            # subsequent repair, so preserve the Plan's scientific inputs here.
            "parameters": dict(plan.get("parameters") or {}),
            "seeds": [int(item) for item in plan.get("seeds") or []],
            "supports_smoke_test": bool(candidate.get("supports_smoke_test")),
        }

    @staticmethod
    def validate_bundle(
        plan: dict,
        bundle: ExperimentBundle,
        *,
        require_smoke_test: bool = False,
    ) -> None:
        """Run the unchanged Bundle gates after generation or repair.

        Keeping this separate lets the workflow pass the exact prior Bundle to
        ``repair_bundle`` after a preflight failure instead of regenerating it.
        """
        issues: list[str] = []
        try:
            validate_experiment_bundle_source(bundle)
        except ExperimentBundleValidationError as exc:
            issues.extend(exc.issues)
        if require_smoke_test and not bundle.manifest.supports_smoke_test:
            issues.append(
                "EXPERIMENT_SMOKE_TEST_PROTOCOL_REQUIRED:"
                "set supports_smoke_test=true and implement --smoke-test"
            )
        issues.extend(ExperimentAgent._bundle_against_plan_issues(plan, bundle))
        compiled = compile_bundle_runtime_contract(plan, {}, bundle).runtime_contract
        if bundle.runtime_contract != compiled:
            issues.append("EXPERIMENT_RUNTIME_CONTRACT_MISMATCH")
        if issues:
            raise ExperimentBundleValidationError(issues)

    @staticmethod
    def _dataset_card_for(plan: dict, task: dict) -> dict:
        dataset = (plan or {}).get("dataset")
        if isinstance(dataset, dict) and isinstance(dataset.get("card"), dict):
            return dataset["card"]
        for candidate in (dataset, (task or {}).get("dataset")):
            name = normalize_dataset_name(candidate)
            if name:
                return dataset_card(name)
        return {}

    @staticmethod
    def _local_dataset_layout_instructions(plan: dict) -> str:
        """Describe verified layouts that differ from a generic torchvision cache."""
        dataset = plan.get("dataset") or {}
        files = {
            str(item.get("relative_path") or "").replace("\\", "/")
            for item in dataset.get("files") or []
            if isinstance(item, dict)
        }
        flat_fashion_mnist = {
            "train-images-idx3-ubyte.gz",
            "train-labels-idx1-ubyte.gz",
            "t10k-images-idx3-ubyte.gz",
            "t10k-labels-idx1-ubyte.gz",
        }
        if contract_canonical_name(dataset) == "fashion-mnist" and flat_fashion_mnist <= files:
            return (
                "Verified local storage layout: the four Fashion-MNIST IDX gzip files are "
                "directly inside DATA_ROOT, not in torchvision's FashionMNIST/raw cache layout. "
                "Do not call torchvision.datasets.FashionMNIST. Implement a local torch Dataset "
                "that reads exactly those gzip IDX image/label files from DATA_ROOT with no download, "
                "copies, moves, symlinks, or fallback data."
            )
        return ""

    def run(self, task: dict, code: dict | ExperimentBundle | None = None) -> dict:
        return self.experiment_provider.run(task, code)

    def analyze_result(
        self,
        plan: dict,
        task: dict,
        result: dict,
        *,
        instructions: str = "",
    ) -> dict:
        if self.llm_provider is None:
            raw = {}
        else:
            raw = self.llm_provider.generate_json(
                "experiment.analyze_results",
                {
                    "plan": plan,
                    "manifest": task.get("manifest") or {},
                    "result": result,
                },
                {
                    "experiment_id": "string",
                    "result_id": "string",
                    "metrics": {"metric_name": "finite number"},
                    "comparisons": [{
                        "baseline": "string",
                        "variant": "string",
                        "metric": "string",
                        "baseline_value": "number|null",
                        "variant_value": "number|null",
                        "difference": "number|null",
                        "interpretation": "string",
                    }],
                    "observations": ["string"],
                    "limitations": ["string"],
                    "verdict": "supported|partial|failed",
                },
                instructions=instructions,
            )
        verdict = str(raw.get("verdict") or "partial").strip().lower()
        if verdict not in {"supported", "partial", "failed"}:
            verdict = "partial"
        metrics = dict(result.get("metrics") or {})
        return {
            "experiment_id": str(result.get("experiment_id") or ""),
            "result_id": str(result.get("result_id") or ""),
            # Metrics are authoritative runtime values.  The model may explain
            # them, but it may not rewrite or invent them.
            "metrics": metrics,
            "comparisons": _validated_comparisons(raw.get("comparisons"), metrics),
            "observations": _string_list(raw.get("observations")),
            "limitations": _string_list(raw.get("limitations")),
            "verdict": verdict,
            "provider_mode": getattr(self.llm_provider, "mode", "deterministic"),
            "fallback_used": bool(getattr(self.llm_provider, "fallback", False)),
        }

    def audit_result(
        self,
        bundle: ExperimentBundle,
        result: dict,
        *,
        instructions: str = "",
    ) -> dict:
        manifest = bundle.manifest
        issues: list[str] = []
        if result.get("run_id") != manifest.run_id:
            issues.append("RUN_ID_MISMATCH")
        if result.get("experiment_id") != manifest.experiment_id:
            issues.append("EXPERIMENT_ID_MISMATCH")
        if result.get("result_id") != manifest.result_id:
            issues.append("RESULT_ID_MISMATCH")

        metrics = result.get("metrics")
        if not isinstance(metrics, dict):
            issues.append("RESULT_METRICS_INVALID")
            metrics = {}
        for metric in manifest.expected_metrics:
            if metric not in metrics:
                issues.append(f"EXPECTED_METRIC_MISSING:{metric}")
        for name, value in metrics.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                issues.append(f"METRIC_NOT_NUMERIC:{name}")
            elif not math.isfinite(float(value)):
                issues.append(f"METRIC_NOT_FINITE:{name}")

        environment = result.get("environment") or {}
        if manifest.requires_gpu and not environment.get("cuda_available"):
            issues.append("GPU_REQUIREMENT_NOT_VERIFIED")
        attempts = list(result.get("attempts") or [])
        if attempts and attempts[-1].get("status") != "completed":
            issues.append("EXPERIMENT_EXIT_NOT_SUCCESSFUL")

        runtime = result.get("runtime") if isinstance(result.get("runtime"), dict) else {}
        # The compiled harness is the formal multi-seed execution boundary.
        # Legacy Bundles keep their established provider contract, while a
        # runtime-bound Bundle must prove its complete per-seed result set.
        formal_runtime = bundle.runtime_contract is not None and bool(
            result.get("is_real_experiment")
        )
        if formal_runtime and runtime.get("mode") != "full":
            issues.append("FORMAL_RUNTIME_MODE_REQUIRED")
        if formal_runtime and manifest.seeds:
            result_seeds = result.get("seeds")
            if result_seeds != manifest.seeds:
                issues.append("FORMAL_SEED_SET_MISMATCH")
            seed_results = result.get("seed_results")
            if not isinstance(seed_results, list):
                issues.append("FORMAL_SEED_RESULTS_REQUIRED")
            else:
                observed_seeds = [
                    item.get("seed") for item in seed_results if isinstance(item, dict)
                ]
                if observed_seeds != manifest.seeds:
                    issues.append("FORMAL_SEED_RESULT_LINEAGE_MISMATCH")
                for item in seed_results:
                    if not isinstance(item, dict) or not isinstance(item.get("metrics"), dict):
                        issues.append("FORMAL_SEED_RESULT_METRICS_INVALID")
                        break

        if self.llm_provider is not None:
            semantic = self.llm_provider.generate_json(
                "experiment.audit_result",
                {
                    "manifest": manifest.model_dump(),
                    "files": [
                        {
                            "path": item.path,
                            "sha256": item.sha256,
                            "content": item.content,
                        }
                        for item in bundle.files
                    ],
                    "result": result,
                },
                {
                    "integrity_status": "passed|failed",
                    "issues": ["string: concrete source/result inconsistency"],
                    "verified_files": [{"path": "string", "sha256": "string"}],
                    "environment_summary": {"key": "value"},
                    "is_real_experiment": "boolean",
                },
                instructions=instructions,
            )
            issues.extend(_string_list(semantic.get("issues")))

        provider_reports_real = bool(result.get("is_real_experiment"))
        issues = list(dict.fromkeys(issues))
        is_real = provider_reports_real and not issues
        return {
            "integrity_status": "passed" if not issues else "failed",
            "issues": issues,
            "verified_files": [
                {"path": item.path, "sha256": item.sha256} for item in bundle.files
            ],
            "environment_summary": {
                "provider": result.get("provider", ""),
                "python_version": environment.get("python_version", ""),
                "torch_version": environment.get("torch_version", ""),
                "cuda_available": bool(environment.get("cuda_available")),
                "device_names": list(environment.get("device_names") or []),
            },
            "is_real_experiment": is_real,
        }

    def run_and_analyze(self, task: dict, code: dict | None = None) -> dict:
        """Compatibility wrapper for callers outside WorkflowEngine."""
        result = self.run(task, code)
        analysis = self.experiment_provider.analyze(result)
        return {**result, "analysis": analysis}

    @staticmethod
    def _validate_bundle_against_plan(plan: dict, bundle: ExperimentBundle) -> None:
        issues = ExperimentAgent._bundle_against_plan_issues(plan, bundle)
        if issues:
            raise ExperimentBundleValidationError(issues)

    @staticmethod
    def _bundle_against_plan_issues(plan: dict, bundle: ExperimentBundle) -> list[str]:
        """Plan/dataset contract rules shared by generation and repair validation."""
        issues: list[str] = []
        plan_dataset = plan.get("dataset") or {}
        contract_id = str(plan_dataset.get("contract_id") or "")
        if contract_id:
            planned_name = contract_canonical_name(plan_dataset)
            declared_name = normalize_dataset_name(bundle.manifest.dataset)
            if bundle.manifest.dataset and (
                not planned_name or declared_name != planned_name
            ):
                issues.append(
                    "EXPERIMENT_LOCAL_DATASET_SUBSTITUTION_FORBIDDEN:"
                    f"expected={planned_name or '(generic-local)'}:declared={declared_name or bundle.manifest.dataset}:"
                    f"contract={contract_id}:root={plan_dataset.get('root', '')}"
                )
            if bundle.manifest.dataset_contract_id != contract_id:
                issues.append(
                    "EXPERIMENT_DATASET_CONTRACT_MISMATCH:"
                    f"planned={contract_id}:bundle={bundle.manifest.dataset_contract_id or '(missing)'}"
                )
            expected_fingerprint = str(plan_dataset.get("content_fingerprint") or "")
            if expected_fingerprint and bundle.manifest.dataset_fingerprint != expected_fingerprint:
                issues.append(
                    "EXPERIMENT_DATASET_FINGERPRINT_MISMATCH:"
                    f"planned={expected_fingerprint}:bundle={bundle.manifest.dataset_fingerprint or '(missing)'}"
                )
            source = "\n".join(item.content for item in bundle.files)
            if "DATA_ROOT" not in source:
                issues.append(
                    f"EXPERIMENT_DATASET_ROOT_REQUIRED:contract={contract_id}"
                )
            if re.search(r"(?m)^\s*DATA_ROOT\s*=|os\.environ\s*\[\s*['\"]DATA_ROOT['\"]\s*\]\s*=", source):
                issues.append("EXPERIMENT_DATASET_ROOT_OVERRIDE_FORBIDDEN")
            forbidden = (
                "load_dataset(",
                "download=True",
                "download = True",
            )
            used = [token for token in forbidden if token in source]
            if used:
                issues.append(
                    "EXPERIMENT_LOCAL_DATASET_SUBSTITUTION_FORBIDDEN:"
                    + ",".join(used)
                )
            flat_fashion_mnist = {
                "train-images-idx3-ubyte.gz",
                "train-labels-idx1-ubyte.gz",
                "t10k-images-idx3-ubyte.gz",
                "t10k-labels-idx1-ubyte.gz",
            }
            layout_files = {
                str(item.get("relative_path") or "").replace("\\", "/")
                for item in plan_dataset.get("files") or []
                if isinstance(item, dict)
            }
            if (
                contract_canonical_name(plan_dataset) == "fashion-mnist"
                and flat_fashion_mnist <= layout_files
                and re.search(r"(?:torchvision\.)?(?:datasets\.)?FashionMNIST\s*\(", source)
            ):
                issues.append(
                    "EXPERIMENT_LOCAL_DATASET_LAYOUT_UNSUPPORTED:"
                    "flat Fashion-MNIST IDX gzip files at DATA_ROOT require a local IDX gzip loader; "
                    "torchvision.datasets.FashionMNIST expects FashionMNIST/raw"
                )
        else:
            planned_dataset = normalize_dataset_name((plan.get("dataset") or {}).get("name"))
            bundle_dataset = normalize_dataset_name(bundle.manifest.dataset)
            if planned_dataset and bundle_dataset != planned_dataset:
                issues.append(
                "EXPERIMENT_PLAN_DATASET_MISMATCH:"
                f"planned={planned_dataset}:bundle={bundle_dataset or '(missing)'}"
                )

        planned_seeds = [int(seed) for seed in plan.get("seeds") or []]
        if planned_seeds and bundle.manifest.seeds != planned_seeds:
            issues.append(
                "EXPERIMENT_PLAN_SEEDS_MISMATCH:"
                f"planned={planned_seeds}:bundle={bundle.manifest.seeds}"
            )

        planned_parameters = dict(plan.get("parameters") or {})
        mismatched = [
            key
            for key, value in planned_parameters.items()
            if key not in bundle.manifest.parameters
            or bundle.manifest.parameters.get(key) != value
        ]
        if mismatched:
            issues.append(
                "EXPERIMENT_PLAN_PARAMETERS_MISMATCH:" + ",".join(sorted(mismatched))
            )

        contract = plan.get("iteration_contract") or {}
        if isinstance(contract, dict) and contract:
            required_metrics = [
                str(metric).strip()
                for metric in contract.get("required_metrics") or []
                if str(metric).strip()
            ]
            missing_metrics = [
                metric for metric in required_metrics
                if metric not in bundle.manifest.expected_metrics
            ]
            if missing_metrics:
                issues.append(
                    "EXPERIMENT_FEEDBACK_METRICS_MISSING:"
                    + ",".join(sorted(missing_metrics))
                )
        return issues


def _record_list(value) -> list[dict]:
    return [dict(item) for item in value or [] if isinstance(item, dict)]


def _string_list(value) -> list[str]:
    return [str(item).strip() for item in value or [] if str(item).strip()]
