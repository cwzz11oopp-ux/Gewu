"""Focused tests for the hypothesis literature-input optimization.

The hypothesis step must feed EVERY valid paper to the LLM (filter+dedup then
compact), never a representative subset and never a character-budget slice.  It
must also give hypothesis.generate a bounded output token cap and run it with
thinking DISABLED -- both strictly for hypothesis.generate, never for the other
reasoning tasks.

The relevance filter is question-driven: English terms are derived from the
current research question (no hardcoded domain vocabulary), and a card is
removed only when it shares none of those terms AND its existing retrieval
relevance is below the conservative floor.
"""

import json

import httpx
import pytest

from backend.app.config import Settings
from backend.app.providers.llm import (
    QwenLLMProvider,
    _max_tokens_payload,
    _TASK_DISABLE_THINKING,
)
from backend.app.workflow.prompt_context import (
    _first_sentence,
    _question_terms,
    build_hypothesis_context,
    filter_and_dedupe_hypothesis_cards,
)

COMPACT_FIELDS = frozenset({
    "paper_id", "title", "year", "research_goal", "core_method", "contribution",
    "task", "dataset", "baseline", "protocol", "metrics", "key_results",
    "improvement", "ablation", "limitations_gap", "future_work", "evidence",
    "provenance",
})

# The real run's research question (Chinese, names Fashion-MNIST + SE + ECA).
QUESTION = (
    "在 Fashion-MNIST 数据集上，集成轻量级通道注意力机制（如 SE 或 ECA 模块）"
    "能否显著降低易混淆服装类别的分类错误率，且计算开销增加受限？"
)


def _card(
    *,
    title="Lightweight Channel Attention for Efficient CNNs",
    claim=(
        "Attention mechanisms have become integral to modern convolutional "
        "neural networks, delivering notable performance improvements with "
        "minimal computational overhead. However, the efficiency-accuracy "
        "trade-off of different channel attention designs remains underexplored. "
        "This work presents an empirical study comparing SE, ECA and a proposed "
        "Lite Channel Attention module across ResNet-18 and MobileNet on "
        "Fashion-MNIST, showing a consistent accuracy gain for confusable "
        "garment classes with negligible parameter cost."
    ),
    doi="10.0000/example.1",
    arxiv="",
    url="https://arxiv.org/abs/2601.00001",
    relevance=0.42,
    retrieval_intent="DIRECT_METHOD",
    target_gap="channel-attention cost vs accuracy trade-off unexplored",
    source="arxiv",
):
    identifiers = {}
    if doi:
        identifiers["doi"] = doi
    if arxiv:
        identifiers["arxiv"] = arxiv
    return {
        "title": title,
        "claim": claim,
        "url": url,
        "identifiers": identifiers,
        "relevance": relevance,
        "retrieval_intent": retrieval_intent,
        "target_gap": target_gap,
        "source": source,
        "year": 2026,
    }


def test_compact_card_has_exactly_the_18_fields():
    card = build_hypothesis_context(_card())
    assert set(card) == COMPACT_FIELDS


def test_compact_card_maps_equivalent_fields_without_inventing():
    card = build_hypothesis_context(_card())
    assert card["paper_id"].startswith("PAPER-")
    assert card["title"] == "Lightweight Channel Attention for Efficient CNNs"
    assert card["year"] == 2026
    # research_goal = first sentence of the claim; evidence = full claim.
    assert card["research_goal"].startswith("Attention mechanisms have become integral")
    assert card["evidence"] == _card()["claim"]
    # No field is filled by a fabricated value; unknown fields stay empty.
    assert card["core_method"] == ""
    assert card["dataset"] == ""
    assert card["metrics"] == []
    assert card["future_work"] == []
    # task keeps the retrieval role as the closest equivalent; gap keeps target_gap.
    assert card["task"] == "DIRECT_METHOD"
    assert card["limitations_gap"] == "channel-attention cost vs accuracy trade-off unexplored"
    # provenance carries the stable identifier chain.
    assert card["provenance"] == {
        "source": "arxiv",
        "identifier": "10.0000/example.1",
        "url": "https://arxiv.org/abs/2601.00001",
    }


def test_compact_card_prefers_arxiv_identifier_when_no_doi():
    card = build_hypothesis_context(_card(doi="", arxiv="2601.12345"))
    assert card["provenance"]["identifier"] == "2601.12345"
    assert card["paper_id"] != build_hypothesis_context(_card())["paper_id"]


def test_no_llm_fields_added_and_no_bloated_metadata():
    card = build_hypothesis_context(_card())
    # Compact card must not smuggle the full model dump into the prompt.
    for heavy in ("authors", "identifiers", "conflict_notes", "reliability",
                  "verified", "local_document_id", "model_dump"):
        assert heavy not in card
    # research_goal is a truncated sentence, never the whole abstract.
    assert len(card["research_goal"]) < len(card["evidence"])


