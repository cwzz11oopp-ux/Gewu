import { useEffect, useMemo, useRef, useState } from "react";
import {
  BookPlus,
  ChevronDown,
  ChevronUp,
  ExternalLink,
  FileText,
  Globe2,
  Link2,
  Search,
  Trash2,
  Upload,
} from "lucide-react";
import { api, literatureFileUrl } from "../../api/client";
import type { Artifact, LocalLiteratureDocument, ResearchKnowledgeBase, ResearchWikiStats } from "../../api/types";
import type { PaperItem } from "../researchViewModel";

type Props = {
  artifacts: Artifact[];
  papers: PaperItem[];
  runId: string;
  knowledgeBaseId: string;
  busy: boolean;
  onKnowledgeBaseIdChange: (value: string) => void;
  onRunRefresh: () => Promise<void>;
};

type RetrievalRound = {
  id: string;
  label: string;
  gapCount: number;
  queryCount: number;
  retrievedCount: number;
  newCount: number;
  queries: string[];
  sources: { wiki: number; local: number; external: number };
};

const isRecord = (value: unknown): value is Record<string, unknown> => Boolean(value && typeof value === "object" && !Array.isArray(value));
const records = (value: unknown) => Array.isArray(value) ? value.filter(isRecord) : [];
const asNumber = (value: unknown) => typeof value === "number" && Number.isFinite(value) ? value : 0;

function roundSources(content: Record<string, unknown>) {
  const sources = isRecord(content.sources) ? content.sources : {};
  const raw = isRecord(sources.raw_counts) ? sources.raw_counts : sources;
  return {
    wiki: asNumber(raw.wiki),
    local: asNumber(raw.local),
    external: asNumber(raw.external),
  };
}

function roundQueries(content: Record<string, unknown>) {
  const direct = isRecord(content.queries)
    ? Object.values(content.queries).flatMap((value) => Array.isArray(value) ? value.map(String) : [])
    : Array.isArray(content.queries) ? content.queries.map(String) : [];
  const specs = records(content.query_specs).map((item) => String(item.query ?? ""));
  const sources = isRecord(content.sources) ? content.sources : {};
  const calls = records(sources.calls).map((item) => String(item.query ?? ""));
  return Array.from(new Set([...direct, ...specs, ...calls].map((item) => item.trim()).filter(Boolean)));
}

function retrievalRounds(artifacts: Artifact[]): RetrievalRound[] {
  const output: RetrievalRound[] = [];
  const initial = [...artifacts].reverse().find((item) => item.type === "evidence");
  if (initial) {
    const sources = roundSources(initial.content);
    const queries = roundQueries(initial.content);
    output.push({
      id: initial.id,
      label: "初始检索",
      gapCount: queries.length,
      queryCount: queries.length,
      retrievedCount: asNumber((initial.content.sources as Record<string, unknown> | undefined)?.raw_candidate_count) || sources.wiki + sources.local + sources.external,
      newCount: records(initial.content.references).length,
      queries,
      sources,
    });
  }
  let iterationIndex = 0;
  for (const artifact of artifacts) {
    if (artifact.type !== "targeted_retrieval" && artifact.type !== "iteration_evidence") continue;
    const queries = roundQueries(artifact.content);
    const sources = roundSources(artifact.content);
    const targeted = artifact.type === "targeted_retrieval";
    if (!targeted) iterationIndex += 1;
    const rawRound = targeted ? asNumber(artifact.content.round) : iterationIndex;
    const gapCount = targeted && isRecord(artifact.content.queries)
      ? Object.keys(artifact.content.queries).length
      : records(artifact.content.query_specs).length;
    output.push({
      id: artifact.id,
      label: `${targeted ? "候选证据补充" : "实验反馈补充"} · 第 ${rawRound || 1} 轮`,
      gapCount,
      queryCount: queries.length,
      retrievedCount: asNumber((artifact.content.sources as Record<string, unknown> | undefined)?.raw_candidate_count) || sources.wiki + sources.local + sources.external,
      newCount: records(targeted ? artifact.content.new_papers : artifact.content.references).length,
      queries,
      sources,
    });
  }
  return output;
}

