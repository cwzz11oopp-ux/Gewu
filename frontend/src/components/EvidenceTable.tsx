import { useEffect, useState } from "react";
import { BookPlus, Link, ShieldCheck, Trash2, Upload } from "lucide-react";
import type { Artifact, LocalLiteratureDocument } from "../api/types";
import { api } from "../api/client";
import { findLatestArtifact, formatAuthors, formatReferenceIdentifier, formatReferenceTitle } from "../utils/presentation";

type Props = {
  artifacts: Artifact[];
  runId?: string;
  onRunRefresh: () => Promise<void>;
};

export function EvidenceTable({ artifacts, runId, onRunRefresh }: Props) {
  const [documents, setDocuments] = useState<LocalLiteratureDocument[]>([]);
  const [libraryMessage, setLibraryMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [uploadMetadata, setUploadMetadata] = useState({
    title: "",
    authors: "",
    year: "",
    abstract: "",
    doi: "",
    arxiv: "",
  });
  const evidence = findLatestArtifact(artifacts, "evidence");
  const references = Array.isArray(evidence?.content.references)
    ? evidence.content.references as Array<Record<string, unknown>>
    : [];
  const sources = evidence?.content.sources && typeof evidence.content.sources === "object"
    ? evidence.content.sources as Record<string, unknown>
    : {};
  const searchCalls = Array.isArray(sources.calls)
    ? sources.calls.filter((call): call is Record<string, unknown> => Boolean(call && typeof call === "object"))
    : [];
  const warnings = Array.isArray(evidence?.content.warnings)
    ? evidence.content.warnings.map(String)
    : [];
  const retrievedCount = [sources.wiki, sources.local, sources.external]
    .filter((count): count is number => typeof count === "number")
    .reduce((total, count) => total + count, 0);
  const literatureStatus = references.length === 0
    ? "未检索到可用参考文献"
    : warnings.length
      ? "部分来源未返回结果"
      : "检索完成";

  useEffect(() => {
    api.listLiterature().then(setDocuments).catch((error) => setLibraryMessage(error.message));
  }, []);

  async function refreshLibrary() {
    setDocuments(await api.listLiterature());
  }

  async function uploadLiterature(file?: File) {
    if (!file) return;
    setBusy(true);
    try {
      await api.uploadLiterature(file, uploadMetadata);
      await refreshLibrary();
      setLibraryMessage(`已上传 ${file.name}`);
      setUploadMetadata({ title: "", authors: "", year: "", abstract: "", doi: "", arxiv: "" });
    } catch (error) {
      setLibraryMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  async function attachLiterature(document: LocalLiteratureDocument) {
    if (!runId) return;
    setBusy(true);
    try {
      await api.attachLiterature(runId, document.id);
      await Promise.all([refreshLibrary(), onRunRefresh()]);
      setLibraryMessage(`已关联 ${document.title || document.filename}`);
    } catch (error) {
      setLibraryMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  async function verifyLiterature(document: LocalLiteratureDocument) {
    setBusy(true);
    try {
      await api.verifyLiterature(document.id);
      await refreshLibrary();
      setLibraryMessage(`已验证 ${document.title || document.filename}`);
    } catch (error) {
      setLibraryMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  async function addLiteratureToWiki(document: LocalLiteratureDocument) {
    setBusy(true);
    try {
      await api.addLiteratureToWiki(document.id);
      await refreshLibrary();
      setLibraryMessage(`已加入 Wiki: ${document.title || document.filename}`);
    } catch (error) {
      setLibraryMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  async function deleteLiterature(document: LocalLiteratureDocument) {
    setBusy(true);
    try {
      await api.deleteLiterature(document.id);
      await refreshLibrary();
      setLibraryMessage(`已删除 ${document.title || document.filename}`);
    } catch (error) {
      setLibraryMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel section-card literature-card">
      <div className="section-title"><span>B</span><h2>文献检索与验证</h2></div>
      <div className="local-library-toolbar">
        <strong>本地文献库</strong>
        <label className={`secondary-button literature-upload-button ${busy ? "disabled" : ""}`}>
          <Upload size={15} /> 上传文献
          <input
            className="visually-hidden"
            type="file"
            accept=".pdf,.txt,.md"
            disabled={busy}
            onChange={(event) => {
              void uploadLiterature(event.target.files?.[0]);
              event.currentTarget.value = "";
            }}
          />
        </label>
      </div>
      <div className="local-library-metadata">
        <input
          name="literature-title"
          aria-label="文献标题"
          placeholder="标题（可选）"
          value={uploadMetadata.title}
          onChange={(event) => setUploadMetadata({ ...uploadMetadata, title: event.target.value })}
        />
        <input
          name="literature-authors"
          aria-label="文献作者"
          placeholder="作者，逗号分隔"
          value={uploadMetadata.authors}
          onChange={(event) => setUploadMetadata({ ...uploadMetadata, authors: event.target.value })}
        />
        <input
          name="literature-year"
          aria-label="发表年份"
          placeholder="年份"
          inputMode="numeric"
          value={uploadMetadata.year}
          onChange={(event) => setUploadMetadata({ ...uploadMetadata, year: event.target.value })}
        />
        <input
          name="literature-doi"
          aria-label="DOI"
          placeholder="DOI"
          value={uploadMetadata.doi}
          onChange={(event) => setUploadMetadata({ ...uploadMetadata, doi: event.target.value })}
        />
        <input
          name="literature-arxiv"
          aria-label="arXiv 标识"
          placeholder="arXiv ID"
          value={uploadMetadata.arxiv}
          onChange={(event) => setUploadMetadata({ ...uploadMetadata, arxiv: event.target.value })}
        />
        <input
          name="literature-abstract"
          aria-label="文献摘要"
          placeholder="摘要（可选）"
          value={uploadMetadata.abstract}
          onChange={(event) => setUploadMetadata({ ...uploadMetadata, abstract: event.target.value })}
        />
      </div>
      {libraryMessage ? <p className="inline-note local-library-message">{libraryMessage}</p> : null}
      <div className="local-library-list">
        {documents.length ? documents.map((document) => {
          const attached = Boolean(runId && document.run_ids.includes(runId));
          const hasIdentifier = Boolean(document.identifiers.doi || document.identifiers.arxiv);
          return (
            <div className="local-library-row" key={document.id}>
              <div>
                <strong title={document.title || document.filename}>{document.title || document.filename}</strong>
                <small>
                  {document.verification.verified ? "已验证" : "本地未验证"}
                  {document.wiki_node_id ? " · Wiki 已收录" : ""}
                  {attached ? " · 已关联当前研究" : ""}
                </small>
              </div>
              <div className="local-library-actions">
                <button
                  className="icon-button"
                  title="验证 DOI 或 arXiv 标识"
                  aria-label="验证文献"
                  disabled={busy || !hasIdentifier || document.verification.verified}
                  onClick={() => void verifyLiterature(document)}
                ><ShieldCheck size={16} /></button>
                <button
                  className="icon-button"
                  title="关联当前研究"
                  aria-label="关联当前研究"
                  disabled={busy || !runId || attached}
                  onClick={() => void attachLiterature(document)}
                ><Link size={16} /></button>
                <button
                  className="icon-button"
                  title="加入 Research Wiki"
                  aria-label="加入 Research Wiki"
                  disabled={busy || Boolean(document.wiki_node_id)}
                  onClick={() => void addLiteratureToWiki(document)}
                ><BookPlus size={16} /></button>
                <button
                  className="icon-button danger-icon-button"
                  title="删除本地文献"
                  aria-label="删除本地文献"
                  disabled={busy}
                  onClick={() => void deleteLiterature(document)}
                ><Trash2 size={16} /></button>
              </div>
            </div>
          );
        }) : <p className="muted-copy">尚未上传本地文献</p>}
      </div>
      <div className="toggle-row"><span className="toggle on" /> 已检索文献 <strong>{retrievedCount} 篇</strong></div>
      <p className="muted-copy">可用参考文献 {references.length} 篇 · {literatureStatus}</p>
      {references.length ? <p className="inline-note">{references.slice(0, 2).map((record) => formatReferenceTitle(record.title)).join("；")}</p> : null}
      <button className="secondary-button" onClick={() => setDetailsOpen((open) => !open)}>
        {detailsOpen ? "收起文献" : "查看全部文献"}
      </button>
      {detailsOpen ? <>
      {searchCalls.length ? <ul className="inline-note">
        {searchCalls.map((call, index) => <li key={`${call.source}-${call.query}-${index}`}>{String(call.source ?? "来源")}：{String(call.query ?? "")}</li>)}
      </ul> : null}
      {warnings.length ? <p className="inline-note">检索状态：{warnings.join("；")}</p> : null}
      <table className="data-table literature-table">
        <colgroup><col className="paper-title-column" /><col className="paper-author-column" /><col className="paper-id-column" /><col className="paper-source-column" /></colgroup>
        <thead><tr><th>论文标题</th><th>作者</th><th>DOI / arXiv</th><th>来源</th></tr></thead>
        <tbody>
          {references.length ? references.map((record, index) => {
            const title = formatReferenceTitle(record.title);
            const authors = formatAuthors(record.authors);
            const identifier = formatReferenceIdentifier(record);
            const source = record.source_kind === "local"
              ? "本地"
              : record.source_kind === "wiki"
                ? "Wiki"
                : "外部";
            return (
              <tr key={`${title}-${index}`}>
                <td data-label="论文标题"><span className="paper-title" title={title}>{title}</span></td>
                <td data-label="作者"><span className="paper-authors" title={authors}>{authors}</span></td>
                <td data-label="DOI / arXiv"><span className="paper-identifier" title={identifier}>{identifier}</span></td>
                <td data-label="来源"><span className="evidence-source">{source}</span></td>
              </tr>
            );
          }) : <tr><td colSpan={4}>等待文献检索或关联本地文献</td></tr>}
        </tbody>
      </table>
      </> : null}
    </section>
  );
}
