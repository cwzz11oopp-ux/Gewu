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

_CLASSIFICATION_SMOKE_INSTRUCTIONS = (
    "Smoke-test rule: load the complete verified dataset and use exactly the same "
    "split, preprocessing, labels/targets, and validation code as scientific runs. "
    "Never create a smoke subset or resample data: no X[:N], y[:N], Subset, random "
    "sampling, class reduction, anomaly-ratio reduction, or alternate split. Smoke "
    "seed and execution budget are provided by the Harness through "
    "GEWU_EXECUTION_STAGE and GEWU_EXECUTION_EPOCHS; do not redefine them. Keep "
    "the existing Skill and Validator-required training-progress and result-output "
    "protocol."
)

_OBSERVED_STRUCTURE_INSTRUCTIONS = (
    "observed_structure comes from a read-only inspection of the real local data files. "
    "Implement loading from its actual keys, shapes, dtypes, and columns; never assume a key "
    "that is absent. Do not put data values into code or prompts, and never change or truncate "
    "the dataset scale for Smoke."
)

_RUNTIME_SEED_INSTRUCTIONS = (
    "--seed is the authoritative runtime seed. Use args.seed to initialize every "
    "random source actually used by train.py (for example random, numpy, and torch). "
    "Use the same args.seed for baseline, variant, training, validation, and result "
    "output; result seed must equal args.seed. Do not hardcode a seed or select one "
    "from Plan seeds inside train.py."
)


def _system_bundle_payload(raw: dict, plan: dict, task: dict) -> dict:
    """Attach the system-owned execution contract to model-produced source.

    The code model owns only ``files`` and ``requirements``.  Requiring it to
    echo seeds, metrics, parameters, or runtime flags proved fragile and made a
    missing transport field poison every repair attempt.
    """
    dataset = plan.get("dataset") or {}
    resources = plan.get("resources") or {}
    gpu_value = str(resources.get("gpu") or task.get("compute_resource") or "").strip().lower()
    requires_gpu = gpu_value not in {"", "cpu", "none", "false", "0"}
    metrics = [
        str(item.get("metric") or "").strip()
        for item in plan.get("evaluations") or []
        if isinstance(item, dict) and str(item.get("metric") or "").strip()
    ]
    return {
        "entrypoint": "train.py",
        "files": raw.get("files"),
        "requirements": raw.get("requirements") or [],
        "requires_gpu": requires_gpu,
        "dataset": contract_canonical_name(dataset) or str(task.get("dataset") or ""),
        "expected_metrics": list(dict.fromkeys(metrics)) or ["test_accuracy"],
        "parameters": dict(plan.get("parameters") or {}),
        "seeds": [int(item) for item in plan.get("seeds") or []],
        "supports_smoke_test": True,
    }


def _result_contract_instructions(expected_metrics: list[str]) -> str:
    """Keep generated result keys aligned with the immutable Bundle contract."""
    metrics = [str(item) for item in expected_metrics if str(item).strip()]
    return (
        "Result-output contract: import gewu_runtime as gr and finish each run with "
        "exactly one gr.write_result(metrics, seed=args.seed) call after every planned "
        "variant has completed. For a baseline-versus-intervention comparison, retain both "
        "sets of scalar metrics using Baseline_ and intervention-prefixed keys, and copy the "
        "intervention values to the required exact keys. Never write one result per variant, "
        "because the later write overwrites the baseline evidence. The metrics dictionary must declare "
        "every required key as an exact string literal; do not use aliases, shortened "
        "names, or only Baseline_/ECA_ prefixed variants. Required exact keys: "
        + json.dumps(metrics, ensure_ascii=False)
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


def _compact_result_for_audit(result: dict) -> dict:
    """Strip observational epoch history before semantic review.

    The audit verifies final scalar metrics and source integrity; a full per-epoch
    training curve would only inflate the review context.  Scientific scalar
    fields (metrics, seed_results, runtime) are preserved verbatim.
    """
    compact = {key: value for key, value in result.items() if key != "epoch_metrics"}
    seed_results = compact.get("seed_results")
    if isinstance(seed_results, list):
        compact["seed_results"] = [
            (
                {key: value for key, value in item.items() if key != "epoch_metrics"}
                if isinstance(item, dict)
                else item
            )
            for item in seed_results
        ]
    return compact


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
        raw_constraints = plan.get("experiment_constraints")
        constraints = dict(raw_constraints) if isinstance(raw_constraints, dict) else {}
        execution_seeds = [
            int(seed)
            for seed in (plan.get("seeds") or [])
            if isinstance(seed, int) and not isinstance(seed, bool)
        ]
        if execution_seeds:
            # The backend-preregistered plan contract is authoritative.  Keep the
            # provider command, task metadata, and multi-seed harness on the same
            # primary seed instead of leaking the historical placeholder ``7``.
            constraints["seed"] = execution_seeds[0]
            constraints["seeds"] = list(execution_seeds)
        else:
            constraints.setdefault("seed", 7)
        task = self.experiment_provider.plan(plan, constraints)
        if execution_seeds:
            task["seed"] = execution_seeds[0]
            task["seeds"] = list(execution_seeds)
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
            instructions="\n\n".join(
                part
                for part in (
                    instructions,
                    _CLASSIFICATION_SMOKE_INSTRUCTIONS,
                    _RUNTIME_SEED_INSTRUCTIONS,
                    output_contract,
                )
                if part
            ),
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
        implementation_base: dict | None = None,
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
        # Agent-local text is limited to the transport representation. The
        # backend injects every execution-contract field after generation.
        plan_metrics = [
            str(item.get("metric"))
            for item in plan.get("evaluations") or []
            if isinstance(item, dict) and str(item.get("metric") or "").strip()
        ]
        output_contract = "\n\n".join(
            part for part in (
                _BUNDLE_TRANSPORT_INSTRUCTIONS,
                "Return only train.py source files and third-party requirements. "
                "The backend owns and injects dataset identity, runtime flags, metrics, "
                "parameters, seeds, identifiers, and output paths; do not return them.",
                _result_contract_instructions(plan_metrics or ["test_accuracy"]),
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
                "observed_structure": self._observed_structure_for(plan, task),
                # A PIVOT may reuse a previously audited implementation as a
                # read-only source base. The model still returns a full bundle.
                "implementation_base": deepcopy(implementation_base or {}),
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
            },
            instructions="\n\n".join(
                part
                for part in (
                    instructions,
                    self._local_dataset_layout_instructions(plan),
                    _OBSERVED_STRUCTURE_INSTRUCTIONS,
                    _RUNTIME_SEED_INSTRUCTIONS,
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
                    _CLASSIFICATION_SMOKE_INSTRUCTIONS,
                    (
                        "PIVOT implementation rule: implementation_base is a read-only, "
                        "previously audited source snapshot. Start from it and make only the "
                        "declared change_set needed by the current Plan. Preserve the loader, "
                        "split, controls, metric calculation, runtime scaffold, and result "
                        "serialization unless the current Plan explicitly changes one of them."
                        if implementation_base
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
                _system_bundle_payload(raw, plan, task),
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
                task=task,
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
                "observed_structure": self._observed_structure_for(plan, task),
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
                    _OBSERVED_STRUCTURE_INSTRUCTIONS,
                    _RUNTIME_SEED_INSTRUCTIONS,
                    _CLASSIFICATION_SMOKE_INSTRUCTIONS,
                    _result_contract_instructions(
                        list(frozen.get("expected_metrics") or [])
                    ),
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
                _system_bundle_payload(
                    {**raw, "requirements": raw.get("requirements") or previous_requirements},
                    plan,
                    task,
                ),
                str(frozen.get("run_id") or task.get("run_id") or "run_1"),
                str(frozen.get("experiment_id") or task.get("experiment_id") or "experiment_1"),
                {**task, "plan": task.get("plan") or plan},
                validate_source=False,
            )
            repaired = compile_bundle_runtime_contract(plan, task, repaired)
        except ValueError as exc:
            raise ExperimentBundleCandidateError(exc, raw) from exc
        if validate:
            self.validate_bundle(plan, repaired, task=task)
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
            "requires_gpu": bool(_system_bundle_payload({}, plan, task).get("requires_gpu")),
            "dataset": contract_canonical_name(dataset) or "",
            "dataset_contract_id": str(dataset.get("contract_id") or ""),
            "dataset_fingerprint": str(dataset.get("content_fingerprint") or ""),
            "expected_metrics": _system_bundle_payload({}, plan, task)["expected_metrics"],
            # A rejected model candidate has no authority to redefine the
            # accepted Plan.  Freezing its schema-example defaults poisons every
            # subsequent repair, so preserve the Plan's scientific inputs here.
            "parameters": dict(plan.get("parameters") or {}),
            "seeds": [int(item) for item in plan.get("seeds") or []],
            "supports_smoke_test": True,
        }

    @staticmethod
    def validate_bundle(
        plan: dict,
        bundle: ExperimentBundle,
        *,
        require_smoke_test: bool = False,
        task: dict | None = None,
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
        compiled = compile_bundle_runtime_contract(plan, task or {}, bundle).runtime_contract
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
    def _observed_structure_for(plan: dict, task: dict) -> list[dict]:
        dataset = (plan or {}).get("dataset") or {}
        observed = dataset.get("observed_structure")
        if isinstance(observed, list):
            return deepcopy(observed)
        card = ExperimentAgent._dataset_card_for(plan, task)
        return deepcopy(card.get("observed_structure") or [])

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
                    "result": _compact_result_for_audit(result),
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
        # The compiled harness is the scientific execution boundary.
        # Legacy Bundles keep their established provider contract, while a
        # runtime-bound Bundle must prove its complete per-seed result set.
        runtime_bound = bundle.runtime_contract is not None and bool(
            result.get("is_real_experiment")
        )
        execution_contract = bundle.runtime_contract
        if runtime_bound and runtime.get("mode") != "full":
            issues.append("FORMAL_RUNTIME_MODE_REQUIRED")
        if runtime_bound and runtime.get("stage") != execution_contract.stage:
            issues.append("EXECUTION_STAGE_MISMATCH")
        if runtime_bound and runtime.get("epochs") != execution_contract.epochs:
            issues.append("EXECUTION_EPOCHS_MISMATCH")
        if runtime_bound and execution_contract.seeds:
            result_seeds = result.get("seeds")
            if result_seeds != execution_contract.seeds:
                issues.append("FORMAL_SEED_SET_MISMATCH")
            seed_results = result.get("seed_results")
            if not isinstance(seed_results, list):
                issues.append("FORMAL_SEED_RESULTS_REQUIRED")
            else:
                observed_seeds = [
                    item.get("seed") for item in seed_results if isinstance(item, dict)
                ]
                if observed_seeds != execution_contract.seeds:
                    issues.append("FORMAL_SEED_RESULT_LINEAGE_MISMATCH")
                for item in seed_results:
                    if not isinstance(item, dict) or not isinstance(item.get("metrics"), dict):
                        issues.append("FORMAL_SEED_RESULT_METRICS_INVALID")
                        break

        # A runtime-bound result has already been checked against the compiled
        # Harness contract above. Keep that deterministic audit authoritative;
        # an LLM review must not reject or rewrite a successfully executed run.
        if self.llm_provider is not None and not runtime_bound:
            semantic = self.llm_provider.generate_json(
                "experiment.audit_result",
                {
                    "manifest": manifest.model_dump(),
                    "runtime_contract": (
                        bundle.runtime_contract.model_dump()
                        if bundle.runtime_contract is not None
                        else None
                    ),
                    "files": [
                        {
                            "path": item.path,
                            "sha256": item.sha256,
                            "content": item.content,
                        }
                        for item in bundle.files
                    ],
                    "result": _compact_result_for_audit(result),
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
            # The semantic reviewer may include contextual observations.  Only
            # concrete inconsistencies belong in the failure set; an advisory
            # explicitly labelled INFO/NOTE must not turn a valid real result
            # into an audit failure.
            issues.extend(
                item
                for item in _string_list(semantic.get("issues"))
                if not item.strip().upper().startswith(("INFO:", "NOTE:"))
            )

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
            uses_runtime_data_root = bool(
                re.search(r"\b(?:gewu_runtime|gr)\.\s*data_root\s*\(", source)
            )
            if "DATA_ROOT" not in source and not uses_runtime_data_root:
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
