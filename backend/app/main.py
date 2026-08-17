from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.agents.reviewer import ReviewerAgent
from backend.app.agents.supervisor import SupervisorAgent
from backend.app.bootstrap import GreenfieldBootstrapService
from backend.app.api import literature, paper, providers, reports, runs, v2_research
from backend.app.config import Settings
from backend.app.providers.experiment import get_experiment_provider
from backend.app.providers.literature import get_literature_provider
from backend.app.providers.llm import get_llm_provider
from backend.app.literature import SprintLiteratureService
from backend.app.models.gateway import LegacyQwenAdapter
from backend.app.research.ideator import BranchConstructor
from backend.app.services.v2_sessions import ResearchSessionService
from backend.app.services.v2_runner import V2ResearchRunner
from backend.app.services.v2_critic import ScientificCritic
from backend.app.storage.runtime_config import RuntimeConfigStore
from backend.app.storage.repository import Repository
from backend.app.storage.literature import LiteratureLibrary
from backend.app.storage.research_wiki import ResearchWikiStore
from backend.app.storage.v2 import V2Stores
from backend.app.workflow.engine import WorkflowEngine
from backend.app.workflow.knowledge import KnowledgeIntegrationService
from backend.app.workflow.orchestrator import WorkflowOrchestrator
from backend.app.workflow.skills import SkillCatalog, SkillLoader, SkillRegistry
from backend.app.paper_writing import PaperWritingManager
from backend.app.runtime_info import format_runtime_banner, runtime_info


@dataclass
class Dependencies:
    data_dir: str
    base_settings: Settings
    settings: Settings
    repository: Repository
    engine: WorkflowEngine
    runtime_config: RuntimeConfigStore
    skill_loader: SkillLoader
    skill_registry: SkillRegistry
    skill_catalog: SkillCatalog
    literature_library: LiteratureLibrary
    literature_provider: object
    research_wiki: ResearchWikiStore
    knowledge_service: KnowledgeIntegrationService
    v2_sessions: ResearchSessionService
    v2_runner: V2ResearchRunner
    greenfield_bootstrap: GreenfieldBootstrapService
    runtime: dict[str, object]
    orchestrator: WorkflowOrchestrator | None = None
    paper_writing: PaperWritingManager | None = None

    def reload(self) -> None:
        self.settings = self.runtime_config.apply(self.base_settings)
        llm_provider = get_llm_provider(self.settings)
        literature_provider = get_literature_provider(self.settings)
        self.literature_provider = literature_provider
        self.knowledge_service = KnowledgeIntegrationService(
            self.research_wiki,
            self.literature_library,
            literature_provider,
        )
        experiment_provider = get_experiment_provider(self.settings)
        reviewer = None if llm_provider.fallback else ReviewerAgent(llm_provider)
        supervisor = SupervisorAgent(self.skill_registry, reviewer)
        self.engine = WorkflowEngine(
            self.repository,
            llm_provider,
            literature_provider,
            experiment_provider,
            self.skill_loader,
            self.skill_registry,
            self.skill_catalog,
            supervisor_agent=supervisor,
            knowledge_service=self.knowledge_service,
            competition_mode=self.settings.competition_mode,
            max_feedback_iterations=self.settings.feedback_max_iterations,
            max_deepseek_plan_revision=self.settings.max_deepseek_plan_revision,
        )
        self.v2_sessions = _build_v2_sessions(
            self.data_dir,
            self.settings,
            llm_provider,
            literature_provider,
            self.literature_library,
        )
        self.greenfield_bootstrap = _build_greenfield_bootstrap(
            self.data_dir,
            self.v2_sessions,
            llm_provider,
            literature_provider,
            self.literature_library,
        )
        self.v2_runner = V2ResearchRunner(
            self.v2_sessions, LegacyQwenAdapter(llm_provider), self.data_dir
        )


def _build_v2_sessions(
    data_dir: str,
    settings: Settings,
    llm_provider,
    literature_provider,
    literature_library: LiteratureLibrary,
) -> ResearchSessionService:
    gateway = LegacyQwenAdapter(llm_provider)
    return ResearchSessionService(
        V2Stores(data_dir),
        BranchConstructor(gateway),
        SprintLiteratureService(literature_provider, literature_library),
        model_ready=(
            settings.llm_provider == "qwen"
            and bool(settings.qwen_api_key)
            and not getattr(llm_provider, "fallback", False)
        ),
        critic=(
            ScientificCritic(gateway)
            if settings.llm_provider == "qwen"
            and bool(settings.qwen_api_key)
            and not getattr(llm_provider, "fallback", False)
            else None
        ),
    )


