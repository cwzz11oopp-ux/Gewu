from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

from pydantic import BaseModel, Field


class WikiQueryResult(BaseModel):
    status: str
    papers: list[dict] = Field(default_factory=list)
    gaps: list[dict] = Field(default_factory=list)
    failed_ideas: list[dict] = Field(default_factory=list)
    query_pack: str | None = None
    warnings: list[str] = Field(default_factory=list)

    @property
    def paper_ids(self) -> list[str]:
        return [str(paper.get("id")) for paper in self.papers]


class WikiChangeSet(BaseModel):
    papers: list[dict] = Field(default_factory=list)
    gaps: list[dict] = Field(default_factory=list)
    edges: list[dict] = Field(default_factory=list)
    origin_run_id: str
    knowledge_base_id: str = "default"


class WikiCommitResult(BaseModel):
    paper_count: int = 0
    gap_count: int = 0
    edge_count: int = 0
    node_ids: list[str] = Field(default_factory=list)


class ResearchWikiStore:
    def __init__(self, root: str | Path, query_pack_limit: int = 8_000) -> None:
        self.root = Path(root)
        self.query_pack_limit = query_pack_limit

    def initialize(self) -> None:
        for directory in ("papers", "gaps", "ideas", "experiments", "claims", "graph"):
            (self.root / directory).mkdir(parents=True, exist_ok=True)
        defaults = {
            self.root / "graph" / "edges.jsonl": "",
            self.root / "index.md": "# Research Wiki\n",
            self.root / "query_pack.md": "# Research Wiki Query Pack\n",
            self.root / "log.md": "# Research Wiki Audit Log\n",
        }
        for path, content in defaults.items():
            if not path.exists():
                path.write_text(content, encoding="utf-8")

    def query(
        self,
        topic: str,
        limit: int = 8,
        knowledge_base_id: str = "default",
    ) -> WikiQueryResult:
        scoped = self._scoped_store(knowledge_base_id)
        if scoped is not self:
            return scoped.query(topic, limit=limit)
        self.initialize()
        degraded = not self._edges_are_valid()
        papers = self._load_nodes("papers")
        gaps = self._load_nodes("gaps")
        failed_ideas = [
            item for item in self._load_nodes("ideas") if item.get("status") in {"failed", "partial"}
        ]
        terms = tuple(dict.fromkeys(_tokens(topic)))
        matches = [paper for paper in papers if _score(paper, terms) > 0] if terms else []
        matches.sort(key=lambda paper: (-_score(paper, terms), str(paper.get("id", ""))))
        matches = matches[: max(0, limit)]
        query_pack = self.rebuild_query_pack()

        if degraded:
            return WikiQueryResult(
                status="degraded",
                papers=matches,
                gaps=gaps,
                failed_ideas=failed_ideas,
                query_pack=query_pack,
                warnings=["WIKI_DEGRADED"],
            )
        if not matches:
            return WikiQueryResult(
                status="empty",
                papers=[],
                gaps=[],
                failed_ideas=[],
                query_pack=None,
                warnings=["WIKI_EMPTY"],
            )
        return WikiQueryResult(
            status="ready",
            papers=matches,
            gaps=gaps,
            failed_ideas=failed_ideas,
            query_pack=query_pack,
        )

    def rebuild_query_pack(self) -> str:
        self.initialize()
        sections = ["# Research Wiki Query Pack\n"]
        for paper in sorted(self._load_nodes("papers"), key=lambda item: str(item.get("id", ""))):
            section = (
                f"## {paper.get('title') or paper.get('id')}\n"
                f"ID: {paper.get('id', '')}\n"
                f"Abstract: {paper.get('abstract', '')}\n"
            )
            if len("\n".join(sections) + section) > self.query_pack_limit:
                break
            sections.append(section)
        content = "\n".join(sections)
        if len(content) > self.query_pack_limit:
            content = "# Research Wiki Query Pack\n"[: self.query_pack_limit]
        if content and not content.endswith("\n"):
            content += "\n"
        path = self.root / "query_pack.md"
        temp = path.with_suffix(".tmp")
        temp.write_text(content, encoding="utf-8")
        temp.replace(path)
        return content

    def stats(self, knowledge_base_id: str = "default") -> dict[str, int]:
        scoped = self._scoped_store(knowledge_base_id)
        if scoped is not self:
            return scoped.stats()
        self.initialize()
        return {
            "papers": len(self._load_nodes("papers")),
            "gaps": len(self._load_nodes("gaps")),
            "ideas": len(self._load_nodes("ideas")),
            "experiments": len(self._load_nodes("experiments")),
            "claims": len(self._load_nodes("claims")),
        }

    def commit_changes(
        self,
        changes: WikiChangeSet,
        actor: str,
    ) -> WikiCommitResult:
        scoped = self._scoped_store(changes.knowledge_base_id)
        if scoped is not self:
            return scoped.commit_changes(
                changes.model_copy(update={"knowledge_base_id": "default"}),
                actor,
            )
        if actor != "supervisor":
            raise ValueError("WIKI_COMMIT_REJECTED")
        for gap in changes.gaps:
            if not str(gap.get("id") or "").startswith("gap:"):
                raise ValueError("WIKI_NODE_ID_INVALID")
        for edge in changes.edges:
            if not _valid_node_id(str(edge.get("source") or "")) or not _valid_node_id(
                str(edge.get("target") or "")
            ):
                raise ValueError("WIKI_EDGE_INVALID")
        self.initialize()
        existing_papers = self._load_nodes("papers")
        known_papers = {
            _paper_key(paper): paper
            for paper in existing_papers
            if _paper_key(paper)
        }
        node_ids: list[str] = []
        paper_count = 0
        for paper in changes.papers:
            key = _paper_key(paper)
            existing = known_papers.get(key) if key else None
            canonical_id = str(existing.get("id")) if existing else None
            if not canonical_id:
                canonical_id = _paper_id(paper)
                value = {
                    **paper,
                    "id": canonical_id,
                    "origin_run_id": changes.origin_run_id,
                }
                self._write_node("papers", canonical_id, value)
                if key:
                    known_papers[key] = value
                paper_count += 1
            else:
                updates = {
                    name: value
                    for name, value in paper.items()
                    if value not in (None, "", [], {})
                }
                value = {
                    **existing,
                    **updates,
                    "id": canonical_id,
                    "origin_run_id": existing.get("origin_run_id")
                    or changes.origin_run_id,
                    "last_updated_run_id": changes.origin_run_id,
                    "verified": bool(existing.get("verified"))
                    or bool(paper.get("verified")),
                    "identifiers": {
                        **(existing.get("identifiers") or {}),
                        **(paper.get("identifiers") or {}),
                    },
                }
                self._write_node("papers", canonical_id, value)
                if key:
                    known_papers[key] = value
            node_ids.append(canonical_id)

        gap_count = 0
        for gap in changes.gaps:
            gap_id = str(gap.get("id") or "")
            self._write_node(
                "gaps",
                gap_id,
                {**gap, "origin_run_id": changes.origin_run_id},
            )
            node_ids.append(gap_id)
            gap_count += 1

        edges_path = self.root / "graph" / "edges.jsonl"
        existing_edges = [
            json.loads(line)
            for line in edges_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        edge_keys = {_edge_key(edge) for edge in existing_edges}
        edge_count = 0
        for edge in changes.edges:
            key = _edge_key(edge)
            if key not in edge_keys:
                existing_edges.append(edge)
                edge_keys.add(key)
                edge_count += 1
        temp_edges = edges_path.with_suffix(".tmp")
        temp_edges.write_text(
            "".join(json.dumps(edge, ensure_ascii=False) + "\n" for edge in existing_edges),
            encoding="utf-8",
        )
        temp_edges.replace(edges_path)

        self._rebuild_index()
        self.rebuild_query_pack()
        with (self.root / "log.md").open("a", encoding="utf-8") as handle:
            handle.write(
                "\n## Commit\n"
                f"- timestamp: {datetime.now(timezone.utc).isoformat()}\n"
                f"- actor: {actor}\n"
                f"- origin_run_id: {changes.origin_run_id}\n"
                f"- nodes: {', '.join(node_ids)}\n"
                f"- edges_added: {edge_count}\n"
            )
        return WikiCommitResult(
            paper_count=paper_count,
            gap_count=gap_count,
            edge_count=edge_count,
            node_ids=list(dict.fromkeys(node_ids)),
        )

    def _scoped_store(self, knowledge_base_id: str) -> "ResearchWikiStore":
        scope = knowledge_base_id.strip() or "default"
        if scope == "default":
            return self
        digest = hashlib.sha256(scope.encode("utf-8")).hexdigest()[:12]
        return ResearchWikiStore(
            self.root / "knowledge-bases" / digest,
            query_pack_limit=self.query_pack_limit,
        )

    def _load_nodes(self, directory: str) -> list[dict]:
        nodes: list[dict] = []
        for path in sorted((self.root / directory).glob("*.json")):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(value, dict):
                nodes.append(value)
        return nodes

    def _edges_are_valid(self) -> bool:
        path = self.root / "graph" / "edges.jsonl"
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip() and not isinstance(json.loads(line), dict):
                    return False
        except (OSError, json.JSONDecodeError):
            return False
        return True

    def _write_node(self, directory: str, node_id: str, value: dict) -> None:
        path = self.root / directory / f"{hashlib.sha256(node_id.encode('utf-8')).hexdigest()[:16]}.json"
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)

    def _rebuild_index(self) -> None:
        lines = ["# Research Wiki", ""]
        for paper in sorted(self._load_nodes("papers"), key=lambda item: str(item.get("id", ""))):
            lines.append(f"- {paper.get('id')}: {paper.get('title', '')}")
        path = self.root / "index.md"
        temp = path.with_suffix(".tmp")
        temp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        temp.replace(path)


