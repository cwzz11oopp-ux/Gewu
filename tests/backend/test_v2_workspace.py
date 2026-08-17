from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from backend.app.workspace import RepositoryWorkspace, WorktreeManager


FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "v2_demo_repo"


def git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.strip()


def make_repository(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    shutil.copytree(FIXTURE, root)
    git(root, "init")
    git(root, "config", "user.name", "V2 Fixture")
    git(root, "config", "user.email", "v2-fixture@example.invalid")
    git(root, "add", ".")
    git(root, "commit", "-m", "fixture baseline")
    return root


def test_repository_workspace_inspects_edits_diffs_and_commits(tmp_path):
    root = make_repository(tmp_path)
    workspace = RepositoryWorkspace(root, allowed_executables={"python.exe", "git"})
    inspection = workspace.inspect()
    assert inspection.dirty is False
    assert inspection.entrypoint_candidates == ["train.py"]
    assert inspection.test_files == ["test_model.py"]

    workspace.write_text(
        "model.py",
        workspace.read_text("model.py").replace("DEFAULT_THRESHOLD = 0.5", "DEFAULT_THRESHOLD = 0.0"),
    )
    assert workspace.git.changed_files() == ["model.py"]
    assert "DEFAULT_THRESHOLD = 0.0" in workspace.git.diff()
    commit = workspace.commit_validated("feat: lower fixture threshold", ["model.py"])
    assert commit == workspace.git.head()
    assert workspace.git.is_dirty() is False


def test_worktree_is_isolated_and_cleanup_rejects_unmanaged_target(tmp_path):
    root = make_repository(tmp_path)
    manager = WorktreeManager(root, tmp_path / "worktrees")
    worktree = manager.create(branch="v2exp/h1", directory_name="h1-attempt-1")
    isolated = worktree.workspace(allowed_executables={"python.exe", "git"})
    isolated.write_text(
        "model.py",
        isolated.read_text("model.py").replace("DEFAULT_THRESHOLD = 0.5", "DEFAULT_THRESHOLD = 0.0"),
    )
    assert isolated.git.changed_files() == ["model.py"]
    assert RepositoryWorkspace(root).git.is_dirty() is False
    isolated.commit_validated("feat: validate h1 fixture", ["model.py"])
    code_commit = isolated.git.head()
    assert code_commit != worktree.base_commit
    manager.remove(worktree)
    assert not worktree.path.exists()

    with pytest.raises(ValueError, match="WORKTREE_NOT_MANAGED"):
        manager.remove(worktree)


def test_workspace_rejects_path_escape_and_unapproved_executable(tmp_path):
    root = make_repository(tmp_path)
    workspace = RepositoryWorkspace(root, allowed_executables={"python.exe"})
    with pytest.raises(ValueError, match="WORKSPACE_PATH_OUTSIDE_ROOT"):
        workspace.write_text("../outside.txt", "denied")
    with pytest.raises(ValueError, match="COMMAND_EXECUTABLE_NOT_ALLOWED"):
        workspace.command_runner.run(["git", "status"])


def test_worktree_creation_refuses_dirty_canonical_repository(tmp_path):
    root = make_repository(tmp_path)
    (root / "model.py").write_text("dirty = True\n", encoding="utf-8")
    manager = WorktreeManager(root, tmp_path / "worktrees")
    with pytest.raises(ValueError, match="CANONICAL_REPOSITORY_DIRTY"):
        manager.create(branch="v2exp/dirty")