def _build_greenfield_bootstrap(
    data_dir: str,
    sessions: ResearchSessionService,
    llm_provider,
    literature_provider,
    literature_library: LiteratureLibrary,
) -> GreenfieldBootstrapService:
    return GreenfieldBootstrapService(
        data_dir,
        sessions,
        LegacyQwenAdapter(llm_provider),
        SprintLiteratureService(literature_provider, literature_library),
    )


def create_app(
    data_dir: str | None = None,
    env: Mapping[str, str] | None = None,
    literature_provider_override=None,
    v2_session_service_override=None,
) -> FastAPI:
    base_settings = Settings.from_env(env)
    resolved_data_dir = data_dir or base_settings.data_dir
    runtime_config = RuntimeConfigStore(resolved_data_dir)
    settings = runtime_config.apply(base_settings)
    repository = Repository(resolved_data_dir)
    llm_provider = get_llm_provider(settings)
    literature_provider = literature_provider_override or get_literature_provider(settings)
    experiment_provider = get_experiment_provider(settings)
    skill_loader = SkillLoader(Path(__file__).resolve().parents[2])
    skill_registry = SkillRegistry()
    skill_catalog = SkillCatalog(skill_loader)
    literature_library = LiteratureLibrary(Path(resolved_data_dir) / "literature")
    research_wiki = ResearchWikiStore(Path(resolved_data_dir) / "research-wiki")
    knowledge_service = KnowledgeIntegrationService(
        research_wiki,
        literature_library,
        literature_provider,
    )
    reviewer = None if llm_provider.fallback else ReviewerAgent(llm_provider)
    supervisor = SupervisorAgent(skill_registry, reviewer)
    engine = WorkflowEngine(
        repository,
        llm_provider,
        literature_provider,
        experiment_provider,
        skill_loader,
        skill_registry,
        skill_catalog,
        supervisor_agent=supervisor,
        knowledge_service=knowledge_service,
        competition_mode=settings.competition_mode,
        max_feedback_iterations=settings.feedback_max_iterations,
        max_deepseek_plan_revision=settings.max_deepseek_plan_revision,
    )
    v2_sessions = v2_session_service_override or _build_v2_sessions(
        resolved_data_dir,
        settings,
        llm_provider,
        literature_provider,
        literature_library,
    )
    v2_runner = V2ResearchRunner(
        v2_sessions, LegacyQwenAdapter(llm_provider), resolved_data_dir
    )
    greenfield_bootstrap = _build_greenfield_bootstrap(
        resolved_data_dir,
        v2_sessions,
        llm_provider,
        literature_provider,
        literature_library,
    )
    runtime = runtime_info(Path(__file__).resolve().parents[2], skill_loader.skills_root)
    deps = Dependencies(
        data_dir=resolved_data_dir,
        base_settings=base_settings,
        settings=settings,
        repository=repository,
        engine=engine,
        runtime_config=runtime_config,
        skill_loader=skill_loader,
        skill_registry=skill_registry,
        skill_catalog=skill_catalog,
        literature_library=literature_library,
        literature_provider=literature_provider,
        research_wiki=research_wiki,
        knowledge_service=knowledge_service,
        v2_sessions=v2_sessions,
        v2_runner=v2_runner,
        greenfield_bootstrap=greenfield_bootstrap,
        runtime=runtime,
    )
    deps.orchestrator = WorkflowOrchestrator(repository, lambda: deps.engine)
    deps.paper_writing = PaperWritingManager(
        repository,
        lambda: deps.engine.llm_provider,
        skill_loader,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        deps.orchestrator.recover()
        deps.paper_writing.recover()
        try:
            yield
        finally:
            # Experiment subprocesses are intentionally left alive. Their durable
            # runtime status allows the next backend process to recover results.
            deps.orchestrator.mark_for_shutdown()

    app = FastAPI(title="AI Scientist Workbench", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.allowed_origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(runs.build_router(deps))
    app.include_router(literature.build_router(deps))
    app.include_router(providers.build_router(deps))
    app.include_router(reports.build_router(deps))
    app.include_router(paper.build_router(deps))
    app.include_router(v2_research.build_router(deps))

    @app.get("/api/system/runtime-info")
    def get_runtime_info():
        return deps.runtime

    print(format_runtime_banner(runtime))
    return app


load_dotenv()
app = create_app()
