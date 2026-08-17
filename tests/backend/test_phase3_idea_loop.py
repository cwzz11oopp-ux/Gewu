from backend.app.workflow.phase3_idea_loop import candidate_issues, next_scientific_action, outcome_for_archives, rank_ideas, revision_payload

def ideas():
    return [{"baseline_problem":"p","modification":f"m{i}","mechanism":"x","evidence_ids":["E1"],"minimal_experiment":"small","expected_observation":"gain","innovation_score":i,"positive_improvement_probability":.5,"feasible":True} for i in range(1,5)]

def test_four_complete_ranked_ideas_and_constraints():
    ranked=rank_ideas(ideas())
    assert len(ranked)==4 and ranked[0]["rank"]==1 and ranked[0]["innovation_score"]==4
    assert candidate_issues(ranked, {"frozen": []}) == []
    assert "PHASE3_EXACTLY_FOUR_IDEAS_REQUIRED" in candidate_issues(ranked[:3], {})

def test_scientific_versions_routes_and_negative_outcome_are_bounded():
    assert next_scientific_action(evidence_route="scientific_review", stage="small_scale", version=1)["action"] == "scientific_diagnosis_and_revision"
    assert next_scientific_action(evidence_route="scientific_review", stage="small_scale", version=3)["action"] == "archive_and_next_idea"
    assert next_scientific_action(evidence_route="scientific_review", stage="small_scale", version=1, engineering_error=True)["consume_version"] is False
    assert next_scientific_action(evidence_route="expand_validation", stage="small_scale", version=1)["action"] == "formal_validation"
    assert next_scientific_action(evidence_route="expand_validation", stage="formal_validation", version=1, formal_positive=True)["action"] == "selected_idea"
    assert outcome_for_archives(["1","2","3","4"]) == "completed_negative"
    revision=revision_payload({"idea_id":"IDEA-01","modification":"a","mechanism":"m","minimal_experiment":"e"},{"why_result_differs":"d","result_evidence_id":"R"},["E2"],2)
    assert revision["version"]==2 and revision["result_evidence_id"]=="R"
