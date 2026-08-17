from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from backend.app.providers.llm import LLMProvider


@dataclass(frozen=True)
class ValidationDecision:
    accepted: bool
    issues: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReviewRubric:
    version: str
    criteria: tuple[str, ...]


class ReviewerAgent:
    name = "Reviewer Agent"

    def __init__(self, llm: LLMProvider) -> None:
        self.llm = llm

    def review(
        self,
        step_id: str,
        artifact_path: Path,
        wiki_paths: tuple[Path, ...],
        rubric: ReviewRubric,
        review_context: dict | None = None,
    ) -> ValidationDecision:
        artifact = _read_artifact(artifact_path)
        wiki_files = [
            {"path": str(path), "content": path.read_text(encoding="utf-8")}
            for path in wiki_paths
        ]
        result = self.llm.generate_json(
            "reviewer.semantic",
            {
                "step_id": step_id,
                "artifact": artifact,
                "review_context": review_context or {},
                "wiki_files": wiki_files,
                "rubric": {
                    "version": rubric.version,
                    "criteria": list(rubric.criteria),
                },
            },
            {"accepted": "boolean", "issues": ["string"]},
            instructions=(
                "Review only the raw candidate artifact and explicitly supplied Wiki files. "
                "The review_context contains read-only source artifacts supplied for traceability; "
                "do not treat it as a Supervisor conclusion. Return an independent structured "
                "decision without assuming or inventing missing evidence. "
                "The issues array must contain only concrete blocking defects against the rubric; "
                "never put strengths, confirmations, or praise in issues. Set accepted=true and "
                "issues=[] when all rubric criteria are met. "
                + (
                    "For feedback_revision, supported, partial, and failed are all valid outcomes. "
                    "Accept an honest partial or failed verdict when it traces measured metrics, "
                    "restrains claims, identifies unsupported claims, and gives a concrete next "
                    "action. Do not reject it merely because the proposed future experiments or "
                    "ablations have not been run yet; those are executed only after feedback is "
                    "accepted."
                    if step_id == "feedback_revision"
                    else (
                        "For evidence_reasoning, a rejected, revised, or evidence-insufficient "
                        "scientific hypothesis is a valid outcome. Accept it when every candidate "
                        "is traceable to evidence and the negative conclusion is honest. Candidate "
                        "selection belongs to the user after this review, so do not require an "
                        "active_hypothesis or reject merely because no candidate received GO."
                        if step_id == "evidence_reasoning"
                        else ""
                    )
                )
            ),
        )
        accepted = result.get("accepted")
        issues = result.get("issues")
        if not isinstance(accepted, bool) or not isinstance(issues, list):
            return ValidationDecision(False, ("REVIEWER_OUTPUT_INVALID",))
        normalized = tuple(str(issue).strip() for issue in issues if str(issue).strip())
        if accepted and normalized:
            return ValidationDecision(False, normalized)
        if not accepted and not normalized:
            return ValidationDecision(False, ("semantic review rejected without issues",))
        return ValidationDecision(accepted, normalized)


def _read_artifact(path: Path):
    raw = path.read_text(encoding="utf-8")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw
