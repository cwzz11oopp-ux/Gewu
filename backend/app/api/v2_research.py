from __future__ import annotations

from typing import Protocol

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.app.bootstrap import (
    DatasetDownloadApprovalRequired,
    GreenfieldBootstrapRequest,
)
from backend.app.research.budget import BudgetState
from backend.app.research.experiment import ExperimentRecord
from backend.app.research.profiles import BaselineProfile, ProblemProfile
from backend.app.services.v2_sessions import ModelUnavailable, SessionConflict


class _Dependencies(Protocol):
    v2_sessions: object
    v2_runner: object


class CreateSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    problem: ProblemProfile
    budget: BudgetState
    baseline: BaselineProfile | None = None


class ContinueSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    baseline: BaselineProfile | None = None
    experiment: ExperimentRecord | None = None

    @model_validator(mode="after")
    def exactly_one_result(self):
        if (self.baseline is None) == (self.experiment is None):
            raise ValueError("EXACTLY_ONE_CONTINUATION_RESULT_REQUIRED")
        return self


class StopSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(default="Stopped by user", min_length=1)


def build_router(deps: _Dependencies) -> APIRouter:
    router = APIRouter(prefix="/api/v2/research/sessions", tags=["v2-research"])

    def session_or_404(session_id: str):
        try:
            return deps.v2_sessions.get(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="RESEARCH_SESSION_NOT_FOUND") from exc

    def transition(operation):
        try:
            value = operation()
            if hasattr(value, "model_dump"):
                return value.model_dump(mode="json")
            if hasattr(value, "state"):
                return {
                    "state": value.state.model_dump(mode="json"),
                    "action": value.action.model_dump(mode="json") if value.action else None,
                    "critique": value.critique.model_dump(mode="json") if value.critique else None,
                }
            return value
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="RESEARCH_SESSION_NOT_FOUND") from exc
        except ModelUnavailable as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
        except SessionConflict as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    @router.post("", status_code=status.HTTP_201_CREATED)
    def create_session(request: CreateSessionRequest):
        problem = deps.v2_runner.prepare_problem(request.problem)
        return deps.v2_sessions.create(
            problem, request.budget, request.baseline
        ).model_dump(mode="json")

    @router.post("/bootstrap/datasets/inspect")
    def inspect_bootstrap_dataset(request: GreenfieldBootstrapRequest):
        try:
            return deps.greenfield_bootstrap.inspect_dataset(request).model_dump(mode="json")
        except DatasetDownloadApprovalRequired as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.detail) from exc
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    @router.post("/bootstrap", status_code=status.HTTP_201_CREATED)
    def bootstrap_greenfield(request: GreenfieldBootstrapRequest):
        try:
            return deps.greenfield_bootstrap.run(request).model_dump(mode="json")
        except DatasetDownloadApprovalRequired as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.detail) from exc
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
        except RuntimeError as exc:
            code = status.HTTP_503_SERVICE_UNAVAILABLE if "QWEN" in str(exc) else status.HTTP_409_CONFLICT
            raise HTTPException(status_code=code, detail=str(exc)) from exc

    @router.get("/{session_id}/bootstrap")
    def get_bootstrap(session_id: str):
        try:
            return deps.greenfield_bootstrap.get(session_id).model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="GREENFIELD_BOOTSTRAP_NOT_FOUND") from exc

    @router.post("/{session_id}/start")
    def start_session(session_id: str):
        return transition(lambda: deps.v2_sessions.start(session_id))

    @router.post("/{session_id}/continue")
    def continue_session(session_id: str, request: ContinueSessionRequest):
        return transition(
            lambda: deps.v2_sessions.continue_session(
                session_id,
                baseline=request.baseline,
                experiment=request.experiment,
            )
        )

    @router.post("/{session_id}/run-next")
    def run_next(session_id: str):
        """Execute exactly one real pending action, then select the next action."""
        return transition(lambda: deps.v2_runner.run_next(session_id))

    @router.post("/{session_id}/run")
    def run(session_id: str):
        """Autonomous bounded execution; stops at any explicit runner boundary."""
        latest = None
        while True:
            latest = deps.v2_runner.run_next(session_id)
            if latest.action is None or latest.state.stopped:
                return transition(lambda: latest)

    @router.post("/{session_id}/stop")
    def stop_session(session_id: str, request: StopSessionRequest):
        return transition(lambda: deps.v2_sessions.stop(session_id, request.reason))

    @router.get("/{session_id}/state")
    def get_state(session_id: str):
        return session_or_404(session_id).model_dump(mode="json")

    @router.get("/{session_id}/frontier")
    def get_frontier(session_id: str):
        session_or_404(session_id)
        return deps.v2_sessions.stores.frontiers.get(session_id).model_dump(mode="json")

    @router.get("/{session_id}/experiments")
    def get_experiments(session_id: str):
        session_or_404(session_id)
        return [item.model_dump(mode="json") for item in deps.v2_sessions.stores.experiments.list(session_id)]

    @router.get("/{session_id}/evidence")
    def get_evidence(session_id: str):
        session_or_404(session_id)
        return [item.model_dump(mode="json") for item in deps.v2_sessions.stores.evidence.list(session_id)]

    @router.get("/{session_id}/summary")
    def get_summary(session_id: str):
        return transition(lambda: deps.v2_sessions.summary(session_id))

    @router.get("/{session_id}/events")
    def get_events(session_id: str):
        return transition(
            lambda: [
                item.model_dump(mode="json")
                for item in deps.v2_sessions.events(session_id)
            ]
        )

    @router.get("/{session_id}/findings")
    def get_findings(session_id: str):
        return transition(lambda: deps.v2_sessions.findings(session_id))

    @router.get("/{session_id}/claims")
    def get_claims(session_id: str):
        session_or_404(session_id)
        graphs = [
            event.payload
            for event in deps.v2_sessions.events(session_id)
            if event.kind.value == "CLAIM_GRAPH_UPDATED"
        ]
        if graphs:
            latest = graphs[-1]
            if isinstance(latest.get("graph"), dict):
                return {
                    **latest["graph"],
                    "audit": latest.get("audit") or latest["graph"].get("audit", {}),
                }
            return latest
        return {
            "claims": [],
            "audit": {
                "exportable": False,
                "reason": "CLAIM_EVIDENCE_GRAPH_NOT_YET_COMPUTED",
            },
        }

    @router.get("/{session_id}/parameter-sweep")
    def get_parameter_sweep(session_id: str):
        session_or_404(session_id)
        sweeps = [
            event.payload
            for event in deps.v2_sessions.events(session_id)
            if event.kind.value == "PARAMETER_SWEEP_RECORDED"
        ]
        return sweeps[-1] if sweeps else {"points": [], "stable_improvement_intervals": []}

    @router.get("/{session_id}/trajectory")
    def get_trajectory(session_id: str):
        state = session_or_404(session_id)
        items = [
            {
                "stage": "Research Question",
                "summary": state.problem.question,
                "iteration": 0,
                "created_at": None,
                "status": "recorded",
                "references": [],
            }
        ]
        critique_seen = False
        for event in deps.v2_sessions.events(session_id):
            payload = event.payload
            common = {
                "iteration": event.iteration,
                "created_at": event.created_at.isoformat(),
                "status": "recorded",
            }
            if event.kind.value == "BRANCH_GATE":
                accepted = payload.get("accepted", [])
                items.append({
                    **common,
                    "stage": "Branch Proposal",
                    "summary": f"{len(accepted)} branches passed the scientific gate",
                    "references": [item.get("branch_id") for item in accepted if item.get("branch_id")],
                })
            elif event.kind.value == "ACTION_SELECTED":
                items.append({
                    **common,
                    "stage": "Follow-up Action" if critique_seen else "Controller Decision",
                    "summary": str(payload.get("operator", "ACTION_SELECTED")),
                    "references": [value for value in [payload.get("id"), payload.get("branch_id")] if value],
                })
                critique_seen = False
            elif event.kind.value == "EXPERIMENT_RECORDED":
                experiment_id = payload.get("experiment_id")
                refs = [experiment_id] if experiment_id else []
                items.extend([
                    {**common, "stage": "Experiment", "summary": str(payload.get("operator", "FORMAL_EXPERIMENT")), "references": refs},
                    {**common, "stage": "Result", "summary": str(payload.get("metrics", {})), "references": refs},
                ])
            elif event.kind.value == "CRITIQUE_RECORDED":
                critique_seen = True
                items.append({
                    **common,
                    "stage": "Critic",
                    "summary": "; ".join(payload.get("recommended_actions", [])) or "Scientific critique recorded",
                    "references": [payload.get("branch_id")] if payload.get("branch_id") else [],
                })
            elif event.kind.value == "PARAMETER_SWEEP_RECORDED":
                points = payload.get("points", [])
                items.extend([
                    {**common, "stage": "Experiment", "summary": f"Locked parameter sweep: {len(points)} runs", "references": [item.get("experiment_id") for item in points if item.get("experiment_id")]},
                    {**common, "stage": "Result", "summary": "Parameter-response evidence recorded", "references": [item.get("experiment_id") for item in points if item.get("experiment_id")]},
                ])
            elif event.kind.value == "CLAIM_GRAPH_UPDATED":
                graph = payload.get("graph") or payload
                claims = graph.get("claims", [])
                items.append({
                    **common,
                    "stage": "Evidence Update",
                    "summary": f"{len(claims)} claims audited",
                    "references": [claim.get("id") for claim in claims if claim.get("id")],
                })
            elif event.kind.value == "REPORT_EXPORTED":
                conclusion = payload.get("final_conclusion") or {}
                supported = conclusion.get("supported", []) if isinstance(conclusion, dict) else []
                items.append({
                    **common,
                    "stage": "Final Conclusion",
                    "summary": "; ".join(str(item) for item in supported) or "Bounded research conclusion exported",
                    "references": [str(payload.get("docx"))] if payload.get("docx") else [],
                })
        return items

    return router
