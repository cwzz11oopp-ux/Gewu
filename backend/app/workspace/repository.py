from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from backend.app.workspace.command_runner import ApprovedCommandRunner
from backend.app.workspace.git import GitRepository


class RepositoryInspection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    root: str
    head: str
    dirty: bool
    dirty_entries: list[str] = Field(default_factory=list)
    tracked_files: list[str] = Field(default_factory=list)
    entrypoint_candidates: list[str] = Field(default_factory=list)
    config_files: list[str] = Field(default_factory=list)
    test_files: list[str] = Field(default_factory=list)


class RepositoryWorkspace:
    ENTRYPOINT_NAMES = {
        "main.py",
        "train.py",
        "run.py",
        "app.py",
        "manage.py",
        "package.json",
    }
    CONFIG_NAMES = {
        "pyproject.toml",
        "requirements.txt",
        "environment.yml",
        "setup.py",
        "setup.cfg",
        "package.json",
        "Dockerfile",
    }

    def __init__(
        self,
        root: str | Path,
        *,
        allowed_executables: set[str] | None = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.git = GitRepository(self.root)
        self.command_runner = ApprovedCommandRunner(
            self.root,
            allowed_executables=allowed_executables,
        )

    def inspect(self) -> RepositoryInspection:
        files = self.git.tracked_files()
        return RepositoryInspection(
            root=str(self.root),
            head=self.git.head(),
            dirty=self.git.is_dirty(),
            dirty_entries=self.git.status(),
            tracked_files=files,
            entrypoint_candidates=[
                item for item in files if Path(item).name in self.ENTRYPOINT_NAMES
            ],
            config_files=[item for item in files if Path(item).name in self.CONFIG_NAMES],
            test_files=[
                item
                for item in files
                if "test" in Path(item).name.lower()
                or "tests" in {part.lower() for part in Path(item).parts}
            ],
        )

    def read_text(self, relative_path: str) -> str:
        path = self._resolve_file(relative_path)
        return path.read_text(encoding="utf-8")

    def write_text(self, relative_path: str, content: str) -> None:
        path = self._resolve_file(relative_path, must_exist=False)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def commit_validated(self, message: str, paths: list[str]) -> str:
        if not message.strip() or not paths:
            raise ValueError("WORKSPACE_COMMIT_INPUT_INVALID")
        normalized = [
            str(self._resolve_file(path, must_exist=False).relative_to(self.root))
            for path in paths
        ]
        self.git.require_success(["add", "--", *normalized])
        self.git.require_success(["commit", "-m", message])
        return self.git.head()

    def _resolve_file(self, relative_path: str, *, must_exist: bool = True) -> Path:
        if not relative_path or Path(relative_path).is_absolute():
            raise ValueError(f"WORKSPACE_PATH_INVALID:{relative_path}")
        path = (self.root / relative_path).resolve()
        if not path.is_relative_to(self.root) or path == self.root:
            raise ValueError(f"WORKSPACE_PATH_OUTSIDE_ROOT:{relative_path}")
        relative_parts = path.relative_to(self.root).parts
        if relative_parts and relative_parts[0].lower() == ".git":
            raise ValueError("WORKSPACE_GIT_METADATA_WRITE_DENIED")
        if must_exist and not path.is_file():
            raise FileNotFoundError(path)
        return path
