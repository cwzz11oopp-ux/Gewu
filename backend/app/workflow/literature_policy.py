from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re

from pydantic import BaseModel


class LiteratureIntent(StrEnum):
    BASELINE = "BASELINE"
    DIRECT_METHOD = "DIRECT_METHOD"
    MECHANISM = "MECHANISM"
    BENCHMARK = "BENCHMARK"
    EVALUATION = "EVALUATION"
    CONTRADICTORY_EVIDENCE = "CONTRADICTORY_EVIDENCE"
    RELATED_APPLICATION = "RELATED_APPLICATION"


class LiteratureQuery(BaseModel):
    query: str
    intent: LiteratureIntent = LiteratureIntent.RELATED_APPLICATION
    target_gap: str = ""


@dataclass(frozen=True)
class LiteratureRetrievalPolicy:
    # These are operational safety ceilings, never a scientific completion
    # condition.  Coverage/saturation is evaluated after synthesis.
    max_query_count: int = 20
    max_results_per_query: int = 20
    max_results_per_source: int = 100
    max_candidate_count: int = 100
    # ``core_references`` is a compact UI/legacy compatibility view.  It is not
    # the literature collection used to build research synthesis.
    max_core_reference_count: int = 24
    minimum_theme_count: int = 2
    minimum_method_coverage: float = 0.60
    minimum_conclusion_coverage: float = 0.60
    minimum_limitations_coverage: float = 0.50
    minimum_future_work_coverage: float = 0.50
    minimum_gap_stability: float = 0.70
    saturation_new_information_rate: float = 0.10
    recency_weight: float = 0.10
    relevance_weight: float = 0.45
    quality_weight: float = 0.25
    diversity_weight: float = 0.20


def normalize_queries(values: list[object], policy: LiteratureRetrievalPolicy) -> list[LiteratureQuery]:
    queries: list[LiteratureQuery] = []
    seen: list[set[str]] = []
    for value in values:
        if isinstance(value, dict):
            try:
                spec = LiteratureQuery.model_validate(value)
            except ValueError:
                spec = LiteratureQuery(query=str(value.get("query") or ""))
        else:
            spec = LiteratureQuery(query=str(value))
        query = re.sub(r"\s+", " ", spec.query).strip()
        if not query:
            continue
        tokens = set(re.findall(r"[a-z0-9]+", query.casefold()))
        if any(_similar(tokens, prior) for prior in seen):
            continue
        seen.append(tokens)
        queries.append(spec.model_copy(update={"query": query}))
        if len(queries) >= policy.max_query_count:
            break
    return queries


def _similar(left: set[str], right: set[str]) -> bool:
    if not left or not right:
        return False
    return len(left & right) / len(left | right) >= 0.8
