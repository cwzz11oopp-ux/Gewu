# Local Literature and Research Wiki Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add safe local literature upload, verification-aware evidence handling, and a persistent Research Wiki that participates in literature search without blocking on an empty or degraded knowledge base.

**Architecture:** `LiteratureLibrary` owns uploaded files and metadata, `ResearchWikiStore` owns long-term graph state, and `KnowledgeIntegrationService` merges Wiki, local, and external candidates. ResearchAgent proposes evidence and Wiki changes; Supervisor validates and commits them.

**Tech Stack:** Python 3.12, FastAPI multipart uploads, Pydantic 2, pypdf, atomic JSON storage, React 19, TypeScript, lucide-react, pytest, Node test runner.

## Global Constraints

- This plan depends on `2026-07-14-supervisor-skill-runtime.md` being complete.
- Empty Wiki and no-match queries return `WIKI_EMPTY` and continue external search.
- Degraded Wiki returns `WIKI_DEGRADED` and continues external search.
- Local upload is not equivalent to verified citation status.
- Only externally verified DOI/arXiv records are exportable competition references.
- ResearchAgent cannot directly commit Wiki mutations.
- First release supports PDF, Markdown, and TXT; no OCR and no vector database.
- Default upload limit is 30 MB.
- Uploaded user data remains ignored by Git.

---

## File Structure

Create:

- `backend/app/models/literature.py`: LocalDocument, verification, query, and upload response models.
- `backend/app/storage/literature.py`: file storage, parsing, indexing, and deterministic local search.
- `backend/app/storage/research_wiki.py`: Wiki initialization, query, ingest, graph, query pack, and audit log.
- `backend/app/workflow/knowledge.py`: Wiki/local/external merge and Wiki change proposals.
- `backend/app/api/literature.py`: multipart and library APIs.
- `tests/backend/test_literature_library.py`: storage/parser tests.
- `tests/backend/test_research_wiki.py`: Wiki empty/degraded/ingest tests.
- `tests/backend/test_knowledge_integration.py`: three-source merge tests.
- `frontend/src/components/LiteratureUploadDialog.tsx`: upload and metadata dialog.

Modify:

- `backend/requirements.txt`: add `pypdf` pin.
- `backend/app/models/provider.py`: evidence provenance and local verification fields.
- `backend/app/providers/literature.py`: identifier verification API.
- `backend/app/storage/repository.py`: attach document and save evidence versions.
- `backend/app/workflow/engine.py`: call KnowledgeIntegrationService and Supervisor commit gate.
- `backend/app/main.py`: construct and inject library, Wiki, and knowledge service.
- `backend/app/api/runs.py`: attach uploaded document to a Run.
- `frontend/src/api/types.ts`: LocalDocument and source status types.
- `frontend/src/api/client.ts`: multipart-safe request and literature APIs.
- `frontend/src/components/EvidenceTable.tsx`: upload action, source, and verification status.
- `frontend/src/pages/WorkbenchPage.tsx`: literature callbacks.
- `frontend/src/App.tsx`: upload/attach orchestration and refresh.
- `frontend/src/styles.css`: compact dialog and status styling.
- `frontend/tests/ui-contract.test.mjs`: upload UI contracts.
- `tests/backend/test_api.py`: literature endpoints.
- `tests/backend/test_workflow_engine.py`: Wiki/local/external workflow behavior.

### Task 1: Define LocalDocument and Evidence Provenance Contracts

**Files:**
- Create: `backend/app/models/literature.py`
- Modify: `backend/app/models/provider.py`
- Create: `tests/backend/test_literature_library.py`

**Interfaces:**
- Produces: `LocalDocument`, `DocumentVerification`, `DocumentStatuses`.
- Produces: `EvidenceCard.source_kind`, `local_document_id`, and `exportable` behavior.

- [ ] **Step 1: Write failing model tests**

```python
def test_local_document_is_not_verified_by_upload_alone():
    document = LocalDocument(
        id="paper_ab12",
        filename="paper.pdf",
        media_type="application/pdf",
        sha256="a" * 64,
        size_bytes=100,
        title="Local paper",
    )
    assert document.statuses == ["uploaded"]
    assert document.verification.verified is False


def test_local_evidence_requires_external_verification_to_export():
    card = EvidenceCard(
        title="Local paper",
        authors=[],
        year=2026,
        source="local_upload",
        source_kind="local",
        local_document_id="paper_ab12",
        claim="",
        url="",
    )
    assert card.exportable is False
```

- [ ] **Step 2: Run tests and verify red state**

Run: `.\.venv\Scripts\python.exe -m pytest tests/backend/test_literature_library.py -q`

Expected: FAIL because the models and fields do not exist.

- [ ] **Step 3: Implement models**

```python
class DocumentVerification(BaseModel):
    verified: bool = False
    provider: str = ""
    verified_at: str | None = None


class LocalDocument(BaseModel):
    id: str
    filename: str
    media_type: str
    sha256: str
    size_bytes: int
    title: str = ""
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    abstract: str = ""
    identifiers: dict[str, str] = Field(default_factory=dict)
    source: str = "local_upload"
    statuses: list[str] = Field(default_factory=lambda: ["uploaded"])
    verification: DocumentVerification = Field(default_factory=DocumentVerification)
    wiki_node_id: str | None = None
    run_ids: list[str] = Field(default_factory=list)
```

Add `source_kind: Literal["external", "local", "wiki"] = "external"` and `local_document_id: str | None = None` to EvidenceCard. Keep exportable dependent on `verified` and an identifier or URL.

- [ ] **Step 4: Run model tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/backend/test_literature_library.py -q`

Expected: PASS.

- [ ] **Step 5: Commit models**

```powershell
git add backend/app/models/literature.py backend/app/models/provider.py tests/backend/test_literature_library.py
git commit -m "feat: define local literature provenance"
```

### Task 2: Implement Safe Upload Storage, Parsing, and Deduplication

**Files:**
- Create: `backend/app/storage/literature.py`
- Modify: `backend/requirements.txt`
- Modify: `tests/backend/test_literature_library.py`

**Interfaces:**
- Produces: `LiteratureLibrary.upload(stream, filename, media_type, metadata) -> LocalDocument`.
- Produces: `list_documents()`, `get(document_id)`, `delete(document_id)`, `search(query, limit)`.

- [ ] **Step 1: Add failing upload tests**

```python
def test_upload_text_document_persists_hash_text_and_index(tmp_path):
    library = LiteratureLibrary(tmp_path, max_upload_bytes=1024)
    document = library.upload(
        io.BytesIO(b"dropout improves robustness"),
        "paper.txt",
        "text/plain",
        {"title": "Dropout Study"},
    )
    assert document.id.startswith("paper_")
    assert document.statuses == ["uploaded", "parsed", "metadata_ready"]
    assert library.get(document.id).sha256 == document.sha256
    assert library.text_path(document.id).read_text(encoding="utf-8") == "dropout improves robustness"
```

Add tests for duplicate SHA-256, unsupported content, 30 MB limit using a smaller configured limit, Markdown parsing, PDF parsing from a small fixture, and empty extracted PDF text.

- [ ] **Step 2: Verify red state**

Run: `.\.venv\Scripts\python.exe -m pytest tests/backend/test_literature_library.py -q`

Expected: FAIL because LiteratureLibrary does not exist.

- [ ] **Step 3: Add pypdf and implement atomic index storage**

Add `pypdf==6.14.2` to `backend/requirements.txt` and install it in the current environment before running tests.

Use `index.json` plus temporary-file replacement. Generate IDs from `sha256[:12]`:

```python
document_id = f"paper_{digest[:12]}"
```

Read the stream in chunks and stop once `max_upload_bytes` is exceeded. Store files using the generated ID, never the client filename.

- [ ] **Step 4: Implement deterministic search**

Tokenize normalized query terms and score title as 4, abstract as 2, and extracted text as 1 per matching unique term. Sort by descending score then document ID. Return no results for an empty query.

- [ ] **Step 5: Run storage tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/backend/test_literature_library.py -q`

Expected: PASS.

- [ ] **Step 6: Commit storage and dependency**

```powershell
git add backend/requirements.txt backend/app/storage/literature.py tests/backend/test_literature_library.py
git commit -m "feat: store and parse local literature"
```

### Task 3: Expose Literature APIs

**Files:**
- Create: `backend/app/api/literature.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/api/runs.py`
- Modify: `backend/app/storage/repository.py`
- Modify: `backend/app/providers/literature.py`
- Modify: `tests/backend/test_api.py`
- Modify: `tests/backend/test_literature_provider.py`

**Interfaces:**
- Produces: document create/list/get/delete and `/api/literature/documents/{paper_id}/verify`; the Supervisor-gated Wiki endpoint follows in Task 5.
- Produces: `Repository.attach_local_document(run_id, document) -> Artifact`.

- [ ] **Step 1: Write failing API tests**

```python
def test_upload_and_list_local_literature(tmp_path):
    client = TestClient(create_app(data_dir=str(tmp_path / "data"), env=DEV_ENV))
    response = client.post(
        "/api/literature/documents",
        files={"file": ("paper.txt", b"robust training", "text/plain")},
        data={"title": "Robust Training"},
    )
    assert response.status_code == 200
    document = response.json()
    assert document["source"] == "local_upload"
    assert client.get("/api/literature/documents").json()[0]["id"] == document["id"]
```

Add tests for get, delete, duplicate 409 response, attach to Run, and not-found 404.

Add identifier-verification coverage with an injected fake external verifier:

```python
response = client.post(f"/api/literature/documents/{paper_id}/verify")
assert response.status_code == 200
assert response.json()["verification"]["verified"] is True
assert response.json()["verification"]["provider"] == "fake_external"
```

When neither DOI nor arXiv is present, assert HTTP 422 with stable code `LITERATURE_REFERENCE_UNVERIFIED`; upload status alone must never pass verification.

- [ ] **Step 2: Verify red state**

Run: `.\.venv\Scripts\python.exe -m pytest tests/backend/test_api.py -q`

Expected: FAIL with 404 routes.

- [ ] **Step 3: Implement multipart endpoints**

Use `UploadFile`, `File`, and `Form`. Convert stable library exceptions to structured HTTP details:

```python
raise HTTPException(
    status_code=409,
    detail={"code": "LITERATURE_DUPLICATE", "document_id": existing_id},
)
```

Do not use the JSON request helper for file uploads.

- [ ] **Step 4: Implement explicit identifier verification**

The verify endpoint calls `LiteratureProvider.verify_identifier()` with DOI first and arXiv second, persists returned canonical metadata and `verified_at`, and records the provider. Network/provider failure returns `LITERATURE_VERIFICATION_FAILED` without changing the previous verification state. A local metadata match without an external provider response remains unverified.

- [ ] **Step 5: Implement Run attachment**

`attach_local_document` creates a new `evidence` Artifact version containing the previous references plus a local EvidenceCard. It sets `verified` from the document verification record, not from upload status, and adds the Run ID to the document index.

- [ ] **Step 6: Run API, repository, and provider tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/backend/test_api.py tests/backend/test_repository.py tests/backend/test_literature_provider.py -q`

Expected: PASS.

- [ ] **Step 7: Commit APIs**

```powershell
git add backend/app/api/literature.py backend/app/main.py backend/app/api/runs.py backend/app/storage/repository.py backend/app/providers/literature.py tests/backend/test_api.py tests/backend/test_repository.py tests/backend/test_literature_provider.py
git commit -m "feat: expose local literature APIs"
```

### Task 4: Implement ResearchWikiStore Empty and Degraded Behavior

**Files:**
- Create: `backend/app/storage/research_wiki.py`
- Create: `tests/backend/test_research_wiki.py`

**Interfaces:**
- Produces: `ResearchWikiStore.query(topic: str) -> WikiQueryResult`.
- Produces: `initialize()`, `ingest_papers()`, `commit_changes()`, and `stats()`.

- [ ] **Step 1: Write failing empty/degraded tests**

```python
def test_missing_wiki_initializes_and_returns_empty(tmp_path):
    wiki = ResearchWikiStore(tmp_path / "research-wiki")
    result = wiki.query("dropout robustness")
    assert result.status == "empty"
    assert result.papers == []
    assert result.warnings == ["WIKI_EMPTY"]
    assert (tmp_path / "research-wiki" / "graph" / "edges.jsonl").is_file()


