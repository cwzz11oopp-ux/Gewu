import hashlib
from pathlib import Path

import pytest

from backend.app.workflow.skills import SkillCatalog, SkillLoader, SkillRegistry


RUNTIME_SKILL_TOOLS = {
    "ai-scientist-supervisor": {
        "read_run",
        "read_artifact",
        "load_skill",
        "dispatch_agent",
        "validate_artifact",
        "request_revision",
        "update_step",
        "append_event",
        "commit_wiki_changes",
    },
    "problem-framing": {"read_run", "read_artifact"},
    "research-lit": {
        "read_run",
        "read_artifact",
        "search_local_literature",
        "literature_search",
    },
    "research-wiki": {
        "read_run",
        "read_artifact",
        "query_wiki",
        "propose_wiki_changes",
    },
    "idea-creator": {"read_run", "read_artifact", "read_wiki_query_pack"},
    "novelty-check": {"read_run", "read_artifact", "literature_search", "audit_evidence"},
    "research-review": {"read_run", "read_artifact", "audit_evidence"},
    "research-refine": {"read_run", "read_artifact"},
    "experiment-plan": {"read_run", "read_artifact"},
    "experiment-implementation": {"read_run", "read_artifact", "build_experiment_bundle"},
    "run-experiment": {
        "read_run",
        "read_artifact",
        "local_process_run",
        "ssh_run",
        "read_experiment_result",
    },
    "analyze-results": {"read_run", "read_artifact", "read_experiment_result"},
    "experiment-audit": {
        "read_run",
        "read_artifact",
        "read_experiment_result",
        "audit_result",
    },
    "experiment-diagnosis": {
        "read_run",
        "read_artifact",
        "read_experiment_result",
        "audit_result",
        "repair_dataset_cache",
        "retry_experiment",
        "build_experiment_bundle",
    },
    "monitor-experiment": {
        "read_run",
        "read_artifact",
        "local_process_run",
        "ssh_run",
        "read_experiment_result",
    },
    "result-to-claim": {"read_run", "read_artifact", "audit_result"},
    "experiment-iteration": {
        "read_run",
        "read_artifact",
        "audit_result",
        "query_wiki",
        "search_local_literature",
        "literature_search",
    },
    "ablation-planner": {"read_run", "read_artifact"},
    "competition-report": {"read_run", "read_artifact", "render_report"},
    "report-quality-audit": set(),
}

FORBIDDEN_RUNTIME_PROMPT_MARKERS = (
    "mcp__codex",
    "codex-reply",
    "CLAUDE.md",
    "~/.claude",
    "Executor — Claude",
    "Vast.ai",
    "serverless-modal",
)

FORBIDDEN_RUNTIME_TOOLS = {"Bash(*)", "WebSearch", "WebFetch", "Agent"}


def _write_skill(root: Path, skill_id: str, body: str, frontmatter: str = "") -> None:
    target = root / "skills" / skill_id
    target.mkdir(parents=True)
    extra = f"{frontmatter.rstrip()}\n" if frontmatter else ""
    (target / "SKILL.md").write_text(
        "---\nname: demo\ndescription: test skill\n" + extra + "---\n" + body,
        encoding="utf-8",
    )


def test_loader_reads_frontmatter_and_bounds_instruction_text(tmp_path):
    _write_skill(tmp_path, "demo", "x" * 20)

    context = SkillLoader(tmp_path, per_skill_limit=10).load("demo")

    assert context.id == "demo"
    assert context.name == "demo"
    assert context.description == "test skill"
    assert context.instructions == "x" * 10
    assert context.truncated is True


def test_loader_parses_allowed_tools_and_hashes_complete_body(tmp_path):
    _write_skill(
        tmp_path,
        "demo",
        "Return a result.",
        frontmatter="allowed-tools: read_run, literature_search",
    )

    context = SkillLoader(tmp_path).load("demo")

    assert context.allowed_tools == ("read_run", "literature_search")
    assert context.instruction_sha256 == hashlib.sha256(
        b"Return a result."
    ).hexdigest()


def test_instruction_hash_is_stable_and_changes_with_body(tmp_path):
    _write_skill(tmp_path, "first", "same")
    _write_skill(tmp_path, "second", "same")
    _write_skill(tmp_path, "edited", "changed")

    loader = SkillLoader(tmp_path)

    assert loader.load("first").instruction_sha256 == loader.load("second").instruction_sha256
    assert loader.load("first").instruction_sha256 != loader.load("edited").instruction_sha256


@pytest.mark.parametrize("skill_id", ["../secret", "C:/secret", "/secret"])
def test_loader_rejects_paths_outside_the_skills_directory(tmp_path, skill_id):
    with pytest.raises(ValueError, match="SKILL_NOT_FOUND"):
        SkillLoader(tmp_path).load(skill_id)


