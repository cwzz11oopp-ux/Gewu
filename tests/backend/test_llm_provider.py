import json

import httpx
import pytest

from backend.app.config import Settings
from backend.app.providers.llm import (
    LLMRequestCancelled,
    MockLLMProvider,
    QwenLLMProvider,
    get_llm_provider,
    normalize_task_output_shape,
)


def test_mock_llm_marks_fallback_metadata():
    provider = MockLLMProvider()

    result = provider.generate_json("research", {"problem": "train cnn"}, {"problem_statement": "string"})

    assert result["provider_mode"] == "mock"
    assert result["fallback_used"] is True
    assert "Development fallback" in result["fallback_reason"]


def test_competition_mode_rejects_mock_llm_provider():
    settings = Settings.from_env({"COMPETITION_MODE": "true", "LLM_PROVIDER": "mock"})

    with pytest.raises(RuntimeError, match="QWEN_PROVIDER_REQUIRED"):
        get_llm_provider(settings)


def test_qwen_llm_parses_json_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "choices": [
                {"message": {"content": json.dumps({"structured": True})}}
            ]
        })

    provider = QwenLLMProvider(
        Settings.from_env({"LLM_PROVIDER": "qwen", "QWEN_API_KEY": "key"}),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    result = provider.generate_json("research", {"problem": "train cnn"}, {"structured": "boolean"})

    assert result["structured"] is True
    assert result["provider_mode"] == "qwen"
    assert result["fallback_used"] is False


def test_qwen_rejects_raw_duplicate_fix_map_issue_ids():
    raw = (
        '{"objective":"x","fix_map":{'
        '"PRI-1":["procedure"],"PRI-1":["dataset"]}}'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": raw}}]},
        )

    provider = QwenLLMProvider(
        Settings.from_env({"LLM_PROVIDER": "qwen", "QWEN_API_KEY": "key"}),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(ValueError, match="FIX_MAP_DUPLICATE_KEY:PRI-1"):
        provider.generate_json("planning.revise_from_review", {}, {})


def test_task_output_shape_unwraps_a_unique_known_payload_wrapper():
    normalized, changed = normalize_task_output_shape(
        "idea_selection.review",
        {
            "idea_selection_review": {
                "evaluations": [{"candidate_index": 0}],
            },
            "summary": "extra prose",
        },
    )

    assert changed is True
    assert normalized == {"evaluations": [{"candidate_index": 0}]}


def test_qwen_llm_routes_tasks_to_reasoning_general_code_and_fast_models():
    payloads = []

    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    provider = QwenLLMProvider(
        Settings.from_env({
            "LLM_PROVIDER": "qwen",
            "QWEN_API_KEY": "key",
            "QWEN_GENERAL_MODEL": "general-model",
            "QWEN_REASONING_MODEL": "reasoning-model",
            "QWEN_CODE_MODEL": "code-model",
            "QWEN_FAST_MODEL": "fast-model",
        }),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    provider.generate_json("research", {}, {})
    provider.generate_json("critic.review_result", {}, {})
    provider.generate_json("experiment.generate_code", {}, {})
    provider.generate_json("experiment.generate_bundle", {}, {})
    provider.generate_json("experiment.repair_bundle", {}, {})
    provider.generate_json("diagnostic.diagnose_experiment", {}, {})

    assert [payload["model"] for payload in payloads] == [
        "general-model",
        "reasoning-model",
        "code-model",
        "code-model",
        "code-model",
        "code-model",
    ]
    assert payloads[0]["enable_thinking"] is False
    assert payloads[1]["enable_thinking"] is True
    assert "enable_thinking" not in payloads[2]


def test_qwen_llm_falls_back_within_the_same_task_route_and_records_it():
    models = []

    def handler(request: httpx.Request) -> httpx.Response:
        model = json.loads(request.content)["model"]
        models.append(model)
        if model == "reasoning-model":
            return httpx.Response(429, request=request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    provider = QwenLLMProvider(
        Settings.from_env({
            "LLM_PROVIDER": "qwen",
            "QWEN_API_KEY": "key",
            "QWEN_REASONING_MODEL": "reasoning-model",
            "QWEN_GENERAL_MODEL": "general-model",
            "QWEN_RETRIES_PER_MODEL": "0",
        }),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    result = provider.generate_json("critic.review_result", {}, {})

    assert models == ["reasoning-model", "general-model"]
    assert result["model_used"] == "general-model"
    assert result["model_route"] == "reasoning"
    assert result["model_fallback_used"] is True
    assert result["model_fallback_reason"] == "reasoning-model:http_429"
    assert provider.consume_call_metadata() == {
        "task": "critic.review_result",
        "model_used": "general-model",
        "model_route": "reasoning",
        "model_fallback_used": True,
        "model_fallback_reason": "reasoning-model:http_429",
        "thinking_enabled": True,
        "json_repaired": False,
        "shape_normalized": False,
    }
    assert provider.consume_call_metadata() == {}


def test_qwen_llm_repairs_invalid_json_with_fast_model():
    payloads = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        payloads.append(payload)
        content = '{"value": 1}' if payload["model"] == "fast-model" else '{"value": 1'
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    provider = QwenLLMProvider(
        Settings.from_env({
            "LLM_PROVIDER": "qwen",
            "QWEN_API_KEY": "key",
            "QWEN_GENERAL_MODEL": "general-model",
            "QWEN_FAST_MODEL": "fast-model",
        }),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    result = provider.generate_json("writer.build_report", {}, {"value": "integer"})

    assert result["value"] == 1
    assert [payload["model"] for payload in payloads] == ["general-model", "fast-model"]
    assert payloads[1]["enable_thinking"] is False


def test_qwen_llm_sends_max_tokens_and_uses_configured_timeout():
    request_payload = {}
    request_timeout = {}

    def handler(request: httpx.Request) -> httpx.Response:
        request_payload.update(json.loads(request.content))
        request_timeout.update(request.extensions["timeout"])
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    settings = Settings.from_env({
        "LLM_PROVIDER": "qwen",
        "QWEN_API_KEY": "key",
        "QWEN_MAX_TOKENS": "4096",
        "QWEN_TIMEOUT_SECONDS": "240",
    })
    provider = QwenLLMProvider(
        settings,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    provider.generate_json("research", {}, {})

    assert request_payload["max_tokens"] == 4096
    assert request_timeout["read"] == 240.0
    default_client_provider = QwenLLMProvider(settings)
    assert default_client_provider.client.timeout.read == 240.0


def test_qwen_llm_omits_max_tokens_when_configured_as_zero():
    request_payload = {}

    def handler(request: httpx.Request) -> httpx.Response:
        request_payload.update(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    provider = QwenLLMProvider(
        Settings.from_env({
            "LLM_PROVIDER": "qwen",
            "QWEN_API_KEY": "key",
            "QWEN_MAX_TOKENS": "0",
        }),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    provider.generate_json("research", {}, {})

    assert "max_tokens" not in request_payload


def test_qwen_llm_reports_truncated_experiment_output():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": '{"files": ['},
                        "finish_reason": "length",
                    }
                ]
            },
        )

    provider = QwenLLMProvider(
        Settings.from_env({"LLM_PROVIDER": "qwen", "QWEN_API_KEY": "key"}),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(
        ValueError,
        match="EXPERIMENT_CODE_OUTPUT_TRUNCATED: model=qwen3-coder-plus",
    ):
        provider.generate_json("experiment.generate_bundle", {}, {})


def test_qwen_llm_reports_invalid_experiment_json_with_response_summary():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": '{"files": ['},
                        "finish_reason": "stop",
                    }
                ]
            },
        )

    provider = QwenLLMProvider(
        Settings.from_env({"LLM_PROVIDER": "qwen", "QWEN_API_KEY": "key"}),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(ValueError) as captured:
        provider.generate_json("experiment.generate_bundle", {}, {})

    message = str(captured.value)
    assert message.startswith("EXPERIMENT_CODE_GENERATION_INVALID_JSON")
    assert "finish_reason=stop" in message
    assert "characters=11" in message
    assert "raw_tail=" in message


def test_qwen_llm_labels_an_empty_structured_response_as_recoverable_output_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": "   "},
                        "finish_reason": "stop",
                    }
                ]
            },
        )

    provider = QwenLLMProvider(
        Settings.from_env({"LLM_PROVIDER": "qwen", "QWEN_API_KEY": "key"}),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(ValueError, match="MODEL_EMPTY_OUTPUT:provider=qwen"):
        provider.generate_json("research", {}, {})


def test_qwen_llm_wraps_timeouts_in_actionable_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("The read operation timed out", request=request)

    provider = QwenLLMProvider(
        Settings.from_env({"LLM_PROVIDER": "qwen", "QWEN_API_KEY": "key"}),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(RuntimeError, match="MODEL_REQUEST_TIMEOUT:provider=qwen"):
        provider.generate_json("research", {}, {})


def test_qwen_diagnostic_log_records_sanitized_http_error(
    tmp_path, monkeypatch
):
    trace_path = tmp_path / "qwen-trace.jsonl"
    monkeypatch.setenv("QWEN_DIAGNOSTIC_LOG", str(trace_path))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            request=request,
            headers={"x-request-id": "request-123"},
            json={
                "error": {
                    "code": "context_length_exceeded",
                    "message": "bad request for sk-secret-value-123456",
                }
            },
        )

    provider = QwenLLMProvider(
        Settings.from_env(
            {
                "LLM_PROVIDER": "qwen",
                "QWEN_API_KEY": "sk-secret-value-123456",
                "QWEN_GENERAL_MODEL": "general-model",
                "QWEN_REASONING_MODEL": "reasoning-model",
                "QWEN_RETRIES_PER_MODEL": "0",
            }
        ),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    provider.begin_run("run_trace")

    with pytest.raises(RuntimeError, match="MODEL_PROVIDER_CONFIG_ERROR:provider=qwen"):
        provider.generate_json(
            "critic.review_result",
            {"evidence": [{"claim": "x"}]},
            {"verdict": "string"},
            instructions="diagnostic instructions",
        )

    events = [
        json.loads(line)
        for line in trace_path.read_text(encoding="utf-8").splitlines()
    ]
    finished = [event for event in events if event["event"] == "attempt_finished"]

    assert len(finished) == 1
    assert finished[0]["run_id"] == "run_trace"
    assert finished[0]["http_status"] == 400
    assert finished[0]["request_id"] == "request-123"
    assert finished[0]["request_characters"] > 0
    assert "context_length_exceeded" in finished[0]["response_excerpt"]
    assert "sk-secret-value-123456" not in trace_path.read_text(encoding="utf-8")


def test_qwen_llm_includes_skill_instructions_as_a_system_message():
    request_payload = {}

    def handler(request: httpx.Request) -> httpx.Response:
        request_payload.update(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    provider = QwenLLMProvider(
        Settings.from_env({"LLM_PROVIDER": "qwen", "QWEN_API_KEY": "key"}),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    provider.generate_json("research", {}, {}, instructions="Follow the loaded skill.")

    assert request_payload["messages"][1] == {
        "role": "system",
        "content": "Follow the loaded skill.",
    }


def test_qwen_llm_honors_run_cancellation_before_retrying():
    provider = QwenLLMProvider(
        Settings.from_env({"LLM_PROVIDER": "qwen", "QWEN_API_KEY": "key"}),
        client=httpx.Client(transport=httpx.MockTransport(
            lambda _: pytest.fail("cancelled request must not reach the transport")
        )),
    )
    provider.begin_run("run_cancelled")
    assert provider.cancel_run("run_cancelled") is True

    with pytest.raises(LLMRequestCancelled, match="PIPELINE_STOPPED"):
        provider.generate_json("research", {}, {})

    provider.end_run("run_cancelled")
