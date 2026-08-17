# Round 6 Research Synthesis & Provenance 重构报告

## 完成范围

本轮完成了 Literature → Research Synthesis → Theme → Research Gap → Hypothesis → Evidence Validation → Selection / Revision 的代码与界面重构。

未运行真实研究问题、未调用真实检索、未创建正式 Run、未修改 `run_9e7d3cd0c97f`、未修改其 Artifact、未创建 Git commit。审查结束时该 Run 仍为：`failed / evidence_reasoning / updated_at=2026-08-15T23:03:30.656281+08:00 / 14 artifacts`。工作目录本身不是 Git repository，因此也不存在可创建的 commit。

## 修改文件

- `backend/app/workflow/research_synthesis.py`（新增）
- `backend/app/workflow/engine.py`
- `backend/app/agents/idea.py`
- `backend/app/workflow/orchestrator.py`
- `backend/app/workflow/research_state.py`
- `frontend/src/components/researchViewModel.ts`
- `frontend/src/components/workspace/ResearchPage.tsx`
- `frontend/src/components/workspace/IdeaPage.tsx`
- `tests/backend/test_research_synthesis.py`（新增）
- `tests/backend/test_workflow_engine.py`
- `frontend/scripts/verify-research-map.ts`（新增 synthetic fixture）

## 1. Research Synthesis 数据结构

`knowledge_integration` 现在在持久化 `evidence` 后新增 `research_synthesis` Artifact。正常路径使用全部 `result.references`（完整 verified/exportable collection），而不是前 5 篇或核心集；仅在旧/mock provider 根本没有完整 references 时，以其完整 `core_references` 作兼容回退。

Schema（`schema_version=1`）包括：

```json
{
  "source_collection": {
    "paper_count": 0,
    "selection_policy": "all_verified_references",
    "source_paper_ids": []
  },
  "papers": [],
  "claims": [],
  "themes": [],
  "established_findings": [],
  "conflicting_findings": [],
  "limitations": [],
  "future_work": [],
  "research_gaps": []
}
```

每个 paper 使用稳定 `PAPER-*`；每个源句使用稳定 `CLAIM-*`，并保存 `locator`（`abstract.sentence.N` 或 `title`）、title 与 URL。Synthesis 是完整 collection 的结构化、可追溯归纳；用于 prompt 的是带 source collection coverage 的 bounded structural summary，而非任意 `[:5]` 的文献列表。

## 2. Future Work → Gap

`future_work` 是独立记录，识别 Future Work / Future Research / Outlook / Further Work 等作者明确方向；`limitations` 独立记录。两者生成的每个 Gap 都包含：

```json
{
  "gap_id": "GAP-xxx",
  "title": "...",
  "description": "...",
  "gap_type": "synthesized | author_proposed | mixed",
  "source_paper_ids": [],
  "source_claim_ids": [],
  "source_future_work_ids": [],
  "confidence": 0.0
}
```

这构成真实多对多关系：Theme 保存多个 source paper / claim，Gap 保存实际 limitation/future-work claim 的 ID，不采用数组位置或代表论文伪连线。

## 3. Gap → Hypothesis provenance

`IdeaAgent.generate` 的输入现在为：Research Question、`research_synthesis` 的结构化上下文、Themes、Research Gaps，以及按 Theme 选择的少量代表性 literature cards（仅补充上下文）。它不再把 top-5 cards 当作主要科研依据。

Hypothesis schema 现在要求：

```json
{
  "candidate_id": "CAND-xxx",
  "source_gap_ids": ["GAP-..."],
  "source_paper_ids": ["PAPER-..."],
  "source_claim_ids": ["CLAIM-..."],
  "reasoning_summary": "..."
}
```

Engine 只接受实际存在于 Research Synthesis 的 `source_gap_ids`，再从对应 Gap 派生 paper/claim IDs。未知 ID 不会以位置、arXiv 字符串或首条 Evidence 修补；候选会明确标记 `provenance_status=unavailable`。因此 arXiv ID 不再伪装成 `EVID-*`。

## 4. Idea Formation 与 Evidence Validation 分离

