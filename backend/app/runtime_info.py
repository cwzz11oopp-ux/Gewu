"""Non-secret runtime identity used to prove which build serves an API request."""

from __future__ import annotations

import hashlib
import importlib
import inspect
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


WORKFLOW_VERSION = "research-loop-v1"
_STARTED_AT = datetime.now(timezone.utc).isoformat()
_WORKFLOW_FILES = (
    "backend/app/workflow/engine.py",
    "backend/app/workflow/evidence_pipeline.py",
    "backend/app/workflow/knowledge.py",
    "backend/app/workflow/research_state.py",
    "backend/app/workflow/skills.py",
)
_RUNTIME_SKILLS = ("evidence-recovery", "hypothesis-evidence", "research-lit")


def runtime_info(repository_root: Path, skills_root: Path) -> dict[str, Any]:
    """Return stable, safe diagnostics without exposing configuration secrets."""
    root = repository_root.resolve()
    return {
        "pid": os.getpid(),
        "project_root": str(root),
        "source_root": str((root / "backend").resolve()),
        "python": sys.executable,
        "cwd": os.getcwd(),
        "started_at": _STARTED_AT,
        "build": _git_revision(root),
        "workflow_version": WORKFLOW_VERSION,
        "workflow_hash": _bundle_hash(root, _WORKFLOW_FILES),
        "module_paths": {
            module_name: _module_path(module_name)
            for module_name in (
                "backend.app.workflow.engine",
                "backend.app.workflow.evidence_pipeline",
                "backend.app.workflow.knowledge",
                "backend.app.workflow.research_state",
            )
        },
        "skills": {
            skill_id: _file_hash(skills_root / skill_id / "SKILL.md")
            for skill_id in _RUNTIME_SKILLS
        },
    }


def format_runtime_banner(info: dict[str, Any]) -> str:
    return "\n".join(
        (
            "=== GEWU BACKEND RUNTIME ===",
            f"PID: {info['pid']}",
            f"ROOT: {info['project_root']}",
            f"SOURCE_ROOT: {info['source_root']}",
            f"CWD: {info['cwd']}",
            f"PYTHON: {info['python']}",
            f"BUILD: {info['build']}",
            f"WORKFLOW_VERSION: {info['workflow_version']}",
            f"WORKFLOW_HASH: {info['workflow_hash']}",
            f"SKILL_BUNDLE_HASH: {_mapping_hash(info['skills'])}",
            "============================",
        )
    )


def _git_revision(root: Path) -> str:
    try:
        head = (root / ".git" / "HEAD").read_text(encoding="utf-8").strip()
        if head.startswith("ref: "):
            return (root / ".git" / head.removeprefix("ref: ")).read_text(
                encoding="utf-8"
            ).strip()
        return head
    except OSError:
        return "unavailable"


def _bundle_hash(root: Path, relative_paths: tuple[str, ...]) -> str:
    digest = hashlib.sha256()
    for relative_path in relative_paths:
        digest.update(relative_path.encode("utf-8"))
        digest.update(_file_hash(root / relative_path).encode("ascii"))
    return digest.hexdigest()


def _mapping_hash(values: dict[str, str]) -> str:
    digest = hashlib.sha256()
    for key in sorted(values):
        digest.update(key.encode("utf-8"))
        digest.update(values[key].encode("ascii"))
    return digest.hexdigest()


def _file_hash(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return "missing"


def _module_path(module_name: str) -> str:
    try:
        return inspect.getfile(importlib.import_module(module_name))
    except (ImportError, TypeError):
        return "unavailable"
