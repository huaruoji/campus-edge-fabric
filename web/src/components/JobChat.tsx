import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import type { ApiClient, Conversation, ServerChatMessage } from "../api";
import { usePolling } from "../hooks/usePolling";
import { formatUnix } from "../format";
import { ErrorBox, LoadingLine } from "./ui";
import { TaskPreview } from "./TaskPreview";
import { DevicePicker } from "./DevicePicker";
import "../workspace.css";

export function JobChat({api}: {api: ApiClient; token: string}) {
  const list = usePolling((signal) => api.getConversations(signal), 5000, api.hasToken, [api]);
  const [selected, setSelected] = useState("");
  const [search, setSearch] = useState("");
  const [archived, setArchived] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [creating, setCreating] = useState(false);
  useEffect(() => {
    if (!selected && list.data?.length) setSelected((list.data.find((c) => !c.archived) ?? list.data[0]).id);
  }, [list.data, selected]);
  const conversation = list.data?.find((c) => c.id === selected);
  async function create() {
    if (creating) return;
    setCreating(true); setError(null);
    try { const c = await api.createConversation("新对话"); setSelected(c.id); list.refresh(); }
    catch (cause) { setError(cause); }
    finally { setCreating(false); }
  }
  return <div className="assistant-workspace">
    <aside className="conversation-sidebar" aria-label="团队对话">
      <button className="btn btn--primary" disabled={creating} onClick={() => void create()}>{creating ? "创建中…" : "新建对话"}</button>
      <input className="input" aria-label="搜索对话" placeholder="搜索团队对话" value={search} onChange={(e) => setSearch(e.target.value)} />
      <label className="field-hint"><input type="checkbox" checked={archived} onChange={(e) => setArchived(e.target.checked)} /> 包含已归档</label>
      <nav className="conversation-list" aria-label="历史会话">{list.data?.filter((c) => (archived || !c.archived) && c.title.toLowerCase().includes(search.toLowerCase())).map((c) =>
        <button key={c.id} className={selected === c.id ? "conversation-item selected" : "conversation-item"} aria-current={selected === c.id ? "page" : undefined} onClick={() => setSelected(c.id)}>
          <span>{c.title}{c.archived ? "（已归档）" : ""}</span><small>{c.updated_at ? formatUnix(c.updated_at) : "历史记录"}</small>
        </button>)}</nav>
      <p className="field-hint">所有持有团队 Token 的成员共享这些会话。</p>
      {list.error ? <ErrorBox error={list.error} onRetry={list.refresh} /> : null}
      {error ? <ErrorBox error={error} compact /> : null}
    </aside>
    {conversation ? <ChatThread key={conversation.id} api={api} conversation={conversation} onChanged={list.refresh} /> : <div className="chat-welcome"><h2>把图片交给团队算力池</h2><p>新建一个对话，选择已登记的数据集，让助手规划批量目标检测。</p>{list.loading ? <LoadingLine /> : null}</div>}
  </div>;
}

