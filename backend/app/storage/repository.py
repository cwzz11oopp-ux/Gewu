from __future__ import annotations

from functools import wraps
from threading import RLock

from backend.app.models.artifact import Artifact, EventRecord, utc_now
from backend.app.models.literature import LocalDocument
from backend.app.models.provider import EvidenceCard
from backend.app.models.run import RunRecord, StepRecord
from backend.app.storage.json_store import JsonStore


STEP_DEFS = [
    ("problem_understanding", "Problem Understanding"),
    ("knowledge_integration", "Knowledge Integration"),
    ("hypothesis_generation", "Hypothesis Generation"),
    ("evidence_reasoning", "Evidence Reasoning"),
    ("research_plan", "Research Plan"),
    ("experiment_task", "Experiment Task"),
    ("experiment_run_analysis", "Experiment Run and Analysis"),
    ("feedback_revision", "Feedback Revision"),
    ("report_export", "Report Export"),
]


def _locked(method):
    @wraps(method)
    def synchronized(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    return synchronized


class Repository:
    def __init__(self, data_dir: str) -> None:
        self.store = JsonStore(data_dir)
        self._lock = RLock()

    def _load_all(self) -> dict[str, RunRecord]:
        raw = self.store.read("runs.json")
        return {run_id: RunRecord.model_validate(value) for run_id, value in raw.items()}

    def _save_all(self, runs: dict[str, RunRecord]) -> None:
        self.store.write("runs.json", {run_id: run.model_dump() for run_id, run in runs.items()})

    @_locked
    def create_run(self, problem_input: str, title: str, domain: str = "", constraints: str = "", github_repository_url: str | None = None, research_constraints: dict | None = None, knowledge_base_id: str = "default") -> RunRecord:
        runs = self._load_all()
        run = RunRecord(
            title=title,
            domain=domain or "code-centered deep learning",
            problem_input=problem_input,
            knowledge_base_id=knowledge_base_id.strip() or "default",
            constraints=constraints,
            research_constraints=dict(research_constraints or {}),
            github_repository_url=github_repository_url.strip() if isinstance(github_repository_url, str) and github_repository_url.strip() else None,
            steps=[StepRecord(id=step_id, name=name) for step_id, name in STEP_DEFS],
        )
        runs[run.id] = run
        self._save_all(runs)
        return run

    @_locked
    def list_runs(self) -> list[RunRecord]:
        return list(self._load_all().values())

    @_locked
    def get_run(self, run_id: str) -> RunRecord:
        runs = self._load_all()
        if run_id not in runs:
            raise KeyError(run_id)
        return runs[run_id]

    @_locked
    def delete_run(self, run_id: str) -> None:
        runs = self._load_all()
        if run_id not in runs:
            raise KeyError(run_id)
        del runs[run_id]
        self._save_all(runs)

    @_locked
    def save_run(self, run: RunRecord) -> RunRecord:
        runs = self._load_all()
        run.updated_at = utc_now()
        runs[run.id] = run
        self._save_all(runs)
        return run

    @_locked
    def update_workflow_state(
        self,
        run_id: str,
        *,
        status: str | None = None,
        current_step: str | None = None,
        automatic: bool | None = None,
        stop_requested: bool | None = None,
    ) -> RunRecord:
        run = self.get_run(run_id)
        if status is not None:
            run.status = status
        if current_step is not None:
            run.current_step = current_step
        if automatic is not None:
            run.automatic = automatic
        if stop_requested is not None:
            run.stop_requested = stop_requested
        return self.save_run(run)

    @_locked
    def update_step_state(
        self,
        run_id: str,
        step_id: str,
        status: str,
        *,
        error: dict | None = None,
    ) -> RunRecord:
        run = self.get_run(run_id)
        step = next((item for item in run.steps if item.id == step_id), None)
        if step is None:
            raise KeyError(step_id)
        now = utc_now()
        step.status = status
        step.error = error
        if status == "running":
            step.started_at = now
            step.completed_at = None
            run.current_step = step_id
        elif status in {"completed", "failed", "interrupted"}:
            step.completed_at = now
        return self.save_run(run)

    @_locked
    def update_provider_retry_state(
        self,
        run_id: str,
        step_id: str,
        state: dict | None,
    ) -> RunRecord:
        """Persist only operational recovery bookkeeping for one workflow step."""
        run = self.get_run(run_id)
        current = dict(run.provider_retry_state or {})
        if state is None:
            current.pop(step_id, None)
        else:
            current[step_id] = dict(state)
        run.provider_retry_state = current
        return self.save_run(run)

    @_locked
    def update_paper_writing(self, run_id: str, values: dict) -> RunRecord:
        run = self.get_run(run_id)
        current = dict(run.paper_writing or {})
        current.update(values)
        run.paper_writing = current
        return self.save_run(run)

    @_locked
    def reconcile_interrupted_runs(self) -> list[str]:
        runs = self._load_all()
        resumable: list[str] = []
        changed = False
        for run in runs.values():
            if run.status not in {"running", "queued", "interrupted", "stopping"}:
                continue
            stopped_by_user = run.status == "stopping" or run.stop_requested
            run.status = "paused" if stopped_by_user else "interrupted"
            for step in run.steps:
                if step.status == "running":
                    step.status = "interrupted"
                    step.completed_at = utc_now()
                    step.error = {
                        "code": "PIPELINE_STOPPED" if stopped_by_user else "PROCESS_INTERRUPTED",
                        "message": (
                            "The user stopped this run before the step completed."
                            if stopped_by_user
                            else "The backend stopped before the step completed; recovery is scheduled."
                        ),
                    }
            if run.automatic and not stopped_by_user:
                resumable.append(run.id)
            run.updated_at = utc_now()
            changed = True
        if changed:
            self._save_all(runs)
        return resumable

    @_locked
    def add_artifact(
        self,
        run_id: str,
        artifact_type: str,
        title: str,
        content: dict,
        source_step: str,
        created_by: str,
        parent_artifact_id: str | None = None,
        self_id_field: str | None = None,
    ) -> Artifact:
        run = self.get_run(run_id)
        version = 1 + sum(1 for artifact in run.artifacts if artifact.type == artifact_type)
        artifact = Artifact(
            run_id=run_id,
            type=artifact_type,
            version=version,
            title=title,
            content=content,
            source_step=source_step,
            created_by=created_by,
            parent_artifact_id=parent_artifact_id,
        )
        if self_id_field:
            # Populate self identity before the first durable write.  This keeps
            # governance artifacts immutable after they enter append-only history.
            artifact.content[self_id_field] = artifact.id
        run.artifacts.append(artifact)
        self.save_run(run)
        return artifact

    @_locked
    def add_artifacts_atomic(self, run_id: str, specs: list[dict]) -> list[Artifact]:
        """Append a related artifact set with one durable store replacement."""
        run = self.get_run(run_id)
        counts: dict[str, int] = {}
        for artifact in run.artifacts:
            counts[artifact.type] = counts.get(artifact.type, 0) + 1
        created: list[Artifact] = []
        for spec in specs:
            artifact_type = str(spec["artifact_type"])
            counts[artifact_type] = counts.get(artifact_type, 0) + 1
            artifact = Artifact(
                id=str(spec["artifact_id"]),
                run_id=run_id,
                type=artifact_type,
                version=counts[artifact_type],
                title=str(spec["title"]),
                content=dict(spec.get("content") or {}),
                source_step=str(spec["source_step"]),
                created_by=str(spec["created_by"]),
                parent_artifact_id=spec.get("parent_artifact_id"),
            )
            self_id_field = spec.get("self_id_field")
            if self_id_field:
                artifact.content[str(self_id_field)] = artifact.id
            created.append(artifact)
        run.artifacts.extend(created)
        self.save_run(run)
        return created

    @_locked
    def get_artifact(self, run_id: str, artifact_id: str) -> Artifact:
        run = self.get_run(run_id)
        for artifact in run.artifacts:
            if artifact.id == artifact_id:
                return artifact
        raise KeyError(artifact_id)

    @_locked
    def lock_artifact(self, run_id: str, artifact_id: str, locked: bool) -> Artifact:
        run = self.get_run(run_id)
        for index, artifact in enumerate(run.artifacts):
            if artifact.id == artifact_id:
                updated = artifact.model_copy(update={"locked": locked})
                run.artifacts[index] = updated
                self.save_run(run)
                return updated
        raise KeyError(artifact_id)

    @_locked
    def append_event(
        self,
        run_id: str,
        step_id: str,
        actor: str,
        message: str,
        data: dict | None = None,
        input_summary: dict | None = None,
        output_summary: dict | None = None,
        tool_calls: list[dict] | None = None,
        provider_mode: str = "",
        fallback_used: bool = False,
        fallback_reason: str = "",
    ) -> EventRecord:
        run = self.get_run(run_id)
        event = EventRecord(
            run_id=run_id,
            step_id=step_id,
            actor=actor,
            message=message,
            data=data or {},
            input_summary=input_summary or {},
            output_summary=output_summary or {},
            tool_calls=tool_calls or [],
            provider_mode=provider_mode,
            fallback_used=fallback_used,
            fallback_reason=fallback_reason,
        )
        run.events.append(event)
        self.save_run(run)
        return event

    @_locked
    def attach_local_document(self, run_id: str, document: LocalDocument) -> Artifact:
        run = self.get_run(run_id)
        previous = next(
            (artifact for artifact in reversed(run.artifacts) if artifact.type == "evidence"),
            None,
        )
        content = dict(previous.content) if previous else {}
        references = list(content.get("references") or [])
        local_only = list(content.get("local_only") or [])
        card = EvidenceCard(
            title=document.title or document.filename,
            authors=document.authors,
            year=document.year,
            source=document.source,
            source_kind="local",
            local_document_id=document.id,
            abstract=document.abstract,
            url=(
                f"https://doi.org/{document.identifiers['doi']}"
                if document.identifiers.get("doi")
                else (
                    f"https://arxiv.org/abs/{document.identifiers['arxiv']}"
                    if document.identifiers.get("arxiv")
                    else ""
                )
            ),
            identifiers=document.identifiers,
            verified=document.verification.verified,
        )
        references = [
            item for item in references
            if item.get("local_document_id") != document.id
        ]
        local_only = [
            item for item in local_only
            if item.get("local_document_id") != document.id
        ]
        if card.exportable:
            references.append(card.model_dump())
        else:
            local_only.append(card.model_dump())
        content["references"] = references
        content["local_only"] = local_only
        sources = dict(content.get("sources") or {})
        sources["local"] = len(local_only)
        sources["verified_local"] = sum(
            item.get("source_kind") == "local" for item in references
        )
        content["sources"] = sources
        return self.add_artifact(
            run_id,
            "evidence",
            "Evidence with Local Literature",
            content,
            "knowledge_integration",
            "human",
            parent_artifact_id=previous.id if previous else None,
        )

    @_locked
    def next_experiment_id(self, run_id: str) -> str:
        run = self.get_run(run_id)
        numbers = []
        for artifact in run.artifacts:
            if artifact.type != "experiment_task":
                continue
            value = str(artifact.content.get("experiment_id") or "")
            if value.startswith("experiment_") and value[11:].isdigit():
                numbers.append(int(value[11:]))
        return f"experiment_{max(numbers, default=0) + 1}"
