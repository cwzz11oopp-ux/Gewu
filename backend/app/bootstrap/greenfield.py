from __future__ import annotations

import gzip
import hashlib
import json
import re
import subprocess
import sys
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.app.baseline import BaselineReproducer, BaselineReproductionRequest
from backend.app.experiment import (
    GeneralRepositoryExperimentContract,
    GeneralRepositoryImplementationPlanner,
    PlannedRepositoryExperimentAdapter,
    WorkspaceExperimentAdapter,
)
from backend.app.literature import SprintLiteratureService
from backend.app.models.gateway import ModelGateway
from backend.app.research import (
    BaselineProfile,
    BudgetState,
    DatasetIdentity,
    ExperimentProtocol,
    MetricDefinition,
    MetricDirection,
    ProblemProfile,
    SeedPolicy,
    TrainingBudget,
)
from backend.app.services.v2_sessions import ResearchSessionService
from backend.app.workspace import RepositoryWorkspace, WorktreeManager
from backend.app.workflow.dataset_catalog import (
    dataset_card,
    dataset_download_script,
    dataset_spec,
    normalize_dataset_name,
    supported_dataset_names,
)
from backend.app.workflow.dataset_inspection import inspect_dataset_directory


class GreenfieldResearchMode(StrEnum):
    GREENFIELD = "greenfield"
    REPOSITORY = "repository"


class DatasetSourceStrategy(StrEnum):
    LOCAL = "local"
    AUTO_LOCAL = "auto_local"
    ONLINE = "online"


class DatasetProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    local_path: str = Field(min_length=1)
    loader_root: str = Field(min_length=1)
    task_type: str = Field(min_length=1)
    format: str = Field(min_length=1)
    split: dict[str, Any]
    sample_count: int = Field(ge=0)
    class_count: int | None = Field(default=None, ge=1)
    label_information: list[str] = Field(default_factory=list)
    input_shape: list[int] = Field(default_factory=list)
    preprocessing_assumptions: dict[str, Any] = Field(default_factory=dict)
    availability: str = Field(pattern="^(available|missing|approval_required)$")
    source: str = "local"
    file_count: int = Field(ge=0)
    total_bytes: int = Field(ge=0)
    file_types: dict[str, int] = Field(default_factory=dict)
    corrupted_files: list[str] = Field(default_factory=list)
    task_compatible: bool = True
    fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    def identity(self) -> DatasetIdentity:
        return DatasetIdentity(
            name=self.name,
            version="local",
            source=self.source,
            fingerprint=self.fingerprint,
        )


class BaselineDesign(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_name: str = Field(min_length=1)
    task_type: str = Field(min_length=1)
    method_summary: str = Field(min_length=1)
    entrypoint: str = Field(min_length=1)
    metric_name: str = Field(min_length=1)
    metric_direction: MetricDirection
    metric_definition: str = Field(min_length=1)
    preprocessing: dict[str, Any] = Field(min_length=1)
    evaluation_protocol: dict[str, Any] = Field(min_length=1)
    training_controls: dict[str, Any] = Field(min_length=1)
    epochs: int = Field(default=1, ge=1, le=20)
    seeds: list[int] = Field(min_length=1, max_length=5)
    environment_requirements: list[str] = Field(default_factory=list)


class GeneratedFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    content: str
    purpose: str = Field(min_length=1)


class BaselineRepositoryPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    files: list[GeneratedFile] = Field(min_length=1, max_length=16)
    smoke_description: str = Field(min_length=1)
    formal_run_description: str = Field(min_length=1)


class GreenfieldBootstrapRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1)
    research_mode: GreenfieldResearchMode = GreenfieldResearchMode.GREENFIELD
    dataset_strategy: DatasetSourceStrategy = DatasetSourceStrategy.AUTO_LOCAL
    local_dataset_path: str = ""
    dataset_root: str = ""
    allow_online_dataset_download: bool = False
    research_constraints: list[str] = Field(default_factory=list)
    experiment_limit: int = Field(default=4, ge=1, le=100)
    compute_minutes_limit: int = Field(default=60, ge=1)
    model_call_limit: int = Field(default=12, ge=2)
    run_first_experiment: bool = True


class BootstrapResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    research_mode: GreenfieldResearchMode
    dataset_profile: DatasetProfile
    baseline: BaselineProfile
    repository_path: str
    baseline_commit: str
    bootstrap_stages: list[dict[str, Any]]
    literature: dict[str, Any]
    first_action: dict[str, Any] | None = None
    first_experiment: dict[str, Any] | None = None
    online_download_performed: bool = False


class DatasetDownloadApprovalRequired(RuntimeError):
    def __init__(self, dataset_name: str, root: Path) -> None:
        self.detail = {
            "code": "ONLINE_DATASET_DOWNLOAD_APPROVAL_REQUIRED",
            "dataset_name": dataset_name,
            "source": "torchvision official dataset source",
            "estimated_size": "unknown; confirm before download",
            "download_location": str(root),
        }
        super().__init__(self.detail["code"])


def _dataset_name_from_question(question: str) -> str:
    compact = re.sub(r"[\s_-]+", "", question.lower())
    aliases = sorted(
        ((re.sub(r"[\s_-]+", "", name), name) for name in supported_dataset_names()),
        key=lambda item: len(item[0]),
        reverse=True,
    )
    for alias, name in aliases:
        if alias in compact:
            return name
    for candidate in ("fashionmnist", "cifar100", "cifar10", "mnist"):
        if candidate in compact:
            return normalize_dataset_name(candidate)
    return ""


def _catalog_dataset_directory(root: Path, name: str) -> Path:
    marker = Path(*dataset_spec(name).marker.split("/"))
    return root / marker.parts[0]


def _check_corruption(files: list[Path], root: Path) -> list[str]:
    corrupted: list[str] = []
    for path in files:
        try:
            if path.suffix.lower() == ".gz":
                with gzip.open(path, "rb") as stream:
                    stream.read(32)
            elif path.stat().st_size == 0:
                raise ValueError("empty file")
        except (OSError, EOFError, ValueError):
            corrupted.append(path.relative_to(root).as_posix())
    return corrupted


def inspect_local_dataset(
    *, question: str, dataset_root: str, local_dataset_path: str = ""
) -> DatasetProfile:
    configured_root = Path(dataset_root).expanduser().resolve()
    name = _dataset_name_from_question(question)
    if local_dataset_path:
        selected = Path(local_dataset_path).expanduser().resolve()
        if not name:
            name = normalize_dataset_name(selected.name) or selected.name
        loader_root = configured_root if configured_root.is_dir() else selected.parent
    else:
        if not name:
            raise ValueError("LOCAL_DATASET_CANNOT_BE_INFERRED_FROM_QUESTION")
        selected = _catalog_dataset_directory(configured_root, name)
        loader_root = configured_root
    if not selected.is_dir():
        raise FileNotFoundError(f"LOCAL_DATASET_NOT_FOUND:{selected}")

    inventory = inspect_dataset_directory(str(selected))
    files = sorted(path for path in selected.rglob("*") if path.is_file())
    corrupted = _check_corruption(files, selected)
    normalized = normalize_dataset_name(name)
    if normalized:
        card = dataset_card(normalized)
        split = {"train": card["train_size"], "validation": "derived from train only", "test": card["test_size"]}
        sample_count = int(card["train_size"]) + int(card["test_size"])
        class_count = int(card["num_classes"])
        labels = [f"class_{index}" for index in range(class_count)]
        input_shape = list(card["input_shape"])
        preprocessing = {"normalization": card["normalization"], "loader": card["loader"]}
        task_type = "image classification"
        data_format = "torchvision local files"
        name = normalized
    else:
        split = {"discovered": [item["relative_path"] for item in inventory["files"][:20]]}
        sample_count = 0
        class_count = None
        labels = []
        input_shape = []
        preprocessing = {"assumption": "custom loader required; no transformation inferred"}
        task_type = "dataset-dependent"
        data_format = ", ".join(sorted(inventory["file_types"]))
    return DatasetProfile(
        name=name,
        local_path=str(selected),
        loader_root=str(loader_root),
        task_type=task_type,
        format=data_format,
        split=split,
        sample_count=sample_count,
        class_count=class_count,
        label_information=labels,
        input_shape=input_shape,
        preprocessing_assumptions=preprocessing,
        availability="available" if not corrupted else "missing",
        file_count=inventory["file_count"],
        total_bytes=inventory["total_bytes"],
        file_types=inventory["file_types"],
        corrupted_files=corrupted,
        task_compatible=not corrupted,
        fingerprint=inventory["content_fingerprint"],
    )


