from __future__ import annotations

import os
import subprocess
from pathlib import Path
from time import monotonic

from pydantic import BaseModel, ConfigDict, Field


class CommandResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    argv: list[str]
    cwd: str
    returncode: int
    stdout: str
    stderr: str
    duration_seconds: float = Field(ge=0.0)


class ApprovedCommandRunner:
    """Runs argv without a shell inside one validated repository workspace."""

    def __init__(
        self,
        root: str | Path,
        *,
        allowed_executables: set[str] | None = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.allowed_executables = {
            value.lower()
            for value in (allowed_executables or {"python", "python.exe", "pytest", "git"})
        }

    def run(
        self,
        argv: list[str],
        *,
        cwd: str | Path | None = None,
        timeout_seconds: float = 300,
        env: dict[str, str] | None = None,
    ) -> CommandResult:
        if not argv or not all(isinstance(item, str) and item for item in argv):
            raise ValueError("COMMAND_ARGV_INVALID")
        executable = Path(argv[0]).name.lower()
        if executable not in self.allowed_executables:
            raise ValueError(f"COMMAND_EXECUTABLE_NOT_ALLOWED:{executable}")
        resolved_cwd = Path(cwd or self.root).resolve()
        self._require_inside_root(resolved_cwd)
        merged_env = os.environ.copy()
        if env:
            merged_env.update({str(key): str(value) for key, value in env.items()})
        started = monotonic()
        completed = subprocess.run(
            argv,
            cwd=resolved_cwd,
            env=merged_env,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            shell=False,
            check=False,
        )
        return CommandResult(
            argv=argv,
            cwd=str(resolved_cwd),
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            duration_seconds=monotonic() - started,
        )

    def _require_inside_root(self, path: Path) -> None:
        if path != self.root and not path.is_relative_to(self.root):
            raise ValueError(f"COMMAND_CWD_OUTSIDE_WORKSPACE:{path}")
        if not path.is_dir():
            raise ValueError(f"COMMAND_CWD_NOT_DIRECTORY:{path}")