def test_corrupt_edges_degrades_without_raising(tmp_path):
    wiki = ResearchWikiStore(tmp_path / "research-wiki")
    wiki.initialize()
    wiki.edges_path.write_text("{bad json}\n", encoding="utf-8")
    result = wiki.query("dropout")
    assert result.status == "degraded"
    assert "WIKI_DEGRADED" in result.warnings
```

- [ ] **Step 2: Verify red state**

Run: `.\.venv\Scripts\python.exe -m pytest tests/backend/test_research_wiki.py -q`

Expected: FAIL with module import error.

- [ ] **Step 3: Implement deterministic initialization and query**

Create `index.md`, `log.md`, `gap_map.md`, `query_pack.md`, `papers/`, `ideas/`, `experiments/`, `claims/`, and `graph/edges.jsonl`. Query reads structured frontmatter from paper pages, performs the same deterministic token match as local search, and never invokes an LLM.

- [ ] **Step 4: Implement query pack budget**

Build sections in fixed order and truncate only at section item boundaries. Assert UTF-8 text length is at most 8000 characters.

- [ ] **Step 5: Run Wiki query tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/backend/test_research_wiki.py -q`

Expected: PASS.

- [ ] **Step 6: Commit Wiki read path**

```powershell
git add backend/app/storage/research_wiki.py tests/backend/test_research_wiki.py
git commit -m "feat: query empty and degraded research wiki"
```

### Task 5: Implement Supervisor-Gated Wiki Ingest

**Files:**
- Modify: `backend/app/storage/research_wiki.py`
- Modify: `backend/app/agents/supervisor.py`
- Modify: `backend/app/api/literature.py`
- Modify: `tests/backend/test_research_wiki.py`
- Modify: `tests/backend/test_supervisor_agent.py`
- Modify: `tests/backend/test_api.py`

**Interfaces:**
- Produces: `WikiChangeSet(papers, gaps, edges, origin_run_id)`.
- Produces: `SupervisorAgent.commit_wiki_changes(change_set, wiki) -> WikiCommitResult`.
- Produces: `POST /api/literature/documents/{paper_id}/wiki` routed through Supervisor.

- [ ] **Step 1: Write failing ingest and permission tests**

```python
def test_supervisor_ingests_verified_paper_and_rebuilds_query_pack(supervisor, wiki):
    changes = WikiChangeSet(
        papers=[verified_paper("1512.03385")],
        gaps=[{"id": "gap:G1", "text": "variance under fixed budget"}],
        edges=[{"source": "paper:1512-03385", "target": "gap:G1", "type": "addresses_gap"}],
        origin_run_id="run_1",
    )
    result = supervisor.commit_wiki_changes(changes, wiki)
    assert result.paper_count == 1
    assert "paper:1512-03385" in wiki.query("variance").paper_ids
```

Add a test that calling `wiki.commit_changes(..., actor="research")` raises `WIKI_COMMIT_REJECTED`.

Add API coverage that a local unverified document can be added to Wiki for retrieval, but its node remains `verified=false` and is not exportable. Assert the endpoint calls `SupervisorAgent.commit_wiki_changes()` rather than writing the Wiki store directly.

- [ ] **Step 2: Verify red state**

Run: `.\.venv\Scripts\python.exe -m pytest tests/backend/test_research_wiki.py tests/backend/test_supervisor_agent.py -q`

Expected: FAIL because change and commit APIs do not exist.

- [ ] **Step 3: Implement canonical nodes and edges**

