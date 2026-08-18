from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping


@dataclass(frozen=True)
class StepAssignment:
    agent_id: str
    primary_skills: tuple[str, ...]
    capability_skills: tuple[str, ...] = ()

    @property
    def skill_ids(self) -> tuple[str, ...]:
        return self.primary_skills + self.capability_skills


@dataclass(frozen=True)
class ConditionalSkill:
    skill_id: str
    field: str
    values: tuple[Any, ...]

    def matches(self, state: Mapping[str, Any]) -> bool:
        return state.get(self.field) in self.values


_ASSIGNMENTS = {
    "problem_understanding": StepAssignment("research", ("problem-framing",)),
    "knowledge_integration": StepAssignment(
        "research", ("research-lit",), ("research-wiki",)
    ),
    "hypothesis_generation": StepAssignment(
        "idea", ("idea-creator", "hypothesis-evidence")
    ),
    "evidence_reasoning": StepAssignment(
        "critic", ("evidence-recovery", "idea-selection", "novelty-check"), ("research-review",)
    ),
    "research_plan": StepAssignment(
        "planning",
        ("research-refine",),
        (
            "hypothesis-experiment-gate",
            "experiment-plan",
            "plan-review-governance",
        ),
    ),
    "experiment_task": StepAssignment("experiment", ("experiment-implementation",)),
    "experiment_run_analysis": (
        StepAssignment(
            "experiment", ("run-experiment",), ("analyze-results", "experiment-audit")
        )
    ),
    "experiment_diagnosis": StepAssignment(
        "diagnostic", ("experiment-diagnosis",)
    ),
    "feedback_revision": StepAssignment(
        "critic", ("experiment-iteration", "result-to-claim")
    ),
    "report_export": StepAssignment(
        "writer",
        ("competition-report", "report-quality-audit"),
    ),
}

# Optional capabilities are intentionally outside the automatic research
# pipeline. They can be invoked only after the user explicitly chooses them.
_OPTIONAL_ASSIGNMENTS = {
    "paper_writing": StepAssignment(
        "writer",
        (
            "paper-writing",
            "paper-plan",
            "paper-write",
        ),
    ),
}

_CONDITIONAL_SKILLS = {
    "feedback_revision": (
        ConditionalSkill(
            "research-refine",
            field="plan_refinement_enabled",
            values=(True,),
        ),
        ConditionalSkill(
            "experiment-plan",
            field="plan_refinement_enabled",
            values=(True,),
        ),
        ConditionalSkill(
            "ablation-planner",
            field="experiment_verdict",
            values=("failed", "partial"),
        ),
    ),
    "experiment_run_analysis": (
        ConditionalSkill(
            "monitor-experiment",
            field="monitoring_enabled",
            values=(True,),
        ),
    ),
}

EXCLUDED_CATALOG_DIRECTORIES = (
    "shared-references",
    "skills-codex",
    "skills-codex-claude-review",
    "skills-codex-gemini-review",
)


@dataclass(frozen=True)
class SkillContext:
    id: str
    name: str
    description: str
    instructions: str
    allowed_tools: tuple[str, ...]
    instruction_sha256: str
    truncated: bool


@dataclass(frozen=True)
class SkillPolicy:
    skill_id: str
    content: dict[str, Any]
    sha256: str


@dataclass(frozen=True)
class ParsedSkill:
    name: str
    description: str
    allowed_tools: tuple[str, ...]
    body: str


@dataclass(frozen=True)
class CatalogSkill:
    id: str
    name: str
    description: str
    tokens: tuple[str, ...]


@dataclass(frozen=True)
class RouteDecision:
    mandatory_skills: tuple[str, ...]
    selected_skills: tuple[str, ...]
    candidate_scores: tuple[dict, ...]


class SkillRegistry:
    def assignment_for(self, step_id: str) -> StepAssignment:
        assignment = _ASSIGNMENTS.get(step_id)
        if assignment is None:
            raise ValueError(f"UNKNOWN_WORKFLOW_STEP:{step_id}")
        return assignment

    def skills_for(self, step_id: str) -> tuple[str, ...]:
        assignment = _ASSIGNMENTS.get(step_id)
        return assignment.skill_ids if assignment else ()

    def optional_assignment_for(self, capability_id: str) -> StepAssignment:
        assignment = _OPTIONAL_ASSIGNMENTS.get(capability_id)
        if assignment is None:
            raise ValueError(f"UNKNOWN_OPTIONAL_CAPABILITY:{capability_id}")
        return assignment

    def conditional_skills_for(
        self,
        step_id: str,
        state: Mapping[str, Any],
    ) -> tuple[str, ...]:
        return tuple(
            rule.skill_id
            for rule in _CONDITIONAL_SKILLS.get(step_id, ())
            if rule.matches(state)
        )

    def select(
        self,
        step_id: str,
        context: str = "",
        catalog: "SkillCatalog | None" = None,
    ) -> RouteDecision:
        mandatory = self.skills_for(step_id)
        return RouteDecision(
            mandatory_skills=mandatory,
            selected_skills=mandatory,
            candidate_scores=(),
        )