class GreenfieldBootstrapService:
    def __init__(
        self,
        data_dir: str,
        sessions: ResearchSessionService,
        gateway: ModelGateway,
        literature: SprintLiteratureService,
        *,
        python: str = sys.executable,
    ) -> None:
        self.data_dir = Path(data_dir).resolve()
        self.sessions = sessions
        self.gateway = gateway
        self.literature = literature
        self.python = python

    def inspect_dataset(self, request: GreenfieldBootstrapRequest) -> DatasetProfile:
        root = Path(request.dataset_root or "datasets").expanduser().resolve()
        if request.dataset_strategy == DatasetSourceStrategy.ONLINE:
            name = _dataset_name_from_question(request.question) or "requested dataset"
            if not request.allow_online_dataset_download:
                raise DatasetDownloadApprovalRequired(name, root)
            normalized = normalize_dataset_name(name)
            if not normalized:
                raise ValueError(f"ONLINE_DATASET_UNSUPPORTED:{name}")
            root.mkdir(parents=True, exist_ok=True)
            completed = subprocess.run(
                [self.python, "-c", dataset_download_script(), str(root), normalized, "", "5"],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=1800,
                check=False,
            )
            if completed.returncode != 0:
                raise RuntimeError("ONLINE_DATASET_DOWNLOAD_FAILED:" + (completed.stderr or completed.stdout)[-2000:])
            profile = inspect_local_dataset(question=request.question, dataset_root=str(root))
            return profile.model_copy(update={"source": "online-fallback-confirmed"})
        try:
            profile = inspect_local_dataset(
                question=request.question,
                dataset_root=str(root),
                local_dataset_path=request.local_dataset_path,
            )
        except (FileNotFoundError, ValueError):
            name = _dataset_name_from_question(request.question) or "requested dataset"
            if request.allow_online_dataset_download:
                raise DatasetDownloadApprovalRequired(name, root)
            raise
        if profile.corrupted_files:
            raise ValueError("LOCAL_DATASET_CORRUPTED:" + ",".join(profile.corrupted_files))
        return profile

    def run(self, request: GreenfieldBootstrapRequest) -> BootstrapResult:
        if request.research_mode != GreenfieldResearchMode.GREENFIELD:
            raise ValueError("GREENFIELD_BOOTSTRAP_REQUIRES_GREENFIELD_MODE")
        session_id = f"research_{uuid4().hex[:12]}"
        stages: list[dict[str, Any]] = []
        self._stage(stages, "analyze_problem", "completed", request.question)
        literature = self.literature.research([request.question], limit_per_query=5)
        self._stage(stages, "literature_search", "completed", f"{len(literature.papers)} papers")
        dataset = self.inspect_dataset(request)
        self._stage(stages, "inspect_local_dataset", "completed", dataset.fingerprint)
        self._stage(stages, "select_dataset", "completed", dataset.local_path)

        design = self._design(request, dataset, literature)
        self._stage(stages, "design_baseline", "completed", design.method_summary)
        protocol = self._protocol(design, dataset)
        repository, commit = self._create_repository(session_id, request, dataset, design, protocol, stages)
        baseline = self._run_baseline(repository, commit, dataset, design, protocol, stages)
        if not baseline.can_be_comparison_denominator:
            raise RuntimeError(f"GREENFIELD_BASELINE_NOT_VALIDATED:{baseline.validation_reason}")

        problem = ProblemProfile(
            question=request.question,
            task=design.task_type,
            repository=str(repository),
            dataset=dataset.identity(),
            compute_constraints={
                "compute_minutes_limit": request.compute_minutes_limit,
                "environment": "configured local runtime",
            },
            research_constraints=[
                *request.research_constraints,
                "Keep the dataset fingerprint and evaluation protocol locked across variants.",
            ],
            success_criteria=["Produce audited, protocol-compatible evidence against the validated baseline."],
            open_questions=[],
        )
        budget = BudgetState(
            experiment_limit=request.experiment_limit,
            compute_minutes_limit=request.compute_minutes_limit,
            model_call_limit=request.model_call_limit,
        )
        self.sessions.create(problem, budget, baseline, session_id=session_id)
        transition = self.sessions.start(session_id)
        first_action = transition.action.model_dump(mode="json") if transition.action else None
        first_experiment = None
        if request.run_first_experiment and transition.action and transition.action.branch_id:
            self._assert_dataset_unchanged(dataset)
            branch = transition.state.frontier.get(transition.action.branch_id)
            pool = self.data_dir / "workspace" / "worktrees" / session_id
            result_path = "variant-result.json"
            planned = PlannedRepositoryExperimentAdapter(
                GeneralRepositoryImplementationPlanner(self.gateway),
                WorkspaceExperimentAdapter(WorktreeManager(repository, pool)),
            ).execute(
                GeneralRepositoryExperimentContract(
                    experiment_id=f"{session_id}_exp_1",
                    action=transition.action,
                    branch=branch,
                    worktree_branch=f"greenfield/{session_id}-exp-1",
                    repository=str(repository),
                    base_commit=baseline.commit,
                    protocol=protocol,
                    baseline_protocol=protocol,
                    baseline_metrics=baseline.local_metrics,
                    config={"operator": transition.action.operator, "dataset_fingerprint": dataset.fingerprint},
                    static_commands=[[self.python, "-m", "py_compile", design.entrypoint]],
                    smoke_commands=[[self.python, design.entrypoint, "--smoke", "--data-root", dataset.loader_root]],
                    formal_command=self._formal_command(design, dataset, protocol, result_path),
                    result_path=result_path,
                    environment={"python": sys.version.split()[0], "dataset_fingerprint": dataset.fingerprint},
                    commit_message=f"experiment: {transition.action.operator}",
                )
            )
            first_experiment = planned.record.model_dump(mode="json")
            self.sessions.continue_session(session_id, experiment=planned.record)
            self._stage(stages, "first_experiment", "completed", planned.record.experiment_id)

        result = BootstrapResult(
            session_id=session_id,
            research_mode=GreenfieldResearchMode.GREENFIELD,
            dataset_profile=dataset,
            baseline=baseline,
            repository_path=str(repository),
            baseline_commit=commit,
            bootstrap_stages=stages,
            literature={
                "queries": literature.queries,
                "paper_count": len(literature.papers),
                "access_levels": [item.access_level for item in literature.papers],
                "warnings": literature.warnings,
            },
            first_action=first_action,
            first_experiment=first_experiment,
            online_download_performed=dataset.source == "online-fallback-confirmed",
        )
        self._persist(result)
        return result

    def get(self, session_id: str) -> BootstrapResult:
        path = self._result_path(session_id)
        if not path.is_file():
            raise KeyError(session_id)
        return BootstrapResult.model_validate_json(path.read_text(encoding="utf-8"))

    def _design(self, request, dataset, literature) -> BaselineDesign:
        return self.gateway.invoke_structured(
            "v2.greenfield.design_baseline",
            [{"role": "user", "content": "Design a simple, low-cost, reproducible baseline. Do not optimize for SOTA."}],
            BaselineDesign,
            context={
                "research_question": request.question,
                "dataset_profile": dataset.model_dump(mode="json"),
                "compute_constraints": {"minutes": request.compute_minutes_limit},
                "literature": [item.model_dump(mode="json") for item in literature.papers[:5]],
                "rules": ["no online dataset download", "one low-cost baseline", "metrics must come from execution"],
            },
        )

    def _protocol(self, design: BaselineDesign, dataset: DatasetProfile) -> ExperimentProtocol:
        return ExperimentProtocol(
            task=design.task_type,
            dataset=dataset.identity(),
            split=dataset.split,
            preprocessing=design.preprocessing,
            metrics=[MetricDefinition(name=design.metric_name, direction=design.metric_direction, definition=design.metric_definition, aggregation="mean across locked seeds")],
            training_budget=TrainingBudget(epochs=design.epochs),
            evaluation_protocol=design.evaluation_protocol,
            seed_policy=SeedPolicy(seeds=design.seeds, aggregation="mean", minimum_repetitions=len(design.seeds)),
            training_controls={**design.training_controls, "dataset_fingerprint": dataset.fingerprint},
        )

    def _create_repository(self, session_id, request, dataset, design, protocol, stages):
        root = (self.data_dir / "workspace" / "projects" / session_id).resolve()
        projects = (self.data_dir / "workspace" / "projects").resolve()
        if not root.is_relative_to(projects):
            raise ValueError("GREENFIELD_PROJECT_PATH_OUTSIDE_WORKSPACE")
        root.mkdir(parents=True, exist_ok=False)
        self._stage(stages, "create_project", "completed", str(root))
        self._git(root, "init")
        self._git(root, "config", "user.name", "AI Scientist Bootstrap")
        self._git(root, "config", "user.email", "bootstrap@ai-scientist.invalid")
        self._stage(stages, "initialize_git", "completed", str(root / ".git"))
        plan = self.gateway.invoke_structured(
            "v2.greenfield.generate_repository",
            [{"role": "user", "content": "Generate the complete minimal baseline repository described by the context."}],
            BaselineRepositoryPlan,
            context={
                "question": request.question,
                "design": design.model_dump(mode="json"),
                "dataset_profile": dataset.model_dump(mode="json"),
                "protocol": protocol.model_dump(mode="json"),
                "runtime_contract": {
                    "required_cli": ["--smoke", "--output", "--protocol-fingerprint", "--seeds", "--data-root"],
                    "result_json": {"metrics": {design.metric_name: "real finite number"}, "seeds": design.seeds, "protocol_fingerprint": protocol.fingerprint().value},
                    "dataset_rule": "load only from --data-root and always use download=False",
                },
            },
        )
        paths: list[str] = []
        for item in plan.files:
            path = self._safe_generated_path(item.path)
            if path in paths:
                raise ValueError(f"GREENFIELD_DUPLICATE_FILE:{path}")
            if re.search(r"download\s*=\s*True|urllib|requests\.(?:get|post)|https?://", item.content, re.IGNORECASE):
                raise ValueError(f"GREENFIELD_ONLINE_ACCESS_FORBIDDEN:{path}")
            target = (root / path).resolve()
            if not target.is_relative_to(root):
                raise ValueError(f"GREENFIELD_FILE_OUTSIDE_WORKSPACE:{path}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(item.content, encoding="utf-8")
            paths.append(path)
        if design.entrypoint.replace("\\", "/") not in paths:
            raise ValueError("GREENFIELD_ENTRYPOINT_NOT_GENERATED")
        self._stage(stages, "generate_baseline_code", "completed", ", ".join(paths))
        workspace = RepositoryWorkspace(root, allowed_executables={Path(self.python).name})
        static = workspace.command_runner.run([self.python, "-m", "py_compile", *[path for path in paths if path.endswith(".py")]])
        if static.returncode != 0:
            raise RuntimeError("GREENFIELD_STATIC_VALIDATION_FAILED:" + static.stderr[-2000:])
        self._stage(stages, "static_validation", "completed", "python -m py_compile")
        smoke = workspace.command_runner.run([self.python, design.entrypoint, "--smoke", "--data-root", dataset.loader_root], timeout_seconds=120)
        if smoke.returncode != 0:
            raise RuntimeError("GREENFIELD_SMOKE_FAILED:" + (smoke.stderr or smoke.stdout)[-2000:])
        self._stage(stages, "smoke_test", "completed", smoke.stdout[-500:])
        self._git(root, "add", "--", *paths)
        self._git(root, "commit", "-m", "baseline: validated bootstrap project")
        return root, self._git(root, "rev-parse", "HEAD")

    def _run_baseline(self, repository, commit, dataset, design, protocol, stages):
        self._assert_dataset_unchanged(dataset)
        output = "baseline-result.json"
        workspace = RepositoryWorkspace(repository, allowed_executables={Path(self.python).name})
        profile = BaselineReproducer(workspace).reproduce_and_validate(
            BaselineReproductionRequest(
                repository=str(repository), commit=commit, task=design.task_type,
                entrypoint=design.entrypoint, protocol=protocol,
                command=self._formal_command(design, dataset, protocol, output),
                result_path=output,
                environment={"python": sys.version.split()[0], "dataset_fingerprint": dataset.fingerprint, "online_download": False},
                timeout_seconds=900,
            )
        )
        self._stage(stages, "baseline_experiment", "completed" if profile.can_be_comparison_denominator else "failed", profile.validation_reason)
        self._stage(stages, "baseline_established", "completed" if profile.can_be_comparison_denominator else "blocked", json.dumps(profile.local_metrics))
        return profile

    @staticmethod
    def _assert_dataset_unchanged(dataset: DatasetProfile) -> None:
        current = inspect_local_dataset(
            question=dataset.name,
            dataset_root=dataset.loader_root,
            local_dataset_path=dataset.local_path,
        )
        if current.fingerprint != dataset.fingerprint:
            raise ValueError(
                "DATASET_PROTOCOL_INCOMPATIBLE:"
                f"expected={dataset.fingerprint}:actual={current.fingerprint}"
            )

    def _formal_command(self, design, dataset, protocol, output):
        return [self.python, design.entrypoint, "--output", output, "--protocol-fingerprint", protocol.fingerprint().value, "--seeds", *[str(item) for item in design.seeds], "--data-root", dataset.loader_root]

    @staticmethod
    def _safe_generated_path(raw: str) -> str:
        path = raw.replace("\\", "/").strip()
        parsed = PurePosixPath(path)
        if not path or parsed.is_absolute() or ".." in parsed.parts or parsed.parts[0].lower() == ".git":
            raise ValueError(f"GREENFIELD_UNSAFE_FILE_PATH:{raw}")
        return path

    @staticmethod
    def _git(root: Path, *args: str) -> str:
        completed = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=False)
        if completed.returncode != 0:
            raise RuntimeError(f"GREENFIELD_GIT_FAILED:{args[0]}:{completed.stderr.strip()}")
        return completed.stdout.strip()

    @staticmethod
    def _stage(stages, name, status, detail):
        stages.append({"stage": name, "status": status, "detail": str(detail)})

    def _persist(self, result: BootstrapResult) -> None:
        path = self._result_path(result.session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(result.model_dump_json(indent=2), encoding="utf-8")

    def _result_path(self, session_id: str) -> Path:
        if not re.fullmatch(r"research_[0-9a-f]{12}", session_id):
            raise KeyError(session_id)
        return self.data_dir / "greenfield-bootstrap" / f"{session_id}.json"