Use canonical IDs `paper:<slug>`, `gap:<id>`, `idea:<id>`, `exp:<id>`, and `claim:<id>`. Deduplicate paper writes by DOI, arXiv, then normalized title. `graph/edges.jsonl` is the only relationship source of truth.

- [ ] **Step 4: Enforce Supervisor actor and audit log**

Every mutation appends an ISO timestamp, actor, origin Run ID, operation, and affected node IDs to `log.md`. Rebuild `index.md` and `query_pack.md` after the atomic commit succeeds.

- [ ] **Step 5: Expose the Supervisor-gated Wiki endpoint**

Convert the selected `LocalDocument` into a provenance-preserving Wiki paper node and submit a `WikiChangeSet` to Supervisor. Preserve `verification.verified=false` for uploaded-only documents; adding to Wiki changes retrieval status, not citation verification.

- [ ] **Step 6: Run ingest tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/backend/test_research_wiki.py tests/backend/test_supervisor_agent.py tests/backend/test_api.py -q`

Expected: PASS.

- [ ] **Step 7: Commit Wiki mutation path**

```powershell
git add backend/app/storage/research_wiki.py backend/app/agents/supervisor.py backend/app/api/literature.py tests/backend/test_research_wiki.py tests/backend/test_supervisor_agent.py tests/backend/test_api.py
git commit -m "feat: gate research wiki updates through supervisor"
```

### Task 6: Merge Wiki, Local, and External Literature

**Files:**
- Create: `backend/app/workflow/knowledge.py`
- Modify: `backend/app/providers/literature.py`
- Modify: `backend/app/workflow/engine.py`
- Modify: `backend/app/main.py`
- Create: `tests/backend/test_knowledge_integration.py`
- Modify: `tests/backend/test_workflow_engine.py`

**Interfaces:**
- Produces: `KnowledgeIntegrationService.collect(run_id, problem) -> KnowledgeIntegrationResult`.
- Produces: `KnowledgeIntegrationResult.references`, `local_only`, `warnings`, and `wiki_changes`.

- [ ] **Step 1: Write failing three-source merge tests**

```python
def test_collect_continues_external_search_when_wiki_is_empty(service, external_provider):
    result = service.collect("run_1", {"literature_queries": ["dropout robustness"]})
    assert external_provider.queries == ["dropout robustness"]
    assert "WIKI_EMPTY" in result.warnings
    assert result.references
```

Add DOI/arXiv/title dedup tests, local-unverified separation, maximum 12 proposed Wiki papers, and all-problem-query coverage.

Record source calls and assert each query is processed in the order `wiki`, `local`, `external`. `WIKI_EMPTY`, no Wiki match, and `WIKI_DEGRADED` must all leave the external call in place.

- [ ] **Step 2: Verify red state**

Run: `.\.venv\Scripts\python.exe -m pytest tests/backend/test_knowledge_integration.py -q`

Expected: FAIL with module import error.

- [ ] **Step 3: Implement merge keys and ranking**

For each normalized problem query, call `ResearchWikiStore.query()` first, `LiteratureLibrary.search()` second, and the external `LiteratureProvider.search()` third. Source failures become stable warnings and do not erase candidates already returned by another source.

Use this key priority:

```python
def evidence_key(card: EvidenceCard) -> str:
    if card.identifiers.get("doi"):
        return f"doi:{card.identifiers['doi'].lower()}"
    if card.identifiers.get("arxiv"):
        return f"arxiv:{card.identifiers['arxiv'].lower()}"
    return f"title:{normalize_title(card.title)}"
```

Prefer verified external metadata when duplicate local/Wiki cards refer to the same paper, while preserving local document linkage as provenance.

- [ ] **Step 4: Replace the engine's single `search(limit=5)` call**

Save evidence content with `references`, `local_only`, `warnings`, and `sources`. Call `SupervisorAgent.validate()` before saving, then commit `wiki_changes` after the Artifact exists.

- [ ] **Step 5: Run workflow tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/backend/test_knowledge_integration.py tests/backend/test_workflow_engine.py tests/backend/test_literature_provider.py -q`

Expected: PASS.

