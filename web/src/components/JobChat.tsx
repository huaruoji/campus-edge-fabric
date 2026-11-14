import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import type { ChangeEvent, FormEvent, KeyboardEvent } from "react";
import type {
  AgentToolTrace,
  ApiClient,
  ChatMessage,
  DatasetSummary,
  ServerChatMessage,
  TagStat,
  Workload,
} from "../api";
import { CefApiError } from "../api";
import { formatRelativeSeconds, formatUnix } from "../format";
import { usePolling, usePollingLoop } from "../hooks/usePolling";
import { PRESETS, workloadLabel, type Preset } from "../presets";
import { EmptyBox, ErrorBox, LoadingLine, Section } from "./ui";
import "../chat.css";
import { TaskPreview } from "./TaskPreview";

/** 共享对话轮询间隔（毫秒）。 */
const CHAT_POLL_MS = 4000;
/** 一次最多拉多少条历史/增量。 */
const CHAT_PAGE_LIMIT = 200;
/** 发给模型的历史条数上限。 */
const MAX_CONTEXT_MESSAGES = 12;
/** 作为上下文带给服务端的文件数上限。 */
const MAX_CONTEXT_FILES = 200;
/** 数据集列表轮询间隔（毫秒）：数据集不会频繁变动，拉慢一点。 */
const DATASET_POLL_MS = 30000;
const NICKNAME_KEY = "cef.nickname";
const DEFAULT_NICKNAME = "队友";

/** 数据来源：已登记数据集 / 手动上传文件，二选一。 */
type DataSource = "dataset" | "files";

function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined || !Number.isFinite(bytes) || bytes < 0) return "—";
  if (bytes < 1024) return bytes + " B";
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
  return (bytes / (1024 * 1024)).toFixed(1) + " MB";
}

/** 复制文本：优先 Clipboard API，非安全上下文退回 execCommand。 */
async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // 退回到选中复制。
  }
  try {
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.top = "-1000px";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    const ok = document.execCommand("copy");
    area.remove();
    return ok;
  } catch {
    return false;
  }
}

interface ChatEntry extends ServerChatMessage {
  /** 仅本地乐观气泡使用：pending=请求进行中，failed=发送失败可重试。 */
  status?: "pending" | "failed";
}

function localId(prefix: string): string {
  const parts = crypto.getRandomValues(new Uint32Array(2));
  return prefix + Array.from(parts, (part) => part.toString(16).padStart(8, "0")).join("");
}

function compareEntries(a: ChatEntry, b: ChatEntry): number {
  if (a.created_at !== b.created_at) return a.created_at - b.created_at;
  return a.id.localeCompare(b.id);
}

function readNickname(): string {
  try {
    const stored = window.localStorage.getItem(NICKNAME_KEY)?.trim();
    if (stored) return stored.slice(0, 24);
  } catch {
    // 隐私模式下读取失败，用默认昵称。
  }
  return DEFAULT_NICKNAME;
}

function normalizeNickname(raw: string): string {
  return raw.replace(/\s+/g, " ").trim().slice(0, 24);
}

function isAbortErrorLike(error: unknown): boolean {
  return error instanceof CefApiError && error.kind === "aborted";
}

function isEndpointMissing(error: unknown): boolean {
  return error instanceof CefApiError && error.kind === "not_found";
}

interface PresetAvailability {
  /** 已知没有设备可跑时为 false（卡片置灰）。 */
  available: boolean;
  devices: string[];
  /** 置灰原因，可以直接显示给用户。 */
  reason: string | null;
}

