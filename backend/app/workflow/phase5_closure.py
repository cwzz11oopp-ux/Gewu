"""Offline-only Phase 5 closure guards and acceptance fixtures."""
from __future__ import annotations
import re
from typing import Any

REPORTABLE={"completed_positive","completed_negative"}
TERMINAL={"completed_positive","completed_negative","paused","terminated_by_user","preflight_failed","failed_system","engineering_unresolved"}
ALLOWED={"dataset_profile","research_constraints","paper_profile","baseline_profile","result_evidence","scientific_diagnosis","idea_revision","ablation_result_evidence"}

def report_gate(status:str)->bool:return status in REPORTABLE
def grounding(artifacts:list[dict[str,Any]], report:dict[str,Any])->dict[str,Any]:
    verified=[a for a in artifacts if a.get("type") in ALLOWED]
    text=str(report)
    numbers=set(re.findall(r"(?<![A-Za-z])\d+(?:\.\d+)?",text))
    source=str(verified)
    unknown=sorted(n for n in numbers if n not in source and n not in {"1","2","3","4","5"})
    return {"ok":not unknown,"verified_artifact_count":len(verified),"unknown_numbers":unknown}
def offline_paths(task:str)->dict[str,Any]:
    assert task in {"classification","forecasting","anomaly_detection"}
    return {"task":task,"positive":"completed_positive","negative":"completed_negative","ambiguous":"add_seeds","engineering":"recovery_1_to_6","baseline":"approximate_reproduction","recovery":"resume_preserves_artifacts","pause":"paused","terminate":"terminated_by_user"}
