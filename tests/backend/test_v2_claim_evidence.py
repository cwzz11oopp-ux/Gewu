from __future__ import annotations

from backend.app.research import ClaimEvidenceGraph, ClaimStatus
from backend.app.controller import ResearchLoop
from test_v2_research_loop import SyntheticVerifiedExecutor, initial_state


def test_verified_claim_links_experiment_protocol_config_metric_and_commit():
    iterations = ResearchLoop().run(
        initial_state(), SyntheticVerifiedExecutor(), iterations=2
    )
    graph = ClaimEvidenceGraph.from_research_state(iterations[-1].state)
    audit = graph.audit()
    assert audit.exportable is True
    assert audit.verified_major_claims == 2
    assert all(claim.status == ClaimStatus.SUPPORTED for claim in graph.claims)
    link = graph.claims[0].links[0]
    assert link.experiment_id == "fixture_exp_1"
    assert link.metric_name == "accuracy"
    assert len(link.protocol_fingerprint) == 64
    assert len(link.config_fingerprint) == 64
    assert link.code_commit == "variant1"


def test_incompatible_observation_does_not_become_verified_claim():
    state = initial_state()
    state = state.model_copy(update={"experiments": [], "evidence": []})
    graph = ClaimEvidenceGraph.from_research_state(state)
    assert graph.claims == []
    assert graph.audit().exportable is False