- **Idea Formation**：paper → source claim / limitation / future work → theme / gap → hypothesis。其 provenance 位于 `research_synthesis` 和 hypothesis 的 `source_*_ids`。
- **Evidence Validation**：hypothesis → candidate-specific targeted retrieval → `EVID-*` → support / contradict / neutral / missing → assessment。其记录仍在 reasoning Artifact / Candidate Evidence Map。

后生成的 `EVID-*` 没有被回写成 hypothesis 的初始生成依据。现有 Validator、candidate lineage、Repair Loop、Deterministic Harness、Experiment Task / Result 和 scientific coverage contracts 未被削弱或移除。

## 5. `NO_SELECTABLE_HYPOTHESIS` 新状态流

旧行为：Evidence Reasoning completed → 0 viable → `ValueError` → orchestrator 宽泛异常 → Run failed。

新行为：

```text
Evidence Reasoning completed
→ all candidate assessments persisted
→ 0 selectable candidates
→ hypothesis_revision_required Artifact
→ Run status = hypothesis_revision_required
→ automatic orchestrator stops normally (no runtime failure)
```

Artifact 中保留 `NO_SELECTABLE_HYPOTHESIS`、4/总候选审核数、空 selectable list、candidate assessments 与下一步 `hypothesis_revision_required`。已有 `rerun-from` / user-hypothesis / selection 接口仍可用于后续生成或选择下一轮候选；本轮没有触发它们。

## 6. Research Map 新结构

默认 Map 已从虚假的 Paper → Evidence → Hypothesis 改为 aggregate flow：

```text
Question → Literature → Themes → Research Gaps → Hypotheses → Evidence Review
```

节点显示集合数量，例如 `Literature · 45 papers`、`Themes · 6 themes`、`Research Gaps · 8 gaps`、`Hypotheses · 4 candidates`、`Evidence Review · 199 evidence`，不会默认铺开几十篇论文或 claim 节点。

点击 Research Gaps 后可选择具体 Gap，查看其 Related papers、claims/limitations、Future Work 与拥有真实 `source_gap_ids` 的 Hypotheses；继续点击 Related papers 才显示具体文献。旧 Run 没有 `research_synthesis` 时正常渲染 aggregate Map，并显示 `Provenance unavailable`。

Hypothesis 状态区分：`candidate`、`selected`、`evidence_insufficient`、`rejected`、`revision_required`、`refuted`、`partial`。`stance=contradict` 仅为矛盾证据，Evidence Review 不会把它显示为节点失败。

## 7. 已删除的 synthetic/index fallback

已删除 Research Tree 中的：

- `papers.slice(0, 5)` / `evidence.slice(0, 5)` 参与边构建；
- `index % visiblePapers.length` 论文补边；
- `visibleEvidence[index]` → hypothesis 补边；
- Evidence 缺失 source ID 时以 `papers[index]` 修补；
- Candidate assessment 缺少 `candidate_index` 时用 `assessments[index]` 修补。

规则现在为：有真实 persisted ID lineage 才展示具体关系；无 lineage 明确显示 `Provenance unavailable`。显示或 preview 的数量不再参与科学关系构造。

## 8. 验证

仅运行 permitted mock/fixture、单元、后端与前端构建验证：

| 命令/范围 | 结果 |
|---|---|
| `pytest tests/backend/test_research_synthesis.py tests/backend/test_workflow_engine.py tests/backend/test_workflow_orchestrator.py -q` | **93 passed** |
| `pytest tests/backend -q --cache-clear` | **525 passed, 2 skipped**（86.48s） |
| `pnpm build` | **通过**（`tsc -b` + Vite production build） |
| `pnpm dlx tsx frontend/scripts/verify-research-map.ts` | **Research Map fixture passed** |
| provenance fallback scan | **未发现** `visiblePapers` / `visibleEvidence` / `buildTree` / `index % ...paper/evidence` / `papers[index]` / `evidence[index]` 残留 |

所有测试使用测试目录、mock provider 或 synthetic fixture；没有执行 Fashion-MNIST 真正 E2E，也没有调用真实文献检索。

## 停止点

Round 6 本项重构完成，未进入下一轮优化。
