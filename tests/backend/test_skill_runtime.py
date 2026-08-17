from pathlib import Path

from backend.app.workflow.skill_runtime import InstructionBudget, SkillRuntime, ToolRegistry
from backend.app.workflow.skills import SkillLoader, SkillRegistry


def _write_skill(root: Path, skill_id: str, tools: str, body: str) -> None:
    target = root / "skills" / skill_id
    target.mkdir(parents=True, exist_ok=True)
    (target / "SKILL.md").write_text(
        "---\n"
        f"name: {skill_id}\n"
        "description: runtime test\n"
        f"allowed-tools: {tools}\n"
        "---\n\n"
        f"{body}\n",
        encoding="utf-8",
    )


def _registry_with(*names: str) -> ToolRegistry:
    registry = ToolRegistry()
    for name in names:
        registry.register(name, lambda: None)
    return registry


def test_runtime_authorizes_only_four_way_tool_intersection(tmp_path):
    _write_skill(
        tmp_path,
        "research-lit",
        "read_run, literature_search, ssh_run",
        "# Search\nUse approved sources.",
    )
    _write_skill(
        tmp_path,
        "research-wiki",
        "query_wiki",
        "# Wiki\nQuery before external search.",
    )
    tools = _registry_with("read_run", "literature_search", "ssh_run")
    runtime = SkillRuntime(SkillLoader(tmp_path), SkillRegistry(), tools)

    package = runtime.prepare(
        "knowledge_integration",
        "research",
        configured_tools={"read_run", "literature_search", "ssh_run"},
    )

    assert package.authorized_tools == ("literature_search", "read_run")
    assert "ssh_run" not in package.authorized_tools
    assert package.audit["denied_tools"] == ["query_wiki", "ssh_run"]


def test_supervisor_cannot_receive_domain_execution_tools():
    runtime = SkillRuntime(
        SkillLoader(Path(__file__).resolve().parents[2]),
        SkillRegistry(),
        _registry_with("literature_search", "local_process_run", "ssh_run"),
    )

    authorized = runtime.authorize(
        "supervisor",
        {"literature_search", "local_process_run", "ssh_run"},
        {"literature_search", "local_process_run", "ssh_run"},
    )

    assert authorized == ()


def test_diagnostic_agent_receives_only_bounded_repair_tools():
    tools = _registry_with(
        "read_run",
        "read_artifact",
        "read_experiment_result",
        "audit_result",
        "repair_dataset_cache",
        "retry_experiment",
        "build_experiment_bundle",
        "local_process_run",
        "ssh_run",
        "literature_search",
    )
    runtime = SkillRuntime(
        SkillLoader(Path(__file__).resolve().parents[2]),
        SkillRegistry(),
        tools,
    )

    package = runtime.prepare(
        "experiment_diagnosis",
        "diagnostic",
        configured_tools=tools.names(),
    )

    assert package.skill_ids == ("experiment-diagnosis",)
    assert set(package.authorized_tools) == {
        "read_run",
        "read_artifact",
        "read_experiment_result",
        "audit_result",
        "repair_dataset_cache",
        "retry_experiment",
        "build_experiment_bundle",
    }
    assert "local_process_run" not in package.authorized_tools
    assert "ssh_run" not in package.authorized_tools
    assert "literature_search" not in package.authorized_tools


def test_runtime_loads_conditional_skill_from_state():
    root = Path(__file__).resolve().parents[2]
    tools = _registry_with("read_run", "read_artifact", "audit_result")
    runtime = SkillRuntime(SkillLoader(root), SkillRegistry(), tools)

    package = runtime.prepare(
        "feedback_revision",
        "critic",
        configured_tools=tools.names(),
        state={"experiment_verdict": "partial"},
    )
    supported = runtime.prepare(
        "feedback_revision",
        "critic",
        configured_tools=tools.names(),
        state={"experiment_verdict": "supported"},
    )

    assert package.skill_ids == (
        "experiment-iteration",
        "result-to-claim",
        "ablation-planner",
    )
    assert supported.skill_ids == ("experiment-iteration", "result-to-claim")


def test_runtime_records_codex_style_complete_skill_invocations():
    root = Path(__file__).resolve().parents[2]
    tools = _registry_with("read_run", "read_artifact", "audit_result")
    runtime = SkillRuntime(SkillLoader(root), SkillRegistry(), tools)

    package = runtime.prepare(
        "feedback_revision",
        "critic",
        configured_tools=tools.names(),
        state={"experiment_verdict": "partial"},
    )

    invocations = package.audit["skill_invocations"]
    assert [item["skill_id"] for item in invocations] == [
        "experiment-iteration",
        "result-to-claim",
        "ablation-planner",
    ]
    assert [item["trigger"] for item in invocations] == [
        "required",
        "required",
        "conditional",
    ]
    assert all(item["load_mode"] == "complete" for item in invocations)
    assert all(item["instruction_sha256"] for item in invocations)


def test_runtime_renders_only_the_atomic_skill_requested_for_an_operation():
    root = Path(__file__).resolve().parents[2]
    tools = _registry_with(
        "read_run",
        "read_artifact",
        "local_process_run",
        "ssh_run",
        "read_experiment_result",
        "audit_result",
    )
    runtime = SkillRuntime(SkillLoader(root), SkillRegistry(), tools)
    package = runtime.prepare(
        "experiment_run_analysis",
        "experiment",
        set(runtime.tools.names()),
    )

    analysis = runtime.instructions_for(package, "analyze-results")

    assert "# Analyze Results" in analysis
    assert "# Run Experiment" not in analysis
    assert "# Experiment Audit" not in analysis


def test_instruction_budget_omits_complete_markdown_sections(tmp_path):
    _write_skill(
        tmp_path,
        "demo",
        "read_run",
        "# Keep\nshort line\n\n# Omit\nthis section must never be sliced mid-line",
    )
    context = SkillLoader(tmp_path).load("demo")

    result = InstructionBudget(max_characters=60).render([context])

    assert "# Keep\nshort line" in result.text
    assert "# Omit" not in result.text
    assert "this section" not in result.text
    assert result.omitted_sections == ("demo#Omit",)


def test_runtime_budgets_complete_skill_body_instead_of_loader_slice(tmp_path):
    long_line = "complete-section-" * 8
    _write_skill(tmp_path, "research-lit", "read_run", f"# Search\n{long_line}")
    _write_skill(tmp_path, "research-wiki", "read_run", "# Wiki\nshort")
    loader = SkillLoader(tmp_path, per_skill_limit=20, total_limit=30)
    runtime = SkillRuntime(
        loader,
        SkillRegistry(),
        _registry_with("read_run"),
        instruction_budget=InstructionBudget(max_characters=1000),
    )

    package = runtime.prepare(
        "knowledge_integration", "research", configured_tools={"read_run"}
    )

    assert long_line in package.instructions
    assert package.omitted_sections == ()