export function ResearchLiteraturePanel({
  artifacts,
  papers,
  runId,
  knowledgeBaseId,
  busy,
  onKnowledgeBaseIdChange,
  onRunRefresh,
}: Props) {
  const [open, setOpen] = useState(true);
  const [processOpen, setProcessOpen] = useState(false);
  const [documents, setDocuments] = useState<LocalLiteratureDocument[]>([]);
  const [wikiStats, setWikiStats] = useState<ResearchWikiStats | null>(null);
  const [knowledgeBases, setKnowledgeBases] = useState<ResearchKnowledgeBase[]>([]);
  const [creatingKnowledgeBase, setCreatingKnowledgeBase] = useState(false);
  const [newKnowledgeBaseName, setNewKnowledgeBaseName] = useState("");
  const [message, setMessage] = useState("");
  const [working, setWorking] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const scope = knowledgeBaseId.trim() || "default";
  const rounds = useMemo(() => retrievalRounds(artifacts), [artifacts]);
  const latestRound = rounds[rounds.length - 1];

  async function refresh() {
    const [nextDocuments, nextStats] = await Promise.all([
      api.listLiterature(scope),
      api.getResearchWikiStats(scope),
    ]);
    setDocuments(nextDocuments);
    setWikiStats(nextStats);
  }

  useEffect(() => {
    let cancelled = false;
    Promise.all([api.listLiterature(scope), api.getResearchWikiStats(scope)])
      .then(([nextDocuments, nextStats]) => {
        if (!cancelled) {
          setDocuments(nextDocuments);
          setWikiStats(nextStats);
          setMessage("");
        }
      })
      .catch((error) => { if (!cancelled) setMessage(error instanceof Error ? error.message : String(error)); });
    return () => { cancelled = true; };
  }, [scope]);

  useEffect(() => {
    let cancelled = false;
    api.listResearchKnowledgeBases()
      .then((items) => { if (!cancelled) setKnowledgeBases(items); })
      .catch((error) => { if (!cancelled) setMessage(error instanceof Error ? error.message : String(error)); });
    return () => { cancelled = true; };
  }, [scope]);

  function selectKnowledgeBase(value: string) {
    if (value === "__new__") {
      setCreatingKnowledgeBase(true);
      setNewKnowledgeBaseName("");
      return;
    }
    setCreatingKnowledgeBase(false);
    onKnowledgeBaseIdChange(value);
  }

  function createKnowledgeBase() {
    const next = newKnowledgeBaseName.trim();
    if (!next) {
      setMessage("请输入知识库名称。");
      return;
    }
    onKnowledgeBaseIdChange(next);
    setCreatingKnowledgeBase(false);
    setNewKnowledgeBaseName("");
  }

  async function upload(file?: File) {
    if (!file) return;
    setWorking(true);
    setMessage("");
    try {
      const document = await api.uploadLiterature(file, {
        title: file.name.replace(/\.[^.]+$/, ""),
        authors: "",
        year: "",
        abstract: "",
        doi: "",
        arxiv: "",
      }, scope);
      if (runId) {
        await api.attachLiterature(runId, document.id);
        await onRunRefresh();
      }
      await refresh();
      setMessage(runId ? `已上传并挂接：${file.name}` : `已上传到当前知识库：${file.name}`);
    } catch (error) {
      setMessage(error instanceof Error && error.message === "LITERATURE_DUPLICATE" ? "该文献已经存在，无需重复上传。" : error instanceof Error ? error.message : String(error));
    } finally {
      setWorking(false);
    }
  }

  async function attach(document: LocalLiteratureDocument) {
    if (!runId) return;
    setWorking(true);
    try {
      await api.attachLiterature(runId, document.id);
      await Promise.all([refresh(), onRunRefresh()]);
      setMessage(`已挂接：${document.title || document.filename}`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setWorking(false);
    }
  }

  async function addToWiki(document: LocalLiteratureDocument) {
    setWorking(true);
    try {
      await api.addLiteratureToWiki(document.id, scope);
      await refresh();
      setMessage(`已加入当前 Research Wiki：${document.title || document.filename}`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setWorking(false);
    }
  }

  async function remove(document: LocalLiteratureDocument) {
    if (!window.confirm(`删除本地文献“${document.title || document.filename}”？已写入 Run 的历史证据不会删除。`)) return;
    setWorking(true);
    try {
      await api.deleteLiterature(document.id);
      await refresh();
      setMessage(`已从本地文献库删除：${document.title || document.filename}`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setWorking(false);
    }
  }

  const localTitles = new Set(documents.map((item) => (item.title || item.filename).trim().toLocaleLowerCase()));
  const remotePapers = papers.filter((item) => !item.localDocumentId && !localTitles.has(item.title.trim().toLocaleLowerCase()));

  return <section className="research-literature-panel">
    <header className="literature-panel-header">
      <div className="literature-panel-title">
        <strong>文献来源</strong>
        <label>
          <span>知识库 ·</span>
          <select
            aria-label="研究知识库"
            value={creatingKnowledgeBase ? "__new__" : scope}
            disabled={Boolean(runId) || busy || working}
            onChange={(event) => selectKnowledgeBase(event.target.value)}
            title={runId ? "Run 创建后知识库保持固定" : "选择已有知识库，或新建独立知识库"}
          >
            {!knowledgeBases.some((item) => item.knowledge_base_id === scope) ? <option value={scope}>{scope}</option> : null}
            {knowledgeBases.map((item) => <option key={item.knowledge_base_id} value={item.knowledge_base_id}>{item.knowledge_base_id}（{item.papers} 篇）</option>)}
            {!runId ? <option value="__new__">＋ 新建知识库…</option> : null}
          </select>
          {creatingKnowledgeBase ? <span className="knowledge-base-create"><input aria-label="新知识库名称" value={newKnowledgeBaseName} autoFocus maxLength={100} placeholder="例如：医学影像" onChange={(event) => setNewKnowledgeBaseName(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") createKnowledgeBase(); }}/><button type="button" onClick={createKnowledgeBase}>创建</button></span> : null}
        </label>
      </div>
      <span className="wiki-reuse-count">{wikiStats?.papers ?? 0} 篇可复用</span>
      <button className="button secondary literature-upload" disabled={busy || working} onClick={() => inputRef.current?.click()}>
        <Upload size={15}/>上传本地文献
      </button>
      <input ref={inputRef} className="visually-hidden" type="file" accept=".pdf,.txt,.md" onChange={(event) => { void upload(event.target.files?.[0]); event.currentTarget.value = ""; }}/>
      <button className="literature-collapse" aria-label={open ? "收起文献来源" : "展开文献来源"} onClick={() => setOpen((value) => !value)}>{open ? <ChevronUp size={17}/> : <ChevronDown size={17}/>}</button>
    </header>
    {open ? <>
      <div className="literature-column-head"><span>标题</span><span>来源</span><span>状态</span><span>操作</span></div>
      <div className="research-literature-list">
        {documents.map((document) => {
          const attached = Boolean(runId && document.run_ids.includes(runId));
          const inWiki = document.wiki_knowledge_base_ids.includes(scope);
          return <article className="research-literature-row" key={document.id}>
            <div className="literature-name"><FileText size={16}/><strong>{document.title || document.filename}</strong></div>
            <span>本地上传 · {document.filename}</span>
            <em className={attached ? "attached" : inWiki ? "wiki" : "available"}>{attached ? "已挂接" : inWiki ? "已复用" : "可使用"}</em>
            <div className="literature-row-actions">
              <a href={literatureFileUrl(document.id)} target="_blank" rel="noreferrer" title="打开本地文献"><ExternalLink size={15}/></a>
              {runId && !attached ? <button title="挂接当前研究" disabled={working} onClick={() => void attach(document)}><Link2 size={15}/></button> : null}
              {!inWiki ? <button title="加入当前 Research Wiki" disabled={working} onClick={() => void addToWiki(document)}><BookPlus size={15}/></button> : null}
              <button title="删除本地文献" disabled={working} onClick={() => void remove(document)}><Trash2 size={15}/></button>
            </div>
          </article>;
        })}
        {remotePapers.map((paper) => <article className="research-literature-row" key={paper.id}>
          <div className="literature-name">{paper.sourceKind === "wiki" ? <BookPlus size={16}/> : <Globe2 size={16}/>}<strong>{paper.title}</strong></div>
          <span>{paper.sourceKind === "wiki" ? "Research Wiki" : "在线检索"} · {paper.source}</span>
          <em className={paper.sourceKind === "wiki" ? "wiki" : "new"}>{paper.sourceKind === "wiki" ? "已复用" : "本轮新增"}</em>
          <div className="literature-row-actions">{paper.url ? <a href={paper.url} target="_blank" rel="noreferrer" title="打开来源"><ExternalLink size={15}/></a> : <span>—</span>}</div>
        </article>)}
        {!documents.length && !remotePapers.length ? <div className="literature-empty">尚无文献。可先上传本地 PDF、TXT 或 Markdown，研究启动后也会显示 Wiki 与在线检索结果。</div> : null}
      </div>
      {message ? <p className="literature-message">{message}</p> : null}
      <footer className="retrieval-process-footer">
        <Search size={16}/>
        {latestRound ? <>
          <strong>{latestRound.label}</strong>
          <span>提出 {latestRound.gapCount} 个缺口</span><i>→</i>
          <span>检索 {latestRound.queryCount} 个查询</span><i>→</i>
          <span>新增 {latestRound.newCount} 篇有效文献</span>
          <button onClick={() => setProcessOpen((value) => !value)}>{processOpen ? "收起记录" : "展开记录"}{processOpen ? <ChevronUp size={14}/> : <ChevronDown size={14}/>}</button>
        </> : <><strong>补充检索</strong><span>研究运行后将在这里显示真实检索过程</span></>}
      </footer>
      {processOpen && rounds.length ? <div className="retrieval-process-detail">
        {rounds.map((round) => <article key={round.id}>
          <header><strong>{round.label}</strong><span>Wiki {round.sources.wiki} / 本地 {round.sources.local} / 在线 {round.sources.external}</span><em>新增 {round.newCount}</em></header>
          {round.queries.length ? <ol>{round.queries.map((query, index) => <li key={`${round.id}-${index}`}>{query}</li>)}</ol> : <p>本轮没有持久化查询文本。</p>}
        </article>)}
      </div> : null}
    </> : null}
  </section>;
}
