from backend.app.workflow.phase4_quality import *
def test_literature_skills_recovery_ablation():
 p=stage1_papers([{"title":"a","relevance":.9,"abstract":"x"},{"title":"a"}]);assert len(p)==1
 assert paper_profile(p[0],["one","two"])["chunks_read"]==2
 s=production_skills("forecasting",[{"name":"common","scope":"common"},{"name":"forecast","scope":"forecasting"},{"name":"Bash MCP","scope":"common"}]);assert len(s)==2
 assert recovery_action(4,"syntax","idea")["phase"]=="deepseek_recovery" and recovery_action(7,"x","common")["route"]=="failed_system"
 assert ablation_plan(formal_positive=True,components=["A","B"],fair_contract={"seed":1})["variants"]==["baseline","A","B","A+B"]