class SkillCatalog:
    def __init__(self, loader: "SkillLoader") -> None:
        self.loader = loader

    def skills(self) -> tuple[CatalogSkill, ...]:
        if not self.loader.skills_root.is_dir():
            return ()
        return tuple(
            self.loader.catalog_skill(path.name)
            for path in sorted(self.loader.skills_root.iterdir(), key=lambda item: item.name)
            if path.is_dir()
            and path.name not in EXCLUDED_CATALOG_DIRECTORIES
            and (path / "SKILL.md").is_file()
        )


class SkillLoader:
    def __init__(
        self,
        repository_root: Path,
        per_skill_limit: int = 12_000,
        total_limit: int = 32_000,
    ) -> None:
        self.skills_root = (repository_root.resolve() / "skills").resolve()
        self.per_skill_limit = per_skill_limit
        self.total_limit = total_limit

    def load(self, skill_id: str) -> SkillContext:
        return self._load_context(skill_id, self.per_skill_limit)

    def load_complete(self, skill_id: str) -> SkillContext:
        return self._load_context(skill_id, None)

    def _load_context(self, skill_id: str, limit: int | None) -> SkillContext:
        target = self._path_for(skill_id)
        if not target.is_file():
            raise ValueError(f"SKILL_NOT_FOUND:{skill_id}")
        raw = target.read_text(encoding="utf-8").strip()
        if not raw:
            raise ValueError(f"SKILL_NOT_FOUND:{skill_id}")
        parsed = _parse_skill(raw)
        if not parsed.body:
            raise ValueError(f"SKILL_NOT_FOUND:{skill_id}")
        return SkillContext(
            id=skill_id,
            name=parsed.name,
            description=parsed.description,
            instructions=parsed.body if limit is None else parsed.body[:limit],
            allowed_tools=parsed.allowed_tools,
            instruction_sha256=hashlib.sha256(parsed.body.encode("utf-8")).hexdigest(),
            truncated=False if limit is None else len(parsed.body) > limit,
        )

    def load_many(self, skill_ids: list[str]) -> list[SkillContext]:
        remaining = self.total_limit
        contexts = []
        for skill_id in skill_ids:
            context = self.load(skill_id)
            instructions = context.instructions[: max(remaining, 0)]
            contexts.append(
                SkillContext(
                    id=context.id,
                    name=context.name,
                    description=context.description,
                    instructions=instructions,
                    allowed_tools=context.allowed_tools,
                    instruction_sha256=context.instruction_sha256,
                    truncated=context.truncated or len(instructions) < len(context.instructions),
                )
            )
            remaining -= len(instructions)
        return contexts

    def catalog_skill(self, skill_id: str) -> CatalogSkill:
        context = self.load(skill_id)
        return CatalogSkill(
            id=context.id,
            name=context.name,
            description=context.description,
            tokens=_tokens(f"{context.id} {context.name} {context.description}"),
        )

    def load_policy(self, skill_id: str) -> SkillPolicy:
        """Load a Skill-local structured policy without changing instruction loading."""
        skill_dir = self._skill_dir_for(skill_id)
        target = (skill_dir / "policy.json").resolve()
        if target.parent != skill_dir or target.name != "policy.json":
            raise ValueError(f"SKILL_POLICY_NOT_FOUND:{skill_id}")
        if not target.is_file():
            raise ValueError(f"SKILL_POLICY_NOT_FOUND:{skill_id}")
        raw = target.read_bytes()
        try:
            content = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"SKILL_POLICY_INVALID:{skill_id}") from exc
        if not isinstance(content, dict):
            raise ValueError(f"SKILL_POLICY_INVALID:{skill_id}")
        return SkillPolicy(
            skill_id=skill_id,
            content=content,
            sha256=hashlib.sha256(raw).hexdigest(),
        )

    def _path_for(self, skill_id: str) -> Path:
        return self._skill_dir_for(skill_id) / "SKILL.md"

    def _skill_dir_for(self, skill_id: str) -> Path:
        requested = Path(skill_id)
        if requested.is_absolute() or ".." in requested.parts:
            raise ValueError(f"SKILL_NOT_FOUND:{skill_id}")
        target = (self.skills_root / requested).resolve()
        if target.parent != self.skills_root:
            raise ValueError(f"SKILL_NOT_FOUND:{skill_id}")
        return target


def _parse_skill(raw: str) -> ParsedSkill:
    if not raw.startswith("---\n"):
        return ParsedSkill("", "", (), raw)
    closing = raw.find("\n---\n", len("---\n"))
    if closing < 0:
        return ParsedSkill("", "", (), raw)
    frontmatter = raw[len("---\n") : closing]
    body = raw[closing + len("\n---\n") :].strip()
    fields = {}
    for line in frontmatter.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        fields[key.strip()] = value.strip().strip("'\"")
    return ParsedSkill(
        name=fields.get("name", ""),
        description=fields.get("description", ""),
        allowed_tools=_tool_names(fields.get("allowed-tools", "")),
        body=body,
    )


def _tool_names(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _tokens(value: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(token.lower() for token in re.findall(r"[A-Za-z0-9]+", value)))
