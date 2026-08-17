from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from backend.app.workspace.git import GitRepository
from backend.app.workspace.repository import RepositoryWorkspace


@dataclass(frozen=True)
class ExperimentWorktree:
    repository_root: Path
    path: Path
    branch: str
    base_commit: str

    def workspace(self, *, allowed_executables: set[str] | None = None) -> RepositoryWorkspace:
        return RepositoryWorkspace(self.path, allowed_executables=allowed_executables)


class WorktreeManager:
    def __init__(self, repository_root: str | Path, pool_root: str | Path) -> None:
        self.repository = GitRepository(repository_root)
        self.pool_root = Path(pool_root).resolve()
        if self.pool_root == self.repository.root or self.pool_root.is_relative_to(
            self.repository.root
        ):
            raise ValueError("WORKTREE_POOL_MUST_BE_OUTSIDE_REPOSITORY")
        self.pool_root.mkdir(parents=True, exist_ok=True)
        self._managed: dict[Path, ExperimentWorktree] = {}

    def create(
        self,
        *,
        branch: str,
        base_commit: str | None = None,
        directory_name: str | None = None,
    ) -> ExperimentWorktree:
        if self.repository.is_dirty():
            raise ValueError("CANONICAL_REPOSITORY_DIRTY")
        self.repository.validate_branch(branch)
        base = base_commit or self.repository.head()
        name = directory_name or branch.replace("/", "__")
        if not name or name in {".", ".."} or any(char in name for char in "\\/:"):
            raise ValueError(f"WORKTREE_DIRECTORY_INVALID:{name}")
        target = (self.pool_root / name).resolve()
        if not target.is_relative_to(self.pool_root) or target == self.pool_root:
            raise ValueError("WORKTREE_PATH_OUTSIDE_POOL")
        if target.exists():
            raise FileExistsError(target)
        self.repository.require_success(
            ["worktree", "add", "-b", branch, str(target), base],
            timeout_seconds=180,
        )
        value = ExperimentWorktree(
            repository_root=self.repository.root,
            path=target,
            branch=branch,
            base_commit=base,
        )
        self._managed[target] = value
        return value

    def remove(self, worktree: ExperimentWorktree, *, force: bool = False) -> None:
        target = worktree.path.resolve()
        managed = self._managed.get(target)
        if managed != worktree or not target.is_relative_to(self.pool_root):
            raise ValueError("WORKTREE_NOT_MANAGED_BY_THIS_INSTANCE")
        args = ["worktree", "remove"]
        if force:
            args.append("--force")
        args.append(str(target))
        self.repository.require_success(args, timeout_seconds=180)
        self._managed.pop(target, None)
