"""Read-only GitHub source inspection for optional research context.

This module deliberately does not clone repositories or execute repository
content. It reads a bounded public GitHub tree through HTTPS, then derives
line-addressable evidence only from bytes that were actually returned.
"""

from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen


class GitHubGateway(Protocol):
    def get_json(self, url: str) -> dict[str, Any]: ...
    def get_text(self, url: str) -> str: ...


class UrllibGitHubGateway:
    """Minimal HTTPS GET gateway; intentionally exposes no shell or execution API."""

    def _read(self, url: str) -> bytes:
        request = Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "Gewu-readonly-source-inspector"})
        with urlopen(request, timeout=12) as response:  # nosec B310: URL is fixed to GitHub API/raw hosts below
            return response.read()

    def get_json(self, url: str) -> dict[str, Any]:
        value = json.loads(self._read(url).decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("GITHUB_SOURCE_INVALID_JSON")
        return value

    def get_text(self, url: str) -> str:
        return self._read(url).decode("utf-8", errors="replace")


@dataclass(frozen=True)
class GitHubSourceInspection:
    repository_url: str
    github_source_status: str
    repository_commit: str | None = None
    code_evidence: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    inspected_files: list[str] = field(default_factory=list)

    def model_dump(self) -> dict[str, Any]:
        return {
            "repository_url": self.repository_url,
            "github_source_status": self.github_source_status,
            "repository_commit": self.repository_commit,
            "code_evidence": self.code_evidence,
            "code_evidence_ids": [item["code_evidence_id"] for item in self.code_evidence],
            "warnings": self.warnings,
            "inspected_files": self.inspected_files,
        }


class GitHubSourceInspector:
    """Bounded source reader for a public ``https://github.com/owner/repo`` URL."""

    _ALLOWED_SUFFIXES = (".py", ".pyi", ".json", ".yaml", ".yml", ".toml")
    _MAX_FILES = 40
    _MAX_FILE_CHARS = 40_000

    def __init__(self, gateway: GitHubGateway | None = None) -> None:
        self.gateway = gateway or UrllibGitHubGateway()

    def inspect(self, repository_url: str | None) -> GitHubSourceInspection:
        raw = (repository_url or "").strip()
        if not raw:
            return GitHubSourceInspection("", "not_provided")
        try:
            owner, repository, normalized = self._parse_repository_url(raw)
            metadata = self.gateway.get_json(f"https://api.github.com/repos/{quote(owner)}/{quote(repository)}")
            default_branch = str(metadata.get("default_branch") or "").strip()
            if not default_branch:
                raise ValueError("GITHUB_SOURCE_DEFAULT_BRANCH_MISSING")
            commit_payload = self.gateway.get_json(f"https://api.github.com/repos/{quote(owner)}/{quote(repository)}/commits/{quote(default_branch, safe='')}")
            commit = str(commit_payload.get("sha") or "").strip()
            if not commit:
                raise ValueError("GITHUB_SOURCE_COMMIT_MISSING")
            tree_payload = self.gateway.get_json(f"https://api.github.com/repos/{quote(owner)}/{quote(repository)}/git/trees/{quote(commit, safe='')}?recursive=1")
            entries = tree_payload.get("tree")
            if not isinstance(entries, list):
                raise ValueError("GITHUB_SOURCE_TREE_MISSING")
            warnings: list[str] = []
            evidence: list[dict[str, Any]] = []
            inspected: list[str] = []
            for path in self._selected_paths(entries):
                raw_url = f"https://raw.githubusercontent.com/{quote(owner)}/{quote(repository)}/{quote(commit, safe='')}/{quote(path, safe='/')}"
                try:
                    content = self.gateway.get_text(raw_url)
                    if len(content) > self._MAX_FILE_CHARS:
                        content = content[:self._MAX_FILE_CHARS]
                        warnings.append(f"GITHUB_SOURCE_FILE_TRUNCATED:{path}")
                    inspected.append(path)
                    evidence.extend(self._evidence_from_file(normalized, commit, path, content))
                except Exception as exc:
                    warnings.append(f"GITHUB_SOURCE_FILE_READ_FAILED:{path}:{self._error_code(exc)}")
            # A repository whose selected files could not be read at all is
            # unavailable, not a successful empty inspection. Partial reads
            # remain explicitly warned and can still yield real evidence.
            if not inspected:
                return GitHubSourceInspection(normalized, "unavailable", commit, [], warnings or ["GITHUB_SOURCE_READ_FAILED"], [])
            if not evidence:
                warnings.append("GITHUB_SOURCE_NO_CODE_EVIDENCE")
            return GitHubSourceInspection(normalized, "parsed", commit, evidence, warnings, inspected)
        except Exception as exc:
            return GitHubSourceInspection(raw, "unavailable", warnings=[self._error_code(exc)])

    @staticmethod
    def _parse_repository_url(raw: str) -> tuple[str, str, str]:
        parsed = urlparse(raw)
        if parsed.scheme != "https" or parsed.hostname not in {"github.com", "www.github.com"} or parsed.username or parsed.password:
            raise ValueError("GITHUB_SOURCE_INVALID_URL")
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) != 2:
            raise ValueError("GITHUB_SOURCE_INVALID_URL")
        owner, repository = parts
        if repository.endswith(".git"):
            repository = repository[:-4]
        if not repository:
            raise ValueError("GITHUB_SOURCE_INVALID_URL")
        return owner, repository, f"https://github.com/{owner}/{repository}"

    def _selected_paths(self, entries: list[Any]) -> list[str]:
        paths: list[str] = []
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("type") != "blob":
                continue
            path = str(entry.get("path") or "")
            name = path.rsplit("/", 1)[-1].lower()
            if path.endswith(self._ALLOWED_SUFFIXES) or name.startswith("readme"):
                paths.append(path)
        return sorted(dict.fromkeys(paths))[: self._MAX_FILES]

    def _evidence_from_file(self, repository_url: str, commit: str, path: str, content: str) -> list[dict[str, Any]]:
        if not path.endswith((".py", ".pyi")):
            return []
        try:
            tree = ast.parse(content, filename=path)
        except SyntaxError as exc:
            raise ValueError("GITHUB_SOURCE_PARSE_FAILED") from exc
        file_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        output: list[dict[str, Any]] = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            symbol = node.name
            line_start = int(node.lineno)
            line_end = int(getattr(node, "end_lineno", node.lineno))
            kind = "class" if isinstance(node, ast.ClassDef) else "function"
            identity = f"{repository_url}:{commit}:{path}:{symbol}:{line_start}:{line_end}:{file_hash}"
            output.append({
                "code_evidence_id": "CODE-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12],
                "repository_url": repository_url, "repository_commit": commit,
                "source_file": path, "symbol": symbol,
                "line_start": line_start, "line_end": line_end,
                "claim": f"{symbol} is declared as a {kind} in {path}.", "file_hash": file_hash,
            })
        return output

    @staticmethod
    def _error_code(exc: Exception) -> str:
        message = str(exc).strip()
        return message.split(":", 1)[0] if message.startswith("GITHUB_SOURCE_") else "GITHUB_SOURCE_READ_FAILED"
