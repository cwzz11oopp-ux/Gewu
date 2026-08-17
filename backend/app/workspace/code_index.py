from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from backend.app.workspace.repository import RepositoryInspection


@dataclass(frozen=True)
class CodeIndex:
    entrypoints: tuple[str, ...]
    configs: tuple[str, ...]
    tests: tuple[str, ...]
    source_files: tuple[str, ...]


def build_code_index(inspection: RepositoryInspection) -> CodeIndex:
    source_suffixes = {".py", ".ts", ".tsx", ".js", ".jsx", ".java", ".cpp", ".c"}
    return CodeIndex(
        entrypoints=tuple(inspection.entrypoint_candidates),
        configs=tuple(inspection.config_files),
        tests=tuple(inspection.test_files),
        source_files=tuple(
            item
            for item in inspection.tracked_files
            if Path(item).suffix.lower() in source_suffixes
        ),
    )