export function JobChat({ api }: { api: ApiClient; token: string }) {
  const [nickname, setNickname] = useState(readNickname);
  const [nicknameDraft, setNicknameDraft] = useState(nickname);
  const [editingNickname, setEditingNickname] = useState(false);
  const [draft, setDraft] = useState("");
  const [entries, setEntries] = useState<ChatEntry[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [loadError, setLoadError] = useState<unknown>(null);
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState<unknown>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [dataSource, setDataSource] = useState<DataSource>("files");
  const [datasetRef, setDatasetRef] = useState("");
  const [tagFilter, setTagFilter] = useState("");
  const [nowTick, setNowTick] = useState(() => Date.now());

  const entriesRef = useRef<ChatEntry[]>(entries);
  entriesRef.current = entries;
  const latestRef = useRef(0);
  const stickRef = useRef(true);
  const transcriptRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const requestRef = useRef<AbortController | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const composerId = useId();
  const nicknameId = useId();
  const tagSelectId = useId();
  const dataSourceName = useId();
  const datasetSelectId = useId();

  const workloads = usePolling((signal) => api.getWorkloads(signal), 15000, api.hasToken, [api]);
  const tags = usePolling((signal) => api.getTags(signal), 15000, api.hasToken, [api]);
  const datasets = usePolling((signal) => api.listDatasets(signal), DATASET_POLL_MS, api.hasToken, [api]);

  const datasetList: DatasetSummary[] = datasets.data ?? [];
  const datasetByName = useMemo(() => {
    const map = new Map<string, DatasetSummary>();
    for (const item of datasetList) map.set(item.name, item);
    return map;
  }, [datasetList]);
  /** 后端还没上线 GET /datasets 时（404），数据来源里隐藏「数据集」，只保留上传。 */
  const datasetEndpointMissing = datasets.error instanceof CefApiError && datasets.error.kind === "not_found";
  const selectedDataset = dataSource === "dataset" ? datasetByName.get(datasetRef) ?? null : null;
  /** 实际随对话上下文发送的数据集名；上传文件模式下为 null。 */
  const datasetContext = selectedDataset ? selectedDataset.name : null;

  const workloadMap = useMemo(() => {
    const map = new Map<string, Workload>();
    for (const workload of workloads.data ?? []) map.set(workload.kind, workload);
    return map;
  }, [workloads.data]);

  const tagStats: TagStat[] = tags.data ?? [];
  const selectedTag = tagStats.find((item) => item.tag === tagFilter) ?? null;

  useEffect(() => {
    const timer = window.setInterval(() => setNowTick(Date.now()), 30000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    const container = transcriptRef.current;
    if (container && stickRef.current) container.scrollTop = container.scrollHeight;
  }, [entries, sending]);

  const mergeMessages = useCallback((incoming: ServerChatMessage[], latest: number) => {
    if (latest > latestRef.current) latestRef.current = latest;
    if (incoming.length === 0) return;
    setEntries((previous) => {
      const map = new Map<string, ChatEntry>();
      for (const item of previous) map.set(item.id, item);
      for (const item of incoming) map.set(item.id, item);
      return [...map.values()].sort(compareEntries);
    });
  }, []);

  /** 拉一次增量：有游标带 after，只取新消息；首次加载不带 after 取最近 200 条。 */
  const pull = useCallback(async (signal: AbortSignal) => {
    try {
      const page = await api.getChatMessages({
        room: "pool",
        after: latestRef.current > 0 ? latestRef.current : undefined,
        limit: CHAT_PAGE_LIMIT,
      }, signal);
      mergeMessages(page.messages, page.latest);
      setLoadError(null);
    } catch (error) {
      if (isAbortErrorLike(error)) return;
      setLoadError(error);
    } finally {
      if (!signal.aborted) setLoaded(true);
    }
  }, [api, mergeMessages]);

  usePollingLoop((signal) => pull(signal), CHAT_POLL_MS, api.hasToken);

  async function send(contentRaw: string, retryId?: string): Promise<void> {
    const content = contentRaw.trim();
    if (!content || sending || !api.hasToken) return;

    const author = nickname;
    const optimistic: ChatEntry = {
      id: retryId ?? localId("local-"),
      room: "pool",
      role: "user",
      author,
      content,
      trace: null,
      preview: null,
      job_id: null,
      created_at: Date.now() / 1000,
      status: "pending",
    };

    if (retryId) {
      setEntries((previous) => previous.map((item) => (item.id === retryId ? optimistic : item)));
    } else {
      setEntries((previous) => [...previous, optimistic]);
    }
    setDraft("");
    setSendError(null);
    setNotice(null);
    setSending(true);

    const controller = new AbortController();
    requestRef.current = controller;
    const history = [...entriesRef.current.filter((item) => item.status !== "failed"), optimistic]
      .map(({ role, content: text }): ChatMessage => ({ role, content: text }))
      .slice(-MAX_CONTEXT_MESSAGES);

    try {
      const response = await api.chat({
        messages: history,
        context: {
          // 数据集与上传文件互斥：选了数据集就不带 files。
          files: datasetContext
            ? []
            : files.slice(0, MAX_CONTEXT_FILES).map(({ name, size }) => ({ name, size })),
          dataset: datasetContext,
        },
        author,
        room: "pool",
      }, controller.signal);
      if (controller.signal.aborted) return;

      const userId = response.user_message_id ?? optimistic.id;
      const assistantId = response.assistant_message_id ?? localId("local-");
      setEntries((previous) => {
        const next: ChatEntry[] = previous.map((item) =>
          item.id === optimistic.id ? { ...item, id: userId, status: undefined } : item
        );
        next.push({
          id: assistantId,
          room: "pool",
          role: "assistant",
          author: "",
          content: response.reply,
          trace: response.tool_trace.length > 0 ? response.tool_trace : null,
          preview: response.preview,
          job_id: null,
          created_at: Date.now() / 1000,
        });
        const map = new Map(next.map((item) => [item.id, item]));
        return [...map.values()].sort(compareEntries);
      });
      if (response.preview) setNotice("助手给出了任务预览，请在气泡里核对参数后再决定下一步。");
      void pull(new AbortController().signal);
    } catch (error) {
      if (!controller.signal.aborted) {
        setSendError(error);
        setEntries((previous) =>
          previous.map((item) => (item.id === optimistic.id ? { ...item, status: "failed" } : item))
        );
      }
    } finally {
      if (requestRef.current === controller) requestRef.current = null;
      if (!controller.signal.aborted) setSending(false);
    }
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    void send(draft);
  }

  function handleComposerKey(event: KeyboardEvent<HTMLTextAreaElement>): void {
    if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing || event.keyCode === 229) return;
    event.preventDefault();
    event.currentTarget.form?.requestSubmit();
  }

  function handleTranscriptScroll(): void {
    const container = transcriptRef.current;
    if (!container) return;
    stickRef.current = container.scrollHeight - container.scrollTop - container.clientHeight < 48;
  }

  function applyPreset(preset: Preset): void {
    setDraft(preset.prompt);
    setSendError(null);
    // 只填文案 + 预选数据来源：这里不发起任何请求，也不发送消息。
    const registered = datasetByName.get(preset.datasetRef) ?? null;
    if (datasetEndpointMissing) {
      setDataSource("files");
      setNotice(
        `已把「${preset.title}」的文案填进输入框，还没有发送。后端未提供数据集接口，请手动上传 ${preset.fileCount} 个文件。`
      );
    } else if (registered) {
      setDataSource("dataset");
      setDatasetRef(registered.name);
      setNotice(
        `已把「${preset.title}」的文案填进输入框，数据来源设为数据集 ${registered.name}（${registered.count} 个文件 / ${formatBytes(registered.bytes)}），还没有发送。`
      );
    } else {
      setDataSource("files");
      setNotice(
        `已把「${preset.title}」的文案填进输入框，还没有发送。数据集 ${preset.datasetRef} 尚未登记：先在本机运行 ${preset.datasetCommand}，或手动上传 ${preset.fileCount} 个文件。`
      );
    }
    textareaRef.current?.focus();
  }

  function availabilityOf(preset: Preset): PresetAvailability {
    const workload = workloadMap.get(preset.workload);
    if (!workloads.data) return { available: true, devices: [], reason: null };
    if (!workload) {
      return {
        available: false,
        devices: [],
        reason: "后端没有注册「" + workloadLabel(preset.workload) + "」工作负载，暂时不能跑。",
      };
    }
    if (workload.available_on.length === 0) {
      return {
        available: false,
        devices: [],
        reason: "当前没有在线设备声明可运行「" + workloadLabel(preset.workload) + "」，启动对应 Worker 后再试。",
      };
    }
    return { available: true, devices: workload.available_on, reason: null };
  }

  function handleFileInput(event: ChangeEvent<HTMLInputElement>): void {
    const selected = Array.from(event.target.files ?? []);
    event.target.value = "";
    if (selected.length === 0) return;
    setFiles((previous) => {
      const seen = new Set(previous.map((file) => file.name + ":" + file.size));
      return [...previous, ...selected.filter((file) => !seen.has(file.name + ":" + file.size))];
    });
    setNotice(`已加入 ${selected.length} 个文件，不会自动发送。`);
  }

  function startNicknameEdit(): void {
    setNicknameDraft(nickname);
    setEditingNickname(true);
  }

  function commitNickname(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    const next = normalizeNickname(nicknameDraft) || DEFAULT_NICKNAME;
    const previous = nickname;
    setNickname(next);
    setNicknameDraft(next);
    setEditingNickname(false);
    if (next !== previous) {
      // 只改本页显示：把这次会话里自己发过的消息改挂到新昵称，避免改完昵称后
      // 自己的历史气泡突然变成「别人」。服务端保存的 author 不会被改动。
      setEntries((current) =>
        current.map((item) =>
          item.role === "user" && item.author === previous ? { ...item, author: next } : item
        )
      );
    }
    try {
      window.localStorage.setItem(NICKNAME_KEY, next);
    } catch {
      // 存储不可用时只在本次会话生效。
    }
  }

  function retryLoad(): void {
    void pull(new AbortController().signal);
  }

  return (
    <Section title="算力助手">
      <div className="job-chat">
        <div className="job-chat-head">
          <div>
            <h3>演示预设</h3>
            <p>点卡片只把文案填进输入框并聚焦，不会发送任何请求；需要图片的预设请先上传文件。</p>
          </div>
          <div className="job-chat-head-actions">
            {editingNickname ? (
              <form className="job-chat-nickname" onSubmit={commitNickname}>
                <label htmlFor={nicknameId} className="field-label">我的昵称</label>
                <input
                  id={nicknameId}
                  className="input"
                  value={nicknameDraft}
                  maxLength={24}
                  onChange={(event) => setNicknameDraft(event.target.value)}
                  autoFocus
                />
                <button type="submit" className="btn btn--sm btn--primary">保存</button>
                <button
                  type="button"
                  className="btn btn--sm btn--ghost"
                  onClick={() => { setEditingNickname(false); setNicknameDraft(nickname); }}
                >
                  取消
                </button>
              </form>
            ) : (
              <button type="button" className="btn btn--sm btn--ghost" onClick={startNicknameEdit}>
                昵称：{nickname}
              </button>
            )}
          </div>
        </div>

        <ul className="preset-grid">
          {PRESETS.map((preset) => {
            const availability = availabilityOf(preset);
            const registered = datasetByName.get(preset.datasetRef) ?? null;
            const taskCount = registered ? registered.count : preset.taskCount;
            return (
              <li key={preset.id}>
                <div className={"preset-card" + (availability.available ? "" : " preset-card--disabled")}>
                  <button
                    type="button"
                    className="preset-main"
                    onClick={() => applyPreset(preset)}
                    disabled={!availability.available}
                    aria-describedby={availability.reason ? "preset-reason-" + preset.id : undefined}
                  >
                    <span className="preset-title">{preset.title}</span>
                    <dl className="preset-meta">
                      <div>
                        <dt>数据集</dt>
                        <dd>{preset.dataset}</dd>
                      </div>
                      <div>
                        <dt>任务</dt>
                        <dd>{workloadLabel(preset.workload)}</dd>
                      </div>
                      <div>
                        <dt>预计任务数</dt>
                        <dd>
                          {taskCount} 个
                          <span className="preset-note">{registered ? "（按登记文件数）" : "（估算）"}</span>
                        </dd>
                      </div>
                      <div>
                        <dt>可运行设备</dt>
                        <dd>
                          {availability.devices.length > 0
                            ? availability.devices.join("、")
                            : availability.available
                              ? "读取中…"
                              : "暂无"}
                        </dd>
                      </div>
                    </dl>
                    <span className="preset-params">{preset.paramsText}</span>
                    {availability.reason ? (
                      <span className="preset-reason" id={"preset-reason-" + preset.id}>{availability.reason}</span>
                    ) : null}
                  </button>
                  <div className="preset-dataset">
                    <span className="preset-dataset-label">数据来源</span>
                    {datasetEndpointMissing ? (
                      <span className="preset-dataset-state preset-dataset-state--missing">
                        后端未提供数据集接口，需手动上传 {preset.fileCount} 个文件
                      </span>
                    ) : registered ? (
                      <span className="preset-dataset-state preset-dataset-state--ok">
                        已登记 · {registered.count} 个文件 / {formatBytes(registered.bytes)}
                      </span>
                    ) : (
                      <>
                        <span className="preset-dataset-state preset-dataset-state--missing">
                          数据集 {preset.datasetRef} 未登记
                        </span>
                        <code className="preset-command">{preset.datasetCommand}</code>
                        <div className="preset-dataset-actions">
                          <button
                            type="button"
                            className="btn btn--sm btn--ghost"
                            onClick={() => {
                              void copyText(preset.datasetCommand).then((ok) => {
                                setNotice(ok ? "已复制登记命令到剪贴板。" : "复制失败，请手动选中命令文本。");
                              });
                            }}
                          >
                            复制命令
                          </button>
                          <span className="field-hint">卡片仍可点，但只能手动上传 {preset.fileCount} 个文件。</span>
                        </div>
                      </>
                    )}
                  </div>
                </div>
              </li>
            );
          })}
        </ul>

        <div className="job-chat-sources">
          <fieldset className="data-source">
            <legend className="field-label">数据来源</legend>
            <div className="data-source-options">
              <label className="data-source-option">
                <input
                  type="radio"
                  name={dataSourceName}
                  value="dataset"
                  checked={dataSource === "dataset"}
                  disabled={datasetEndpointMissing}
                  onChange={() => setDataSource("dataset")}
                />
                已登记数据集
              </label>
              <label className="data-source-option">
                <input
                  type="radio"
                  name={dataSourceName}
                  value="files"
                  checked={dataSource === "files"}
                  onChange={() => setDataSource("files")}
                />
                上传文件
              </label>
            </div>
          </fieldset>

          {dataSource === "dataset" && !datasetEndpointMissing ? (
            <div className="job-chat-dataset">
              <label htmlFor={datasetSelectId} className="field-label">选择数据集</label>
              <select
                id={datasetSelectId}
                className="select select--sm"
                value={datasetRef}
                onChange={(event) => setDatasetRef(event.target.value)}
              >
                <option value="">选择数据集…</option>
                {datasetList.map((item) => (
                  <option key={item.id} value={item.name}>{item.name}</option>
                ))}
              </select>
              <span className="field-hint" aria-live="polite">
                {selectedDataset
                  ? `${selectedDataset.count} 个文件 / ${formatBytes(selectedDataset.bytes)}；提交时不传文件`
                  : datasets.loading
                    ? "正在读取数据集…"
                    : datasetList.length === 0
                      ? "还没有已登记的数据集：先运行 cef dataset add …"
                      : "选一个已登记的数据集，不需要上传任何文件"}
              </span>
              {selectedDataset?.description ? (
                <span className="field-hint">{selectedDataset.description}</span>
              ) : null}
            </div>
          ) : (
            <div className="job-chat-files">
              <span className="field-hint">待处理文件：{files.length} 个</span>
              <button
                type="button"
                className="btn btn--sm btn--ghost"
                onClick={() => fileInputRef.current?.click()}
              >
                上传文件
              </button>
              <input ref={fileInputRef} type="file" multiple hidden onChange={handleFileInput} />
              {files.length > 0 ? (
                <button
                  type="button"
                  className="btn btn--sm btn--ghost"
                  onClick={() => { setFiles([]); setNotice("已清空样本文件，不会影响已经发出的对话。"); }}
                >
                  清空文件
                </button>
              ) : null}
            </div>
          )}
          {dataSource === "dataset" && datasetEndpointMissing ? (
            <p className="field-hint">后端没有数据集接口（404），暂时只能上传文件。</p>
          ) : null}
          {tagStats.length > 0 ? (
            <div className="job-chat-tag">
              <label htmlFor={tagSelectId} className="field-label">设备标签</label>
              <select
                id={tagSelectId}
                className="select select--sm"
                value={tagFilter}
                onChange={(event) => setTagFilter(event.target.value)}
              >
                <option value="">不限制</option>
                {tagStats.map((item) => (
                  <option key={item.tag} value={item.tag}>{item.tag}</option>
                ))}
              </select>
              <span className="field-hint" aria-live="polite">
                {selectedTag ? `该标签下有 ${selectedTag.online} 台在线设备` : "未限制设备标签"}
              </span>
            </div>
          ) : null}
        </div>

        <div className="job-chat-transcript" ref={transcriptRef} role="log" aria-label="共享对话" tabIndex={0} onScroll={handleTranscriptScroll}>
          {!loaded && !loadError ? <LoadingLine text="正在读取共享对话…" /> : null}
          {entries.length > 0 ? (
            <ol className="job-chat-messages">
              {entries.map((entry) => {
                const own = entry.role === "user" && entry.author === nickname;
                const seconds = Math.max(0, nowTick / 1000 - entry.created_at);
                return (
                  <li
                    key={entry.id}
                    className={
                      "job-chat-message job-chat-message--" + entry.role +
                      (own ? " job-chat-message--own" : "") +
                      (entry.status === "failed" ? " job-chat-message--failed" : "")
                    }
                  >
                    <div className="job-chat-message-head">
                      {own ? (
                        <button
                          type="button"
                          className="job-chat-author job-chat-author--own"
                          onClick={startNicknameEdit}
                          aria-label={"修改我的昵称，当前是 " + entry.author}
                          title="点这里修改昵称"
                        >
                          {entry.author}
                        </button>
                      ) : (
                        <span className="job-chat-author">
                          {entry.author || (entry.role === "assistant" ? "算力助手" : DEFAULT_NICKNAME)}
                        </span>
                      )}
                      <time
                        className="job-chat-time"
                        dateTime={new Date(entry.created_at * 1000).toISOString()}
                        title={formatUnix(entry.created_at)}
                      >
                        {formatRelativeSeconds(seconds)}
                      </time>
                      {entry.status === "pending" ? <span className="job-chat-state">发送中…</span> : null}
                      {entry.status === "failed" ? (
                        <>
                          <span className="job-chat-state job-chat-state--err">发送失败</span>
                          <button
                            type="button"
                            className="btn btn--sm btn--ghost"
                            onClick={() => void send(entry.content, entry.id)}
                            disabled={sending}
                          >
                            重试
                          </button>
                        </>
                      ) : null}
                    </div>
                    <p>{entry.content}</p>
                    {entry.job_id ? (
                      <a href={"#/jobs/" + encodeURIComponent(entry.job_id)}>查看任务详情</a>
                    ) : null}
                    {entry.preview ? <TaskPreview
                      api={api}
                      preview={entry.preview}
                      files={dataSource === "files" || datasetEndpointMissing ? files : []}
                      dataset={selectedDataset ?? undefined}
                      requiredTag={tagFilter}
                      existingJobId={entry.job_id}
                      messageId={entry.id}
                    /> : null}
                    {entry.trace && entry.trace.length > 0 ? (
                      <details className="job-chat-trace">
                        <summary>工具调用记录（{entry.trace.length}）</summary>
                        <ol>
                          {entry.trace.map((step: AgentToolTrace, index: number) => (
                            <li key={index}>
                              <div className="job-chat-tool-head">
                                <code>{step.name}</code>
                                <span className={step.ok ? "job-chat-tool-ok" : "job-chat-tool-error"}>
                                  {step.ok ? "成功" : "失败"}
                                </span>
                              </div>
                              {step.summary ? <p>{step.summary}</p> : null}
                            </li>
                          ))}
                        </ol>
                      </details>
                    ) : null}
                  </li>
                );
              })}
            </ol>
          ) : null}
          {loaded && !loadError && entries.length === 0 ? (
            <EmptyBox title="还没有人发言" hint="在下面输入第一句话。对话对持有 Token 的所有队友共享。" />
          ) : null}
        </div>

        {loadError ? (
          isEndpointMissing(loadError) ? (
            <div className="notice notice--warn" role="status">
              共享对话接口暂不可用：后端还没有部署 GET /api/v1/chat/messages。部署完成后刷新页面即可。
            </div>
          ) : (
            <ErrorBox error={loadError} onRetry={retryLoad} />
          )
        ) : null}
        {sendError ? <ErrorBox error={sendError} onRetry={() => setSendError(null)} /> : null}
        {notice ? <div className="notice" role="status" aria-live="polite">{notice}</div> : null}
        {sending ? <div role="status"><LoadingLine text="算力助手正在处理…" /></div> : null}

        <form className="job-chat-form" onSubmit={handleSubmit}>
          <div className="job-chat-form-head">
            <label htmlFor={composerId} className="field-label">给算力助手发消息</label>
            <span className="field-hint">每次发送带最近 {MAX_CONTEXT_MESSAGES} 条消息；对话每 4 秒同步一次</span>
          </div>
          <textarea
            id={composerId}
            ref={textareaRef}
            className="textarea"
            value={draft}
            rows={3}
            maxLength={4000}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={handleComposerKey}
            placeholder="比如：目前有哪些设备在线？"
          />
          <div className="job-chat-composer-actions">
            <span className="field-hint" aria-live="polite">
              {datasetContext
                ? `数据来源：数据集 ${datasetContext}；不需要上传文件`
                : `数据来源：已载入 ${files.length} 个文件；也可以切换成已登记数据集`}
            </span>
            <div className="job-chat-buttons">
              <button type="button" className="btn btn--ghost" disabled={sending} onClick={retryLoad}>
                刷新对话
              </button>
              <button type="submit" className="btn" disabled={!loaded || !draft.trim() || sending}>
                发送
              </button>
            </div>
          </div>
        </form>
      </div>
    </Section>
  );
}
