from __future__ import annotations

import hashlib
import posixpath
import re
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


EXPERIMENT_ID_PATTERN = re.compile(r"^experiment_[1-9][0-9]*$")


class ExperimentFile(BaseModel):
    path: str
    content: str
    sha256: str = ""

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        normalized = posixpath.normpath(value.replace("\\", "/"))
        if (
            normalized in {"", ".", ".."}
            or normalized.startswith("../")
            or posixpath.isabs(normalized)
        ):
            raise ValueError(f"EXPERIMENT_CODE_PATH_INVALID:{value}")
        return normalized

    @model_validator(mode="after")
    def fill_hash(self):
        if not self.content.strip():
            raise ValueError(f"EXPERIMENT_CODE_FILE_EMPTY:{self.path}")
        digest = hashlib.sha256(self.content.encode("utf-8")).hexdigest()
        if self.sha256 and self.sha256 != digest:
            raise ValueError(f"EXPERIMENT_CODE_HASH_INVALID:{self.path}")
        self.sha256 = digest
        return self


class ExperimentManifest(BaseModel):
    schema_version: int = 1
    run_id: str
    experiment_id: str
    result_id: str
    entrypoint: str = "train.py"
    python_args: list[str] = Field(default_factory=list)
    requirements_file: str = "requirements.txt"
    requires_gpu: bool = False
    dataset: str = ""
    dataset_contract_id: str = ""
    dataset_fingerprint: str = ""
    expected_metrics: list[str] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)
    seeds: list[int] = Field(default_factory=list)
    supports_smoke_test: bool = False

    @model_validator(mode="after")
    def validate_ids_and_paths(self):
        if not EXPERIMENT_ID_PATTERN.fullmatch(self.experiment_id):
            raise ValueError(f"EXPERIMENT_ID_INVALID:{self.experiment_id}")
        if self.result_id != f"{self.experiment_id}_result":
            raise ValueError("EXPERIMENT_RESULT_ID_MISMATCH")
        for value, code in (
            (self.entrypoint, "EXPERIMENT_CODE_ENTRYPOINT_INVALID"),
            (self.requirements_file, "EXPERIMENT_REQUIREMENTS_PATH_INVALID"),
        ):
            normalized = posixpath.normpath(value.replace("\\", "/"))
            if normalized.startswith("../") or posixpath.isabs(normalized):
                raise ValueError(f"{code}:{value}")
        return self


class ExperimentRuntimeContract(BaseModel):
    """Canonical system-owned execution protocol for one frozen Bundle."""

    schema_version: int = 1
    run_id: str
    experiment_id: str
    result_id: str
    dataset: str = ""
    dataset_contract_id: str = ""
    dataset_fingerprint: str = ""
    expected_data_root: str = ""
    requires_gpu: bool = False
    stage: str = "formal_validation"
    epochs: int = 1
    seeds: list[int] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)
    expected_metrics: list[str] = Field(default_factory=list)
    iteration_required_metrics: list[str] = Field(default_factory=list)
    supports_smoke_test: bool = False
    implementation_entrypoint: str = "train.py"
    implementation_output_path: str
    result_output_path: str
    harness_filename: str = ".gewu_harness.py"
    contract_sha256: str = ""


class ExperimentBundle(BaseModel):
    manifest: ExperimentManifest
    files: list[ExperimentFile]
    requirements: list[str] = Field(default_factory=list)
    runtime_contract: ExperimentRuntimeContract | None = None

    @model_validator(mode="after")
    def validate_entrypoint(self):
        paths = {item.path for item in self.files}
        if self.manifest.entrypoint not in paths:
            raise ValueError(
                f"EXPERIMENT_CODE_ENTRYPOINT_MISSING:{self.manifest.entrypoint}"
            )
        return self


class ExperimentEnvironment(BaseModel):
    provider: str
    python_version: str = ""
    torch_version: str = ""
    torch_cuda: str = ""
    cuda_available: bool = False
    device_count: int = 0
    device_names: list[str] = Field(default_factory=list)
    cuda_visible_devices: str = ""


class ExperimentAttempt(BaseModel):
    attempt: int
    start_time: str
    end_time: str | None = None
    status: str
    error_code: str = ""
    log_path: str = ""


class ExperimentResult(BaseModel):
    run_id: str
    experiment_id: str
    result_id: str
    metrics: dict[str, float | int]
    provider: str
    is_real_experiment: bool = False
    workdir: str = ""
    environment: ExperimentEnvironment | dict = Field(default_factory=dict)
    parameters: dict[str, Any] = Field(default_factory=dict)
    attempts: list[ExperimentAttempt] = Field(default_factory=list)
