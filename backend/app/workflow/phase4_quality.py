"""Deterministic Phase 4 quality/recovery contracts; no provider or training calls."""
from __future__ import annotations
from copy import deepcopy
from typing import Any

TASK_SKILLS={"classification":"classification","forecasting":"forecasting","anomaly_detection":"anomaly_detection"}
FOREIGN=("codex","claude","gemini","bash","websearch","mcp")

def stage1_papers(papers:list[dict[str,Any]], budget:int=40)->list[dict[str,Any]]:
    out=[]
    seen=set()
    for paper in papers[:budget]:
        key=str(paper.get("doi") or paper.get("url") or paper.get("title","")).casefold()
        if not key or key in seen: continue
        seen.add(key); item=deepcopy(paper); text=" ".join(str(item.get(k,"")) for k in ("title","abstract","conclusion","future_work"))
        item.update({"reading_stage":"stage1","classification":"Recent Core" if item.get("relevance",0)>=.7 else "Supporting","selection_reason":"high_information_value" if item.get("relevance",0)>=.7 else "supporting_context","evidence_id":item.get("evidence_id") or f"P{len(out)+1:03d}","read_fields":["title","abstract","conclusion","future_work"],"context_policy":"chunked_no_silent_truncation"})
        out.append(item)
    return out

def paper_profile(paper:dict[str,Any], chunks:list[str])->dict[str,Any]:
    return {"paper_id":paper["evidence_id"],"reading_stage":"stage2","chunks_read":len(chunks),"problem":paper.get("problem",""),"method":paper.get("method",""),"key_mechanism":paper.get("mechanism",""),"baseline":paper.get("baseline",""),"dataset":paper.get("dataset",""),"metrics":paper.get("metrics",[]),"important_results":paper.get("results",[]),"limitations":paper.get("limitations",[]),"future_work":paper.get("future_work",""),"implementation_details":paper.get("implementation_details",""),"reproducibility":paper.get("reproducibility","unknown"),"provenance":[paper["evidence_id"]],"content":"\n".join(chunks)}

def production_skills(task_type:str, available:list[dict[str,Any]])->list[dict[str,Any]]:
    scope=TASK_SKILLS[task_type]; selected=[]
    for item in available:
        name=str(item.get("name","")).casefold()
        if any(x in name for x in FOREIGN): continue
        if item.get("scope") in {"common",scope}: selected.append({**item,"task_scope":item.get("scope"),"allowed_tools":item.get("allowed_tools",[])})
    return selected

def recovery_action(attempt:int, category:str, scope:str)->dict[str,Any]:
    if attempt<=3:return {"phase":"qwen_repair","attempt":attempt,"consume_idea_version":False}
    if attempt<=6:return {"phase":"deepseek_recovery","attempt":attempt,"consume_idea_version":False}
    return {"phase":"engineering_unresolved","route":"archive_idea" if scope=="idea" else "failed_system","consume_idea_version":False}

def ablation_plan(*, formal_positive:bool, components:list[str], fair_contract:dict)->dict[str,Any]:
    if not formal_positive or len(components)<2:return {"triggered":False,"reason":"FORMAL_POSITIVE_AND_DECOMPOSABLE_COMPONENTS_REQUIRED"}
    variants=["baseline",*['+'.join(combo) for combo in ([components[0]],[components[1]],components)]]
    return {"triggered":True,"variants":variants,"fair_contract":deepcopy(fair_contract),"result_evidence_required":True}