def test_loader_reports_missing_skill_and_registry_has_plan_route(tmp_path):
    with pytest.raises(ValueError, match="SKILL_NOT_FOUND:experiment-plan"):
        SkillLoader(tmp_path).load("experiment-plan")

    assert SkillRegistry().skills_for("research_plan") == (
        "research-refine",
        "hypothesis-experiment-gate",
        "experiment-plan",
    )


def test_feedback_route_combines_critic_and_plan_refinement_skills():
    registry = SkillRegistry()

    assert registry.skills_for("feedback_revision") == (
        "experiment-iteration",
        "result-to-claim",
    )
    assert registry.conditional_skills_for(
        "feedback_revision",
        {"plan_refinement_enabled": True, "experiment_verdict": "partial"},
    ) == ("research-refine", "experiment-plan", "ablation-planner")


def test_architecture_docs_describe_runtime_skill_routing():
    text = Path("docs/agent_architecture.md").read_text(encoding="utf-8")

    assert "SkillRegistry" in text
    assert "SkillLoader" in text
    assert "SupervisorAgent" in text
    assert "SkillRuntime" in text
    assert "authorized_tools" in text
    assert "problem_understanding" in text
    assert "competition-report" in text
    assert "backend/app/agents" in text
    assert "top-level `.agents`" in text


def test_catalog_discovers_only_direct_qwen_skill_directories(tmp_path):
    _write_skill(tmp_path, "experiment-plan", "experiment roadmap")
    _write_skill(tmp_path, "skills-codex/experiment-plan", "wrong variant")
    _write_skill(tmp_path, "skills-codex-claude-review/research-review", "wrong variant")
    _write_skill(tmp_path, "skills-codex-gemini-review/research-review", "wrong variant")
    _write_skill(tmp_path, "shared-references", "not a skill")

    catalog = SkillCatalog(SkillLoader(tmp_path))

    assert [skill.id for skill in catalog.skills()] == ["experiment-plan"]
    assert catalog.skills()[0].tokens == ("experiment", "plan", "demo", "test", "skill")


def test_registry_uses_only_the_static_skills_for_each_step():
    registry = SkillRegistry()

    experiment_task = registry.select("experiment_task", "CNN training needs GPU execution")
    report = registry.select("report_export", "GPU experiment with an ablation")

    assert experiment_task.mandatory_skills == ("experiment-implementation",)
    assert experiment_task.selected_skills == ("experiment-implementation",)
    assert experiment_task.candidate_scores == ()
    assert report.selected_skills == ("competition-report", "report-quality-audit")


def test_evidence_reasoning_owns_idea_review_and_critic_skills():
    registry = SkillRegistry()

    assert registry.skills_for("evidence_reasoning") == (
        "evidence-recovery",
        "idea-selection",
        "novelty-check",
        "research-review",
    )
    with pytest.raises(ValueError, match="UNKNOWN_WORKFLOW_STEP:idea_selection"):
        registry.assignment_for("idea_selection")


def test_registry_selection_does_not_change_with_run_context():
    registry = SkillRegistry()

    first = registry.select("experiment_run_analysis", "local CNN training")
    second = registry.select("experiment_run_analysis", "Vast GPU transformer experiment")

    assert first == second
    assert first.selected_skills == ("run-experiment", "analyze-results", "experiment-audit")


def test_architecture_docs_exclude_cross_model_skill_variants():
    text = Path("docs/agent_architecture.md").read_text(encoding="utf-8")

    assert "skills-codex-claude-review" in text
    assert "skills-codex-gemini-review" in text
    assert "一级" in text


@pytest.mark.parametrize("skill_id", RUNTIME_SKILL_TOOLS)
def test_required_runtime_skill_is_loadable_with_exact_application_tools(skill_id):
    root = Path(__file__).resolve().parents[2]

    context = SkillLoader(root).load(skill_id)

    assert context.id == skill_id
    assert context.instructions
    assert set(context.allowed_tools) == RUNTIME_SKILL_TOOLS[skill_id]


@pytest.mark.parametrize("skill_id", RUNTIME_SKILL_TOOLS)
def test_routed_skill_prompt_is_qwen_and_application_runtime_compatible(skill_id):
    root = Path(__file__).resolve().parents[2]
    context = SkillLoader(root).load(skill_id)
    prompt = context.instructions

    assert not any(marker in prompt for marker in FORBIDDEN_RUNTIME_PROMPT_MARKERS)
    assert not (set(context.allowed_tools) & FORBIDDEN_RUNTIME_TOOLS)


def test_runtime_skills_are_versioned_but_nested_metadata_is_ignored():
    rules = {
        line.strip()
        for line in Path(".gitignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert "skills/" not in rules
    assert "skills/.git/" in rules
    assert "skills/.agents/" in rules
    assert "backend/data/" in rules
