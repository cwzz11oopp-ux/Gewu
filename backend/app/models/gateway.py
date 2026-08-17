from __future__ import annotations

from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

from backend.app.providers.llm import LLMProvider


StructuredOutput = TypeVar("StructuredOutput", bound=BaseModel)


class ModelGateway(Protocol):
    def invoke_structured(
        self,
        task_type: str,
        messages: list[dict[str, str]],
        output_schema: type[StructuredOutput],
        context: dict[str, Any] | None = None,
    ) -> StructuredOutput: ...


class LegacyQwenAdapter:
    """Provider-neutral V2 gateway backed by the existing Qwen-compatible interface."""

    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider

    def invoke_structured(
        self,
        task_type: str,
        messages: list[dict[str, str]],
        output_schema: type[StructuredOutput],
        context: dict[str, Any] | None = None,
    ) -> StructuredOutput:
        raw = self.provider.generate_json(
            task_type,
            {"messages": messages, "context": context or {}},
            output_schema.model_json_schema(),
            instructions=(
                "Return only data matching the supplied schema. Preserve uncertainty; "
                "do not invent literature, experiments, metrics, or repository facts."
            ),
        )
        # The legacy provider appends transport/routing metadata to its returned
        # dictionary. V2 scientific schemas deliberately forbid unknown fields,
        # so validate only the model payload and keep metadata available through
        # provider.consume_call_metadata().
        infrastructure_fields = {
            "provider_mode",
            "fallback_used",
            "model_used",
            "model_route",
            "model_fallback_used",
            "model_fallback_reason",
            "thinking_enabled",
            "json_repaired",
            "shape_normalized",
        }
        payload = {
            key: value for key, value in raw.items() if key not in infrastructure_fields
        }
        return output_schema.model_validate(payload)
