from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any


_TRACE_LOCK = Lock()
_SECRET_PATTERN = re.compile(r"\bsk-[A-Za-z0-9._-]{12,}\b")


def request_metrics(
    task: str,
    inputs: dict,
    schema_hint: dict,
    instructions: str,
) -> dict[str, Any]:
    canonical = json.dumps(
        {
            "task": task,
            "inputs": inputs,
            "schema_hint": schema_hint,
            "instructions": instructions,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    audit = inputs.get("evidence_audit")
    registry = audit.get("registry") if isinstance(audit, dict) else None
    evidence = (
        inputs.get("evidence")
        or inputs.get("evidence_units")
        or inputs.get("verified_evidence")
    )
    candidates = inputs.get("candidates")
    input_components = {
        "problem_context_chars": _json_chars(inputs.get("problem")),
        "hypothesis_chars": _json_chars(inputs.get("candidates") or inputs.get("hypothesis")),
        "selected_reference_chars": _json_chars(evidence),
        "previous_artifact_chars": _json_chars(inputs.get("previous_artifacts")),
        "evidence_audit_chars": _json_chars(inputs.get("evidence_audit")),
        "evaluation_chars": _json_chars(inputs.get("evaluation")),
    }
    known = len(instructions) + len(json.dumps(schema_hint, ensure_ascii=False, separators=(",", ":")))
    known += sum(input_components.values())
    return {
        "request_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "request_characters": len(canonical),
        "inputs_characters": len(
            json.dumps(inputs, ensure_ascii=False, separators=(",", ":"))
        ),
        "instructions_characters": len(instructions),
        "schema_characters": len(
            json.dumps(schema_hint, ensure_ascii=False, separators=(",", ":"))
        ),
        "evidence_count": len(evidence) if isinstance(evidence, list) else None,
        "candidate_count": len(candidates) if isinstance(candidates, list) else None,
        "registry_count": len(registry) if isinstance(registry, list) else None,
        "component_chars": {
            "system_prompt_chars": 0,
            "agent_prompt_chars": 0,
            "skill_instruction_chars": len(instructions),
            **input_components,
            "review_rubric_chars": 0,
            "structured_schema_chars": len(json.dumps(schema_hint, ensure_ascii=False, separators=(",", ":"))),
            "other_chars": max(0, len(canonical) - known),
            "total_chars": len(canonical),
            "estimated_tokens": (len(canonical) + 3) // 4,
        },
    }


def _json_chars(value: Any) -> int:
    if value is None:
        return 0
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def sanitized_response_excerpt(text: str, api_key: str, limit: int = 4000) -> str:
    value = text[:limit]
    if api_key:
        value = value.replace(api_key, "[REDACTED_API_KEY]")
    return _SECRET_PATTERN.sub("[REDACTED_API_KEY]", value)


def append_trace(path: str, event: dict[str, Any]) -> None:
    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **event,
    }
    line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    with _TRACE_LOCK:
        with target.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(f"{line}\n")
