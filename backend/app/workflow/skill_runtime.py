from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import hashlib
import re
from typing import Any

from backend.app.workflow.skills import SkillContext, SkillLoader, SkillRegistry


AGENT_TOOL_POLICY: dict[str, frozenset[str]] = {
    "supervisor": frozenset(
        {
            "read_run",
            "read_artifact",
            "load_skill",
            "dispatch_agent",
            "validate_artifact",
            "request_revision",
            "update_step",
            "append_event",
            "commit_wiki_changes",
        }
    ),
    "research": frozenset(
        {
            "read_run",
            "read_artifact",
            "query_wiki",
            "search_local_literature",
            "literature_search",
            "propose_wiki_changes",
        }
    ),
    "idea": frozenset({"read_run", "read_artifact", "read_wiki_query_pack"}),
    "critic": frozenset(
        {
            "read_run",
            "read_artifact",
            "query_wiki",
            "search_local_literature",
            "literature_search",
            "audit_evidence",
            "audit_result",
        }
    ),
    "planning": frozenset({"read_run", "read_artifact"}),
    "experiment": frozenset(
        {
            "read_run",
            "read_artifact",
            "build_experiment_bundle",
            "local_process_run",
            "ssh_run",
            "read_experiment_result",
            "audit_result",
        }
    ),
    "diagnostic": frozenset(
        {
            "read_run",
            "read_artifact",
            "read_experiment_result",
            "audit_result",
            "repair_dataset_cache",
            "retry_experiment",
            "build_experiment_bundle",
        }
    ),
    "writer": frozenset({"read_run", "read_artifact", "render_report"}),
}


class ToolRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, Callable[..., Any]] = {}

    def register(self, name: str, handler: Callable[..., Any]) -> None:
        if not name or not callable(handler):
            raise ValueError("SKILL_TOOL_INVALID")
        self._handlers[name] = handler

    def names(self) -> set[str]:
        return set(self._handlers)

    def handler(self, name: str) -> Callable[..., Any]:
        try:
            return self._handlers[name]
        except KeyError as exc:
            raise ValueError(f"SKILL_TOOL_NOT_REGISTERED:{name}") from exc


@dataclass(frozen=True)
class InstructionBundle:
    text: str
    omitted_sections: tuple[str, ...]


class InstructionBudget:
    def __init__(self, max_characters: int = 32_000) -> None:
        if max_characters <= 0:
            raise ValueError("SKILL_INSTRUCTION_BUDGET_INVALID")
        self.max_characters = max_characters

    def render(self, contexts: list[SkillContext]) -> InstructionBundle:
        parts: list[str] = []
        omitted: list[str] = []
        used = 0

        for context in contexts:
            header = f"## Skill: {context.id}\n{context.description}".strip()
            sections = _markdown_sections(context.instructions)
            candidate_header = _append_text(parts, header)
            if used + len(candidate_header) > self.max_characters:
                omitted.extend(f"{context.id}#{title}" for title, _ in sections)
                continue
            parts.append(header)
            used += len(candidate_header)

            for index, (title, section) in enumerate(sections):
                addition = _append_text(parts, section)
                if used + len(addition) > self.max_characters:
                    omitted.extend(
                        f"{context.id}#{remaining_title}"
                        for remaining_title, _ in sections[index:]
                    )
                    break
                parts.append(section)
                used += len(addition)

        return InstructionBundle(text="\n\n".join(parts), omitted_sections=tuple(omitted))


@dataclass(frozen=True)
class RuntimePackage:
    step_id: str
    agent_id: str
    skill_ids: tuple[str, ...]
    instructions: str
    authorized_tools: tuple[str, ...]
    omitted_sections: tuple[str, ...]
    audit: dict[str, Any]


