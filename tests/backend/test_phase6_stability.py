import httpx
import pytest

from backend.app.config import Settings
from backend.app.providers.llm import DeepSeekLLMProvider
from backend.app.workflow.engine import WorkflowEngine
from backend.app.workflow.knowledge import _english_query_fallback


def test_deepseek_transport_error_is_provider_neutral_and_bounded():
    def fail(request):
        raise httpx.ConnectError("offline", request=request)

    settings = Settings.from_env({
        "DEEPSEEK_API_KEY": "test-key", "QWEN_RETRIES_PER_MODEL": "0",
    })
    provider = DeepSeekLLMProvider(
        settings, client=httpx.Client(transport=httpx.MockTransport(fail))
    )
    with pytest.raises(RuntimeError, match="MODEL_REQUEST_FAILED:provider=deepseek"):
        provider.generate_json("provider.preflight", {}, {})


def test_candidate_scopes_are_independent_and_bounded():
    cards = [
        {"title": f"paper-{index}", "url": f"https://example/{index}", "verified": True,
         "relevance": index / 20, "reliability": 1.0}
        for index in range(20)
    ]
    first = {"evidence_basis": [{"source_title": "paper-1"}]}
    second = {"evidence_basis": [{"source_title": "paper-18"}]}
    first_scope = WorkflowEngine._focused_evidence_for_candidate(cards, first)
    second_scope = WorkflowEngine._focused_evidence_for_candidate(cards, second)
    assert len(first_scope) <= 12 and len(second_scope) <= 12
    assert first_scope[0]["title"] == "paper-1"
    assert second_scope[0]["title"] == "paper-18"


def test_unmapped_non_ascii_query_has_no_generic_fallback():
    assert _english_query_fallback("完全未映射的中文检索词") == ""
