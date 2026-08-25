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
from backend.app.api import literature, paper, providers, reports, runs
from backend.app.config import Settings
from backend.app.providers.experiment import get_experiment_provider
from backend.app.providers.literature import get_literature_provider
from backend.app.providers.llm import get_llm_provider
from backend.app.storage.runtime_config import RuntimeConfigStore
from backend.app.storage.repository import Repository
from backend.app.storage.literature import LiteratureLibrary
from backend.app.storage.research_wiki import ResearchWikiStore
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
    runtime: dict[str, object]
    orchestrator: WorkflowOrchestrator | None = None
    paper_writing: PaperWritingManager | None = None

    def __post_init__(self) -> None:
        # Fingerprint of the persisted model config this process applied at
        # startup. sync_model_config() reloads when another process changes it.
        self.model_config_fingerprint = self.runtime_config.model_config_fingerprint()

    def sync_model_config(self) -> bool:
        """Reload runtime settings iff the persisted model config changed since
        this process last applied it. Returns True when a reload occurred."""
        fingerprint = self.runtime_config.model_config_fingerprint()
        if fingerprint == self.model_config_fingerprint:
            return False
        self.reload()
        return True

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
        self.model_config_fingerprint = self.runtime_config.model_config_fingerprint()


def create_app(
    data_dir: str | None = None,
    env: Mapping[str, str] | None = None,
    literature_provider_override=None,
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
        runtime=runtime,
    )
    deps.orchestrator = WorkflowOrchestrator(
        repository,
        lambda: deps.engine,
        config_sync=deps.sync_model_config,
        provider_retry_limit=settings.workflow_provider_retry_limit,
        provider_retry_backoff_seconds=settings.workflow_provider_retry_backoff_seconds,
    )
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

    @app.get("/api/system/runtime-info")
    def get_runtime_info():
        return deps.runtime

    print(format_runtime_banner(runtime))
    return app


load_dotenv()
app = create_app()
