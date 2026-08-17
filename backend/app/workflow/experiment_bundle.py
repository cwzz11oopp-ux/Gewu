from __future__ import annotations

from backend.app.models.experiment import EXPERIMENT_ID_PATTERN


def result_id_for(experiment_id: str) -> str:
    if not EXPERIMENT_ID_PATTERN.fullmatch(experiment_id):
        raise ValueError(f"EXPERIMENT_ID_INVALID:{experiment_id}")
    return f"{experiment_id}_result"