- [ ] **Step 6: Commit knowledge integration**

```powershell
git add backend/app/workflow/knowledge.py backend/app/providers/literature.py backend/app/workflow/engine.py backend/app/main.py tests/backend/test_knowledge_integration.py tests/backend/test_workflow_engine.py tests/backend/test_literature_provider.py
git commit -m "feat: merge wiki local and external evidence"
```

### Task 7: Add Frontend Upload and Verification UI

**Files:**
- Create: `frontend/src/components/LiteratureUploadDialog.tsx`
- Modify: `frontend/src/api/types.ts`
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/components/EvidenceTable.tsx`
- Modify: `frontend/src/pages/WorkbenchPage.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/styles.css`
- Modify: `frontend/tests/ui-contract.test.mjs`

**Interfaces:**
- Produces: `api.uploadLiterature(file, metadata)` using FormData.
- Produces: upload dialog callbacks `onUploaded(document)` and `onAttach(documentId)`.

- [ ] **Step 1: Write failing frontend contract tests**

```javascript
test("literature upload uses FormData without forcing JSON content type", async () => {
  const client = await readSource("src/api/client.ts");
  assert.match(client, /new FormData\(\)/);
  assert.match(client, /uploadLiterature/);
  assert.match(client, /requestForm/);
});
```

Add assertions for lucide `Upload`, local/Wiki/external labels, and verified status display.

- [ ] **Step 2: Verify red state**

Run from `frontend/`: `node --test tests/ui-contract.test.mjs`

Expected: FAIL because upload UI is absent.

- [ ] **Step 3: Implement multipart-safe API helper**

```typescript
async function requestForm<T>(path: string, form: FormData): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, { method: "POST", body: form });
  if (!response.ok) throw await apiError(response);
  return response.json() as Promise<T>;
}
```

Do not set `Content-Type`; the browser supplies the multipart boundary.

- [ ] **Step 4: Implement the compact upload dialog**

Use a lucide Upload icon button with tooltip. Include file, title, authors, year, DOI, arXiv, “加入当前研究”, and “加入 Wiki” controls. Show progress and stable backend error messages.

- [ ] **Step 5: Render evidence provenance accurately**

Count only `verified === true` references in the verified total. Render separate badges for local, Wiki, and external sources; do not label an uploaded-only paper as verified.

- [ ] **Step 6: Run frontend tests and build**

Run from `frontend/`: `node --test tests/ui-contract.test.mjs`

Expected: PASS.

Run from `frontend/`: `node --test tests/presentation.test.ts`

Expected: PASS.

Run from `frontend/`: `pnpm run build`

Expected: TypeScript and Vite build PASS.

- [ ] **Step 7: Commit frontend literature UI**

```powershell
git add frontend/src frontend/tests/ui-contract.test.mjs
git commit -m "feat: upload and classify local literature"
```

### Task 8: Run Full Literature/Wiki Regression

**Files:**
- Modify: `README.md`
- Modify: `docs/runbook.md`

**Interfaces:**
- Consumes: all literature and Wiki APIs.
- Produces: user-facing setup and operation instructions.

- [ ] **Step 1: Add documentation contract assertions**

Require README/runbook to mention supported file types, unverified local status, empty Wiki fallback, and Git-ignored user data.

- [ ] **Step 2: Update README and runbook**

Document upload, attach, verify, and Wiki operations. Include exact data paths and error meanings.

- [ ] **Step 3: Run backend regression**

Run: `.\.venv\Scripts\python.exe -m pytest tests/backend -q`

Expected: all backend tests PASS.

- [ ] **Step 4: Run frontend regression**

Run from `frontend/`: `node --test tests/ui-contract.test.mjs`

Expected: PASS.

Run from `frontend/`: `node --test tests/presentation.test.ts`

Expected: PASS.

Run from `frontend/`: `pnpm run build`

Expected: all tests and build PASS.

- [ ] **Step 5: Commit documentation**

```powershell
git add README.md docs/runbook.md
git commit -m "docs: explain local literature and research wiki"
```