def test_filter_keeps_every_valid_paper_no_truncation():
    cards = [
        _card(doi="10.0000/a"),
        _card(doi="10.0000/b"),
        _card(doi="10.0000/c"),
    ]
    result = filter_and_dedupe_hypothesis_cards(cards, research_question=QUESTION)
    assert result["counts"] == {
        "raw_count": 3, "irrelevant_removed": 0,
        "duplicate_merged": 0, "valid_count": 3,
    }
    assert len(result["cards"]) == 3


def test_filter_removes_only_clearly_irrelevant_low_score_papers():
    cards = [
        # Question-overlapping despite a low score -> kept.
        _card(doi="10.0000/keep", relevance=0.1),
        # No question vocabulary AND essentially-rejected score -> removed.
        _card(title="Algebra corrigendum to a theorem",
              claim="We correct a sign error in the proof of Theorem 4.2.",
              doi="10.0000/drop", relevance=0.03),
        # No question vocabulary but high-scored -> kept (relevance floor).
        _card(title="Mechanical mechanism synthesis",
              claim="We synthesize planar mechanisms from motion constraints.",
              doi="10.0000/high", relevance=0.8),
    ]
    result = filter_and_dedupe_hypothesis_cards(cards, research_question=QUESTION)
    assert result["counts"]["raw_count"] == 3
    assert result["counts"]["irrelevant_removed"] == 1
    assert result["counts"]["valid_count"] == 2
    ids = {card["identifiers"]["doi"] for card in result["cards"]}
    assert ids == {"10.0000/keep", "10.0000/high"}


def test_question_overlap_keeps_even_very_low_score_papers():
    # A paper mentioning the question's own terms (SE / Fashion-MNIST) is kept
    # even with a near-zero retrieval score: question overlap is a keep signal.
    cards = [
        _card(title="SE blocks reduce confusable Fashion-MNIST errors",
              claim="We attach SE blocks to a Fashion-MNIST classifier.",
              doi="10.0000/keep", relevance=0.03),
        # No question vocabulary and low-scored -> the one removed.
        _card(title="Visual Attention Network",
              claim="We propose a visual attention network as a general vision backbone.",
              doi="10.0000/drop", relevance=0.05),
    ]
    result = filter_and_dedupe_hypothesis_cards(cards, research_question=QUESTION)
    assert result["counts"]["irrelevant_removed"] == 1
    assert result["counts"]["valid_count"] == 1
    assert result["cards"][0]["identifiers"]["doi"] == "10.0000/keep"


def test_question_terms_are_word_boundary_safe():
    # "se" must match the SE block, not the "se" inside "sentence" / "segment".
    keep = _card(title="SE block evaluation",
                 claim="We compare SE blocks against baselines on the dataset.",
                 doi="10.0000/se", relevance=0.02)
    drop = _card(title="Nucleus sentence segmentation",
                 claim="We segment sentences in the nuclear chemistry corpus.",
                 doi="10.0000/no", relevance=0.02)
    result = filter_and_dedupe_hypothesis_cards(
        [keep, drop], research_question="Fashion-MNIST SE 通道注意力"
    )
    assert result["counts"]["irrelevant_removed"] == 1
    assert result["counts"]["valid_count"] == 1
    assert result["cards"][0]["identifiers"]["doi"] == "10.0000/se"


def test_empty_question_removes_nothing():
    # A question with no English tokens yields no terms; the filter is then a
    # pure dedup pass -- conservative by construction, never removing a paper.
    cards = [
        _card(title="Algebra corrigendum to a theorem",
              claim="We correct a sign error in the proof of Theorem 4.2.",
              doi="10.0000/nuclear", relevance=0.001),
        _card(doi="10.0000/x", relevance=0.9),
    ]
    result = filter_and_dedupe_hypothesis_cards(cards, research_question="")
    assert result["counts"]["raw_count"] == 2
    assert result["counts"]["irrelevant_removed"] == 0
    assert result["counts"]["valid_count"] == 2


def test_question_terms_derived_from_question():
    # English tokens >= 2 chars, casefolded, sorted, deduped -- no domain table.
    assert _question_terms(
        "在 Fashion-MNIST 数据集上集成 SE 或 ECA 模块？"
    ) == ("eca", "fashion", "mnist", "se")
    assert _question_terms("") == ()
    assert _question_terms("仅中文问题没有英文？") == ()


def test_dedupe_merges_by_doi_then_arxiv_then_title():
    cards = [
        _card(doi="10.0000/same", title="Duplicate One"),
        _card(doi="10.0000/same", title="Duplicate Two"),
        _card(doi="", arxiv="2601.99999", title="No-doi paper"),
        _card(doi="", arxiv="2601.99999", title="No-doi paper again"),
        _card(doi="", arxiv="", title="   Lightweight   Channel   Attention "),
        _card(doi="", arxiv="", title="lightweight channel attention"),
        # No anchor at all -> never merged, always kept.
        _card(doi="", arxiv="", title=""),
        _card(doi="", arxiv="", title=""),
    ]
    result = filter_and_dedupe_hypothesis_cards(cards, research_question=QUESTION)
    assert result["counts"] == {
        "raw_count": 8, "irrelevant_removed": 0,
        "duplicate_merged": 3, "valid_count": 5,
    }


def test_build_hypothesis_context_accepts_pydantic_style_model_dump():
    # filter_and_dedupe must accept Pydantic models (model_dump) and plain dicts.
    class FakeModel:
        def model_dump(self):
            return _card(doi="10.0000/py")

    result = filter_and_dedupe_hypothesis_cards([FakeModel()])
    assert result["counts"]["valid_count"] == 1
    assert result["cards"][0]["identifiers"]["doi"] == "10.0000/py"


def test_first_sentence_extractor():
    assert _first_sentence("") == ""
    assert _first_sentence("One. Two.") == "One."
    assert _first_sentence("Short claim") == "Short claim"


def test_max_tokens_override_for_bounded_tasks():
    # Bounded-output tasks win over both no-cap and a global cap: hypothesis
    # generation is kept tight (4000); full-plan regeneration is capped high
    # enough to never truncate a real plan but stops runaway generation (30000).
    assert _max_tokens_payload("hypothesis.generate", 0) == {"max_tokens": 4000}
    assert _max_tokens_payload("hypothesis.generate", 3000) == {"max_tokens": 4000}
    assert _max_tokens_payload("planning.build_plan", 0) == {"max_tokens": 30000}
    assert _max_tokens_payload("planning.build_plan", 3000) == {"max_tokens": 30000}
    assert _max_tokens_payload("planning.revise_from_review", 0) == {"max_tokens": 30000}
    assert _max_tokens_payload("planning.revise_from_review", 3000) == {"max_tokens": 30000}
    # Every other task keeps the provider default.
    assert _max_tokens_payload("research.structure_problem", 0) == {}
    assert _max_tokens_payload("research.structure_problem", 3000) == {"max_tokens": 3000}
    assert _max_tokens_payload("critic.evidence_reasoning", 0) == {}
    assert _max_tokens_payload("critic.evidence_reasoning", 3000) == {"max_tokens": 3000}


def test_compact_card_serialization_stays_small():
    card = build_hypothesis_context(_card())
    size = len(json.dumps(card, ensure_ascii=False))
    claim = _card()["claim"]
    # research_goal is only the first sentence, so the claim text appears at
    # most once in full; the card must not grow unboundedly with metadata.
    assert size < len(claim) * 3 + 300
    assert len(card["research_goal"]) < len(card["evidence"])


def test_thinking_disabled_only_for_hypothesis_generate(tmp_path, monkeypatch):
    """enable_thinking is False for the timeout-measured reasoning tasks only.

    hypothesis.generate plus the evidence_reasoning-step candidates
    (idea_selection.review, critic.evidence_reasoning) run with thinking off;
    the remaining reasoning tasks (here planning.review_plan) keep the reasoning
    policy's enable_thinking=True.  Asserted on the actual HTTP body the
    provider would send.
    """
    from backend.app.storage.runtime_config import RuntimeConfigStore

    store = RuntimeConfigStore(str(tmp_path / "data"))
    settings = store.apply(Settings.from_env({
        "LLM_PROVIDER": "qwen",
        "QWEN_API_KEY": "test-key",
    }))
    provider = QwenLLMProvider(settings, client=object())

    captured = []

    def recording_post(self, task, inputs, schema_hint, instructions, model,
                       enable_thinking, timeout_seconds):
        captured.append({"task": task, "enable_thinking": enable_thinking})
        content = json.dumps(
            {"candidates": []} if task == "hypothesis.generate"
            else {"status": "accepted"}
        )
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    monkeypatch.setattr(QwenLLMProvider, "_post", recording_post)

    provider.generate_json("hypothesis.generate", {}, {"candidates": "array"})
    provider.generate_json("idea_selection.review", {}, {"status": "string"})
    provider.generate_json("critic.evidence_reasoning", {}, {"status": "string"})
    provider.generate_json("planning.review_plan", {}, {"status": "string"})

    by_task = {call["task"]: call["enable_thinking"] for call in captured}
    assert by_task["hypothesis.generate"] is False
    assert by_task["idea_selection.review"] is False
    assert by_task["critic.evidence_reasoning"] is False
    # A remaining reasoning task keeps the thinking path on.
    assert by_task["planning.review_plan"] is True
    # The disable-set contains exactly the three exempted tasks.
    assert _TASK_DISABLE_THINKING == frozenset({
        "hypothesis.generate",
        "idea_selection.review",
        "critic.evidence_reasoning",
    })
