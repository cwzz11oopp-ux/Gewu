from backend.app.workflow.phase5_closure import *
def test_report_gate_grounding_and_offline_three_tasks():
 assert report_gate("completed_positive") and report_gate("completed_negative") and not report_gate("failed_system")
 a=[{"type":"result_evidence","content":{"accuracy":.9}},{"type":"baseline_profile","content":{"x":1}}]
 assert grounding(a,{"accuracy":.9})["ok"]
 assert all(offline_paths(t)["positive"]=="completed_positive" for t in ("classification","forecasting","anomaly_detection"))
