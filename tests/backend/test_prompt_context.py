import json

from backend.app.providers.qwen_diagnostics import request_metrics
from backend.app.workflow.prompt_context import (
    PromptContextBudget,
    compact_problem,
    literature_card,
    select_units,
)


def test_compact_literature_units_respect_reference_budget():
    budget = PromptContextBudget(max_reference_chars=700)
    cards = [literature_card({
        "title": f"Paper {index}",
        "year": 2024,
        "source": "arxiv",
        "identifiers": {"arxiv": str(index)},
        "verified": True,
        "claim": "evidence " * 20,
    }) for index in range(20)]
    selected = select_units(cards, budget.max_reference_chars)

    assert selected
    assert len(json.dumps(selected, ensure_ascii=False, separators=(",", ":"))) <= 700


def test_request_component_accounting_matches_canonical_request():
    metrics = request_metrics(
        "idea_selection.review",
        {"problem": {"problem_statement": "p"}, "candidates": [{"claim": "h"}],
         "evidence_units": [{"reference_id": "doi:1", "title": "e"}]},
        {"evaluations": ["object"]},
        "skill",
    )
    components = metrics["component_chars"]

    assert components["total_chars"] == metrics["request_characters"]
    assert components["estimated_tokens"] == (metrics["request_characters"] + 3) // 4
    assert components["selected_reference_chars"] > 0


def test_compact_problem_omits_full_dataset_inventory():
    value = compact_problem({
        "problem_statement": "p",
        "dataset_profile": {
            "contract_id": "dataset_1",
            "root": "datasets",
            "file_count": 100,
            "files": [{"relative_path": f"file-{index}"} for index in range(100)],
            "schemas": [{"path": f"file-{index}", "columns": ["a", "b"]} for index in range(100)],
        },
    })

    assert value["dataset_profile"] == {
        "contract_id": "dataset_1", "root": "datasets", "file_count": 100
    }
