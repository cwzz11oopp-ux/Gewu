import json

import pytest

from backend.app.agents.supervisor import SupervisorAgent
from backend.app.storage.research_wiki import ResearchWikiStore
from backend.app.storage.research_wiki import WikiChangeSet
from backend.app.workflow.skills import SkillRegistry


def test_missing_wiki_initializes_and_returns_empty(tmp_path):
    wiki = ResearchWikiStore(tmp_path / "research-wiki")

    result = wiki.query("dropout robustness")

    assert result.status == "empty"
    assert result.papers == []
    assert result.warnings == ["WIKI_EMPTY"]
    assert (tmp_path / "research-wiki" / "graph" / "edges.jsonl").is_file()
    assert (tmp_path / "research-wiki" / "query_pack.md").is_file()


def test_corrupt_edges_degrades_without_raising(tmp_path):
    wiki = ResearchWikiStore(tmp_path / "research-wiki")
    wiki.initialize()
    (wiki.root / "papers" / "paper.json").write_text(
        json.dumps(
            {
                "id": "paper:dropout",
                "title": "Dropout Robustness",
                "abstract": "robust neural network training",
                "identifiers": {"doi": "10.1/dropout"},
                "verified": True,
                "tags": ["dropout", "robustness"],
            }
        ),
        encoding="utf-8",
    )
    (wiki.root / "graph" / "edges.jsonl").write_text("not-json\n", encoding="utf-8")

    result = wiki.query("dropout")

    assert result.status == "degraded"
    assert result.paper_ids == ["paper:dropout"]
    assert result.warnings == ["WIKI_DEGRADED"]


def test_no_match_is_empty_even_when_wiki_has_other_papers(tmp_path):
    wiki = ResearchWikiStore(tmp_path / "research-wiki")
    wiki.initialize()
    (wiki.root / "papers" / "paper.json").write_text(
        json.dumps({"id": "paper:vision", "title": "Image Segmentation", "abstract": "masks"}),
        encoding="utf-8",
    )

    result = wiki.query("language model")

    assert result.status == "empty"
    assert result.papers == []
    assert result.warnings == ["WIKI_EMPTY"]


def test_query_pack_is_deterministic_and_bounded(tmp_path):
    wiki = ResearchWikiStore(tmp_path / "research-wiki", query_pack_limit=300)
    wiki.initialize()
    for index in range(20):
        (wiki.root / "papers" / f"paper-{index}.json").write_text(
            json.dumps(
                {
                    "id": f"paper:{index}",
                    "title": f"Robust Study {index}",
                    "abstract": "robustness " * 20,
                    "tags": ["robustness"],
                }
            ),
            encoding="utf-8",
        )

    first = wiki.rebuild_query_pack()
    second = wiki.rebuild_query_pack()

    assert first == second
    assert len(first) <= 300
    assert first.endswith("\n")


def test_supervisor_ingests_verified_paper_and_rebuilds_query_pack(tmp_path):
    wiki = ResearchWikiStore(tmp_path / "research-wiki")
    supervisor = SupervisorAgent(SkillRegistry())
    changes = WikiChangeSet(
        papers=[
            {
                "title": "Deep Residual Learning",
                "abstract": "residual networks improve optimization variance",
                "identifiers": {"arxiv": "1512.03385"},
                "verified": True,
                "tags": ["variance"],
            }
        ],
        gaps=[{"id": "gap:G1", "text": "variance under fixed budget"}],
        edges=[
            {
                "source": "paper:1512-03385",
                "target": "gap:G1",
                "type": "addresses_gap",
            }
        ],
        origin_run_id="run_1",
    )

    result = supervisor.commit_wiki_changes(changes, wiki)

    assert result.paper_count == 1
    assert "paper:1512-03385" in wiki.query("variance").paper_ids
    assert "paper:1512-03385" in (wiki.root / "query_pack.md").read_text(encoding="utf-8")
    assert "origin_run_id: run_1" in (wiki.root / "log.md").read_text(encoding="utf-8")


def test_non_supervisor_actor_cannot_commit_wiki_changes(tmp_path):
    wiki = ResearchWikiStore(tmp_path / "research-wiki")

    with pytest.raises(ValueError, match="WIKI_COMMIT_REJECTED"):
        wiki.commit_changes(WikiChangeSet(origin_run_id="run_1"), actor="research")


def test_invalid_edge_does_not_partially_commit_paper_nodes(tmp_path):
    wiki = ResearchWikiStore(tmp_path / "wiki")
    changes = WikiChangeSet(
        papers=[
            {
                "title": "Atomic Paper",
                "identifiers": {"doi": "10.1/atomic"},
                "verified": True,
            }
        ],
        edges=[
            {"source": "invalid", "target": "paper:atomic", "type": "supports"}
        ],
        origin_run_id="run_1",
    )

    with pytest.raises(ValueError, match="WIKI_EDGE_INVALID"):
        wiki.commit_changes(changes, actor="supervisor")

    assert wiki.stats()["papers"] == 0


def test_duplicate_wiki_paper_is_upgraded_with_verified_local_metadata(tmp_path):
    wiki = ResearchWikiStore(tmp_path / "wiki")
    first = wiki.commit_changes(
        WikiChangeSet(
            papers=[
                {
                    "title": "Draft Metadata",
                    "identifiers": {"doi": "10.1/shared"},
                    "verified": False,
                }
            ],
            origin_run_id="run_1",
        ),
        actor="supervisor",
    )

    second = wiki.commit_changes(
        WikiChangeSet(
            papers=[
                {
                    "title": "Canonical Metadata",
                    "abstract": "Verified abstract",
                    "identifiers": {"doi": "10.1/shared"},
                    "verified": True,
                    "local_document_id": "paper_local",
                }
            ],
            origin_run_id="run_2",
        ),
        actor="supervisor",
    )

    stored = wiki.query("Canonical Metadata").papers[0]
    assert second.node_ids == first.node_ids
    assert second.paper_count == 0
    assert stored["verified"] is True
    assert stored["local_document_id"] == "paper_local"
    assert stored["title"] == "Canonical Metadata"
def test_query_applies_top_k_limit(tmp_path):
    wiki = ResearchWikiStore(tmp_path / "wiki")
    wiki.initialize()
    changes = WikiChangeSet(
        origin_run_id="run_1",
        papers=[{
            "title": f"Shared benchmark paper {index}",
            "abstract": "shared benchmark evidence",
            "identifiers": {"doi": f"10.1/{index}"},
            "verified": True,
        } for index in range(12)],
    )
    wiki.commit_changes(changes, actor="supervisor")

    assert len(wiki.query("shared benchmark", limit=3).papers) == 3
