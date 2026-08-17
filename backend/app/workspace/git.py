from __future__ import annotations

import re
from pathlib import Path

from backend.app.workspace.command_runner import ApprovedCommandRunner, CommandResult


SAFE_BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")


class GitRepository:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.runner = ApprovedCommandRunner(self.root, allowed_executables={"git"})
        probe = self.run(["rev-parse", "--show-toplevel"])
        if probe.returncode != 0:
            raise ValueError(f"NOT_A_GIT_REPOSITORY:{self.root}")
        discovered = Path(probe.stdout.strip()).resolve()
        if discovered != self.root:
            raise ValueError(f"REPOSITORY_ROOT_MISMATCH:{discovered}")

    def run(self, args: list[str], *, timeout_seconds: float = 120) -> CommandResult:
        return self.runner.run(
            ["git", *args], cwd=self.root, timeout_seconds=timeout_seconds
        )

    def require_success(self, args: list[str], *, timeout_seconds: float = 120) -> CommandResult:
        result = self.run(args, timeout_seconds=timeout_seconds)
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise RuntimeError(f"GIT_COMMAND_FAILED:{args[0]}:{detail}")
        return result

    def head(self) -> str:
        return self.require_success(["rev-parse", "HEAD"]).stdout.strip()

    def is_dirty(self) -> bool:
        return bool(self.require_success(["status", "--porcelain"]).stdout.strip())

    def status(self) -> list[str]:
        return [
            line for line in self.require_success(["status", "--porcelain"]).stdout.splitlines()
            if line.strip()
        ]

    def tracked_files(self) -> list[str]:
        return sorted(
            line
            for line in self.require_success(["ls-files"]).stdout.splitlines()
            if line.strip()
        )

    def tracked_files_at(self, commit: str) -> list[str]:
        self.require_success(["rev-parse", "--verify", f"{commit}^{{commit}}"])
        return sorted(
            line
            for line in self.require_success(
                ["ls-tree", "-r", "--name-only", commit]
            ).stdout.splitlines()
            if line.strip()
        )

    def read_text_at(self, commit: str, relative_path: str) -> str:
        if relative_path not in self.tracked_files_at(commit):
            raise ValueError(f"GIT_PATH_NOT_TRACKED_AT_COMMIT:{relative_path}")
        return self.require_success(["show", f"{commit}:{relative_path}"]).stdout

    def changed_files(self) -> list[str]:
        result = self.require_success(["status", "--porcelain"])
        return sorted({line[3:].strip() for line in result.stdout.splitlines() if len(line) > 3})

    def diff(self) -> str:
        return self.require_success(["diff", "--no-ext-diff", "--binary"]).stdout

    @staticmethod
    def validate_branch(branch: str) -> None:
        if not SAFE_BRANCH.fullmatch(branch) or ".." in branch or branch.endswith("/"):
            raise ValueError(f"GIT_BRANCH_INVALID:{branch}")
