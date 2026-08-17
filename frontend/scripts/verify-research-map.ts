import { buildResearchViewModel } from "../src/components/researchViewModel";
import type { RunRecord } from "../src/api/types";

const run = {
  id: "run_fixture_research_map",
  title: "Research Map fixture",
  problem_input: "How should a compact model be evaluated?",
  status: "hypothesis_revision_required",
  current_step: "evidence_reasoning",
  artifacts: [
    {
      id: "art_evidence", type: "evidence", version: 1, title: "Evidence", source_step: "knowledge_integration", created_by: "fixture", locked: false, created_at: "2026-01-01T00:00:00Z",
      content: { references: [{ title: "Paper A", url: "https://example.test/a", verified: true }] },
    },
    {
      id: "art_synthesis", type: "research_synthesis", version: 1, title: "Synthesis", source_step: "knowledge_integration", created_by: "fixture", locked: false, created_at: "2026-01-01T00:00:01Z",
      content: {
        schema_version: 1, source_collection: { paper_count: 45 }, literature_coverage: { decision: "hard_cap_reached", hard_cap_reached: true }, future_work: [{ future_work_id: "FW-001" }],
        papers: [{ paper_id: "PAPER-001", title: "Paper A", url: "https://example.test/a" }],
        themes: [{ theme_id: "THEME-001", title: "Evaluation", source_paper_ids: ["PAPER-001"], source_claim_ids: ["CLAIM-001"] }],
        research_gaps: [{ gap_id: "GAP-001", title: "Domain shift", description: "Future work requires domain-shift evaluation.", source_paper_ids: ["PAPER-001"], source_claim_ids: ["CLAIM-001"], source_future_work_ids: ["FW-001"] }],
      },
    },
    {
      id: "art_hypothesis", type: "hypothesis", version: 1, title: "Hypothesis", source_step: "hypothesis_generation", created_by: "fixture", locked: false, created_at: "2026-01-01T00:00:02Z",
      content: { hypothesis_round: { round_id: "HYPOTHESIS-ROUND-001", round_index: 1, parent_round_id: "", revision_reason: "initial", created_candidate_ids: ["CAND-001"], scientific_feedback: [] }, candidates: [{ candidate_id: "CAND-001", claim: "A robust compact model can be tested.", source_gap_ids: ["GAP-001"], source_paper_ids: ["PAPER-001"], source_claim_ids: ["CLAIM-001"], source_future_work_ids: ["FW-001"], provenance_status: "grounded" }] },
    },
    {
      id: "art_reasoning", type: "reasoning", version: 1, title: "Reasoning", source_step: "evidence_reasoning", created_by: "fixture", locked: false, created_at: "2026-01-01T00:00:03Z",
      content: { evidence_registry: [{ evidence_id: "EVID-001", paper_id: "PAPER-001", claim: "A verified result.", stance: "contradict" }], candidate_assessments: [{ candidate_index: 0, status: "evidence_insufficient" }] },
    },
  ],
  events: [], steps: [], created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:03Z",
} as unknown as RunRecord;

const model = buildResearchViewModel(run, null, null);
const nodeIds = new Set(model.nodes.map((node) => node.id));
if (!["LITERATURE", "THEMES", "GAPS", "HYPOTHESES", "EVIDENCE_REVIEW"].every((id) => nodeIds.has(id))) throw new Error("aggregate research map nodes missing");
if (model.nodes.some((node) => /^(P\d+|EVID-)/.test(node.id))) throw new Error("synthetic paper/evidence nodes must not be built");
if (model.status !== "revision_required") throw new Error("revision-required run status was not preserved");
if (model.hypotheses[0]?.status !== "evidence_insufficient") throw new Error("candidate evidence status was not rendered");
if (!model.hypotheses[0]?.provenanceAvailable || model.hypotheses[0]?.sourceGapIds[0] !== "GAP-001") throw new Error("gap provenance was not preserved");
if (model.hypotheses[0]?.sourceFutureWorkIds[0] !== "FW-001") throw new Error("future-work provenance was not derived");
if (model.hypothesisRounds[0]?.roundId !== "HYPOTHESIS-ROUND-001") throw new Error("hypothesis round history was not rendered");
if (model.researchSynthesis.literatureCoverage?.hardCapReached !== true) throw new Error("coverage hard-cap state was not rendered");
if (model.evidence[0]?.stance !== "conflict") throw new Error("contradicting evidence should remain a stance, not a node failure");

console.log("Research Map fixture passed");
