from __future__ import annotations

from threading import RLock

from backend.app.research.evidence import EvidenceUnit
from backend.app.research.experiment import ExperimentRecord
from backend.app.research.frontier import ResearchFrontier
from backend.app.models.v2_session import ResearchSessionEvent
from backend.app.state.research import ResearchState
from backend.app.storage.json_store import JsonStore


class _TypedJsonStore:
    filename: str

    def __init__(self, data_dir: str) -> None:
        self.store = JsonStore(data_dir)
        self._lock = RLock()

    def _read(self) -> dict:
        return self.store.read(self.filename)

    def _write(self, value: dict) -> None:
        self.store.write(self.filename, value)


class ResearchStateStore(_TypedJsonStore):
    filename = "v2-research-states.json"

    def save(self, state: ResearchState) -> str:
        with self._lock:
            values = self._read()
            values[state.session_id] = state.model_dump(mode="json")
            self._write(values)
        return state.session_id

    def get(self, session_id: str) -> ResearchState:
        values = self._read()
        if session_id not in values:
            raise KeyError(session_id)
        return ResearchState.model_validate(values[session_id])


class FrontierStore(_TypedJsonStore):
    filename = "v2-frontiers.json"

    def save(self, session_id: str, frontier: ResearchFrontier) -> str:
        with self._lock:
            values = self._read()
            values[session_id] = frontier.model_dump(mode="json")
            self._write(values)
        return session_id

    def get(self, session_id: str) -> ResearchFrontier:
        values = self._read()
        if session_id not in values:
            raise KeyError(session_id)
        return ResearchFrontier.model_validate(values[session_id])


class ExperimentRegistry(_TypedJsonStore):
    filename = "v2-experiments.json"

    def save(self, session_id: str, record: ExperimentRecord) -> str:
        with self._lock:
            values = self._read()
            session = dict(values.get(session_id) or {})
            existing = session.get(record.experiment_id)
            payload = record.model_dump(mode="json")
            if existing is not None and existing != payload:
                raise ValueError(f"EXPERIMENT_RECORD_CONFLICT:{record.experiment_id}")
            session[record.experiment_id] = payload
            values[session_id] = session
            self._write(values)
        return record.experiment_id

    def list(self, session_id: str) -> list[ExperimentRecord]:
        session = dict(self._read().get(session_id) or {})
        return [
            ExperimentRecord.model_validate(session[key]) for key in sorted(session)
        ]


class EvidenceStore(_TypedJsonStore):
    filename = "v2-evidence.json"

    def save(self, session_id: str, evidence: EvidenceUnit) -> str:
        with self._lock:
            values = self._read()
            session = dict(values.get(session_id) or {})
            existing = session.get(evidence.id)
            payload = evidence.model_dump(mode="json")
            if existing is not None and existing != payload:
                raise ValueError(f"EVIDENCE_RECORD_CONFLICT:{evidence.id}")
            session[evidence.id] = payload
            values[session_id] = session
            self._write(values)
        return evidence.id

    def list(self, session_id: str) -> list[EvidenceUnit]:
        session = dict(self._read().get(session_id) or {})
        return [EvidenceUnit.model_validate(session[key]) for key in sorted(session)]


class ResearchEventStore(_TypedJsonStore):
    filename = "v2-research-events.json"

    def append(self, event: ResearchSessionEvent) -> str:
        with self._lock:
            values = self._read()
            session = list(values.get(event.session_id) or [])
            if any(item.get("id") == event.id for item in session):
                return event.id
            session.append(event.model_dump(mode="json"))
            values[event.session_id] = session
            self._write(values)
        return event.id

    def list(self, session_id: str) -> list[ResearchSessionEvent]:
        return [
            ResearchSessionEvent.model_validate(item)
            for item in list(self._read().get(session_id) or [])
        ]


class V2Stores:
    def __init__(self, data_dir: str) -> None:
        self.states = ResearchStateStore(data_dir)
        self.frontiers = FrontierStore(data_dir)
        self.experiments = ExperimentRegistry(data_dir)
        self.evidence = EvidenceStore(data_dir)
        self.events = ResearchEventStore(data_dir)

    def persist(self, state: ResearchState) -> None:
        for record in state.experiments:
            self.experiments.save(state.session_id, record)
        for evidence in state.evidence:
            self.evidence.save(state.session_id, evidence)
        self.frontiers.save(state.session_id, state.frontier)
        self.states.save(state)