def _tokens(value: str) -> list[str]:
    return [token.lower() for token in re.findall(r"[\w-]+", value, flags=re.UNICODE)]


def _score(node: dict, terms: tuple[str, ...]) -> int:
    title = str(node.get("title") or "").lower()
    abstract = str(node.get("abstract") or "").lower()
    tags = " ".join(str(item) for item in node.get("tags") or []).lower()
    return sum(
        (4 if term in title else 0)
        + (2 if term in tags else 0)
        + (1 if term in abstract else 0)
        for term in terms
    )


def _paper_key(paper: dict) -> str:
    identifiers = paper.get("identifiers") or {}
    if identifiers.get("doi"):
        return f"doi:{str(identifiers['doi']).lower()}"
    if identifiers.get("arxiv"):
        return f"arxiv:{str(identifiers['arxiv']).lower()}"
    title = re.sub(r"[^a-z0-9]+", "", str(paper.get("title") or "").lower())
    return f"title:{title}" if title else ""


def _paper_id(paper: dict) -> str:
    identifiers = paper.get("identifiers") or {}
    value = identifiers.get("arxiv") or identifiers.get("doi") or paper.get("title") or "paper"
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", str(value)).strip("-").lower()
    if not slug:
        slug = hashlib.sha256(json.dumps(paper, sort_keys=True).encode("utf-8")).hexdigest()[:12]
    return f"paper:{slug}"


def _valid_node_id(value: str) -> bool:
    return any(value.startswith(prefix) for prefix in ("paper:", "gap:", "idea:", "exp:", "claim:"))


def _edge_key(edge: dict) -> tuple[str, str, str]:
    return (
        str(edge.get("source") or ""),
        str(edge.get("target") or ""),
        str(edge.get("type") or ""),
    )