function ChatThread({api, conversation, onChanged}: {api: ApiClient; conversation: Conversation; onChanged: () => void}) {
  const datasets = usePolling((signal) => api.listDatasets(signal), 30000, api.hasToken, [api]);
  const workers = usePolling((signal) => api.getWorkers(signal), 5000, api.hasToken, [api]);
  const history = usePolling((signal) => api.getChatMessages({room: conversation.id, limit: 200}, signal), 4000, api.hasToken, [api, conversation.id]);
  const [older, setOlder] = useState<ServerChatMessage[]>([]);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [hasOlder, setHasOlder] = useState(true);
  const [draft, setDraft] = useState("");
  const [title, setTitle] = useState(conversation.title);
  const [datasetRef, setDatasetRef] = useState("");
  const [workerIds, setWorkerIds] = useState<string[] | null>(null);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [renameBusy, setRenameBusy] = useState(false);
  const controllerRef = useRef<AbortController | null>(null);
  const transcriptRef = useRef<HTMLDivElement>(null);
  const stickRef = useRef(true);
  useEffect(() => { setTitle(conversation.title); }, [conversation.title]);
  useEffect(() => () => controllerRef.current?.abort(), []);
  useEffect(() => { if (stickRef.current && transcriptRef.current) transcriptRef.current.scrollTop = transcriptRef.current.scrollHeight; }, [history.data, sending]);
  async function update(patch: {title?: string; archived?: boolean}) {
    if (renameBusy) return;
    setRenameBusy(true); setError(null);
    try { await api.patchConversation(conversation.id, patch); onChanged(); }
    catch (cause) { setError(cause); }
    finally { setRenameBusy(false); }
  }
  async function send(event: FormEvent) {
    event.preventDefault();
    if (!draft.trim() || sending || conversation.archived) return;
    setSending(true); setError(null);
    const controller = new AbortController(); controllerRef.current = controller;
    try {
      await api.chat({room: conversation.id, author: "队友", messages: [{role: "user", content: draft.trim()}], context: {dataset: datasetRef || null, files: []}}, controller.signal);
      if (controller.signal.aborted) return;
      setDraft(""); history.refresh(); onChanged();
    } catch (cause) { if (!controller.signal.aborted) {setError(cause); history.refresh();} }
    finally { if (!controller.signal.aborted) setSending(false); }
  }
  const messages = [...new Map([...older, ...(history.data?.messages ?? [])].map((m) => [m.id,m])).values()].sort((a,b)=>a.created_at-b.created_at);
  async function loadOlder() {
    if (loadingOlder || !messages.length) return;
    setLoadingOlder(true); stickRef.current=false;
    try { const page=await api.getChatMessages({room:conversation.id,before:messages[0].created_at,limit:200}); setOlder((old)=>[...page.messages,...old]); setHasOlder(page.messages.length===200); }
    catch(cause) {setError(cause);}
    finally {setLoadingOlder(false);}
  }
  const chosen = datasets.data?.find((d) => d.name === datasetRef);
  return <section className="chat-thread" aria-label="算力助手对话">
    <header className="thread-head"><form onSubmit={(event) => { event.preventDefault(); void update({title: title.trim()}); }}>
      <input className="input thread-title" aria-label="对话名称" maxLength={80} value={title} onChange={(e) => setTitle(e.target.value)} />
      <button className="btn btn--sm btn--ghost" disabled={renameBusy || !title.trim() || title === conversation.title}>保存名称</button>
    </form><button className="btn btn--sm btn--ghost" disabled={renameBusy || sending} onClick={() => void update({archived: !conversation.archived})}>{conversation.archived ? "恢复对话" : "归档对话"}</button></header>
    <div className="chat-transcript" role="log" aria-label="共享对话" ref={transcriptRef} onScroll={() => {const el=transcriptRef.current; if (el) stickRef.current=el.scrollHeight-el.scrollTop-el.clientHeight<64;}}>
      {!history.data && history.loading ? <LoadingLine text="读取对话…" /> : null}
      {history.data?.messages.length === 0 ? <div className="chat-welcome"><h2>今天需要处理哪些图片？</h2><p>选择数据集，描述目标。助手会核对参数，由你确认后创建真实任务。</p>
        <div className="chat-suggestions"><button className="btn btn--ghost" onClick={() => setDraft("对选中的数据集做批量目标检测，使用 yolo11n，并说明输出内容。")}>批量目标检测</button><button className="btn btn--ghost" onClick={() => setDraft("哪些设备具备 GPU 推理能力？最近的任务是否正常？")}>查看设备与任务</button></div>
      </div> : null}
      {hasOlder && (older.length>0 || (history.data?.messages.length ?? 0)>=200) ? <button className="btn btn--sm btn--ghost" disabled={loadingOlder} onClick={()=>void loadOlder()}>{loadingOlder ? "读取中…" : "加载更早的消息"}</button> : null}
      {messages.map((message: ServerChatMessage) => <article key={message.id} className={"chat-message " + message.role}>
        <div className="chat-message-meta"><strong>{message.role === "assistant" ? "算力助手" : message.author || "队友"}</strong><time>{formatUnix(message.created_at)}</time></div>
        <div className="chat-message-content">{message.content}</div>
        {message.preview && !conversation.archived ? <TaskPreview api={api} preview={message.preview} files={[]} dataset={chosen} requiredTag="" workerIds={workerIds} conversationId={conversation.id} messageId={message.id} existingJobId={message.job_id} /> : message.job_id ? <a href={"#/jobs/" + message.job_id}>查看任务详情</a> : null}
        {message.trace?.length ? <details className="chat-tools"><summary>查看真实工具记录（{message.trace.length}）</summary>{message.trace.map((step, index) => <p key={index}>{step.name}：{step.ok ? "成功" : "失败"} {step.summary}</p>)}</details> : null}
      </article>)}
      {sending ? <p className="chat-thinking" role="status">助手正在查看算力池并整理回复…</p> : null}
    </div>
    <div className="chat-composer">
      {history.error ? <ErrorBox error={history.error} onRetry={history.refresh} /> : null}
      {error ? <ErrorBox error={error} compact /> : null}
      <div className="composer-controls"><label>使用的数据集<select className="select" value={datasetRef} onChange={(e) => setDatasetRef(e.target.value)} disabled={sending || !!conversation.archived}>
        <option value="">选择已登记数据集</option>{datasets.data?.map((d) => <option key={d.id} value={d.name}>{d.name}（{d.count} 张）</option>)}</select></label>
        <details><summary>参与设备：{workerIds ? `${workerIds.length} 台指定设备` : "自动选择"}</summary><DevicePicker workers={workers.data ?? []} value={workerIds} onChange={setWorkerIds} disabled={sending || !!conversation.archived} /></details>
      </div>
      {datasets.error ? <ErrorBox error={datasets.error} onRetry={datasets.refresh} /> : null}
      {workers.error ? <ErrorBox error={workers.error} onRetry={workers.refresh} /> : null}
      <form onSubmit={(event) => void send(event)}><textarea className="textarea" aria-label="给算力助手发消息" placeholder="描述你要处理的图片和希望得到的结果…" value={draft} maxLength={4000} rows={3} disabled={!!conversation.archived} onChange={(e) => setDraft(e.target.value)} onKeyDown={(e) => { if(e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing && e.keyCode !== 229) {e.preventDefault(); e.currentTarget.form?.requestSubmit();} }} />
        <div className="composer-bottom"><span className="field-hint">{conversation.archived ? "此对话已归档，恢复后可继续。" : "数据由本地 Agent / CEF CLI 登记；Shift+Enter 换行。"}</span><button className="btn btn--primary" disabled={sending || !draft.trim() || !!conversation.archived}>{sending ? "处理中…" : "发送"}</button></div>
      </form>
    </div>
  </section>;
}