class SkillRuntime:
    def __init__(
        self,
        loader: SkillLoader,
        registry: SkillRegistry,
        tools: ToolRegistry,
        instruction_budget: InstructionBudget | None = None,
        agent_tools: Mapping[str, set[str] | frozenset[str]] | None = None,
    ) -> None:
        self.loader = loader
        self.registry = registry
        self.tools = tools
        self.instruction_budget = instruction_budget or InstructionBudget()
        self.agent_tools = dict(agent_tools or AGENT_TOOL_POLICY)

    def authorize(
        self,
        agent_id: str,
        declared_tools: set[str],
        configured_tools: set[str],
    ) -> tuple[str, ...]:
        authorized = (
            declared_tools
            & set(self.agent_tools.get(agent_id, ()))
            & self.tools.names()
            & configured_tools
        )
        return tuple(sorted(authorized))

    def prepare(
        self,
        step_id: str,
        agent_id: str,
        configured_tools: set[str],
        state: Mapping[str, Any] | None = None,
    ) -> RuntimePackage:
        assignment = self.registry.assignment_for(step_id)
        if assignment.agent_id != agent_id:
            raise ValueError(f"SUPERVISOR_AGENT_MISMATCH:{step_id}")

        conditional_skill_ids = self.registry.conditional_skills_for(
            step_id, state or {}
        )
        skill_ids = assignment.skill_ids + conditional_skill_ids
        contexts = [self.loader.load_complete(skill_id) for skill_id in skill_ids]
        declared = {tool for context in contexts for tool in context.allowed_tools}
        authorized = self.authorize(agent_id, declared, configured_tools)
        rendered = self.instruction_budget.render(contexts)
        if rendered.omitted_sections:
            raise ValueError(
                "SKILL_INSTRUCTION_BUDGET_EXCEEDED:"
                + ",".join(rendered.omitted_sections)
            )
        denied = sorted(declared - set(authorized))
        instruction_hash = hashlib.sha256(rendered.text.encode("utf-8")).hexdigest()
        invocations = [
            {
                "skill_id": context.id,
                "name": context.name or context.id,
                "description": context.description,
                "trigger": (
                    "conditional" if context.id in conditional_skill_ids else "required"
                ),
                "load_mode": "complete",
                "instruction_sha256": context.instruction_sha256,
                "declared_tools": list(context.allowed_tools),
                "authorized_tools": list(self.authorize(
                    agent_id,
                    set(context.allowed_tools),
                    configured_tools,
                )),
            }
            for context in contexts
        ]
        audit = {
            "skill_hashes": {
                context.id: context.instruction_sha256 for context in contexts
            },
            "instruction_sha256": instruction_hash,
            "declared_tools": sorted(declared),
            "authorized_tools": list(authorized),
            "denied_tools": denied,
            "omitted_sections": list(rendered.omitted_sections),
            "skill_invocations": invocations,
        }
        return RuntimePackage(
            step_id=step_id,
            agent_id=agent_id,
            skill_ids=skill_ids,
            instructions=rendered.text,
            authorized_tools=authorized,
            omitted_sections=rendered.omitted_sections,
            audit=audit,
        )

    def instructions_for(
        self,
        package: RuntimePackage,
        *skill_ids: str,
    ) -> str:
        """Render complete instructions for an atomic Skill invocation.

        A workflow step may own several Skills, but each domain operation should
        receive only the protocol it is currently executing.  This mirrors the
        way Codex loads a selected Skill before acting while preserving the
        aggregate authorization and audit package for the parent step.
        """
        requested = tuple(dict.fromkeys(skill_ids))
        unavailable = [skill_id for skill_id in requested if skill_id not in package.skill_ids]
        if unavailable:
            raise ValueError(
                f"SKILL_NOT_ROUTED:{package.step_id}:{','.join(unavailable)}"
            )
        contexts = [self.loader.load_complete(skill_id) for skill_id in requested]
        return self.instruction_budget.render(contexts).text


def _append_text(parts: list[str], value: str) -> str:
    return ("\n\n" if parts else "") + value


def _markdown_sections(body: str) -> list[tuple[str, str]]:
    matches = list(re.finditer(r"(?m)^(#{1,6})\s+(.+?)\s*$", body))
    if not matches:
        return [("body", body.strip())] if body.strip() else []

    sections: list[tuple[str, str]] = []
    prefix = body[: matches[0].start()].strip()
    if prefix:
        sections.append(("preamble", prefix))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        title = match.group(2).strip()
        sections.append((title, body[match.start() : end].strip()))
    return sections
