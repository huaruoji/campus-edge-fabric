import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import type { ChangeEvent, FormEvent, KeyboardEvent } from "react";
import type {
  AgentPreview,
  AgentToolTrace,
  ApiClient,
  ChatMessage,
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

/** 共享对话轮询间隔（毫秒）。 */
const CHAT_POLL_MS = 4000;
/** 一次最多拉多少条历史/增量。 */
const CHAT_PAGE_LIMIT = 200;
/** 发给模型的历史条数上限。 */
const MAX_CONTEXT_MESSAGES = 12;
/** 作为上下文带给服务端的文件数上限。 */
const MAX_CONTEXT_FILES = 200;
const NICKNAME_KEY = "cef.nickname";
const DEFAULT_NICKNAME = "队友";

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

  const workloads = usePolling((signal) => api.getWorkloads(signal), 15000, api.hasToken, [api]);
  const tags = usePolling((signal) => api.getTags(signal), 15000, api.hasToken, [api]);

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
        context: { files: files.slice(0, MAX_CONTEXT_FILES).map(({ name, size }) => ({ name, size })) },
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
    const missing = preset.fileCount - files.length;
    setNotice(missing > 0
      ? `已把「${preset.title}」的文案填进输入框，还没有发送。该预设需要 ${preset.fileCount} 个真实文件（来自 ${preset.dataset}），当前已载入 ${files.length} 个，先补 ${missing} 个。`
      : `已把「${preset.title}」的文案填进输入框，还没有发送。确认文案后点「发送」。`);
    textareaRef.current?.focus();
    // 只填文案：这里不发起任何请求。
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
            return (
              <li key={preset.id}>
                <button
                  type="button"
                  className={"preset-card" + (availability.available ? "" : " preset-card--disabled")}
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
                      <dd>{preset.taskCount} 个</dd>
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
                  {preset.fileCount > 0 ? (
                    <span className="preset-files-hint">
                      需要 {preset.fileCount} 个真实文件（当前 {files.length} 个）
                    </span>
                  ) : null}
                </button>
              </li>
            );
          })}
        </ul>

        <div className="job-chat-sources">
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
                    {entry.preview ? <PreviewBlock preview={entry.preview} /> : null}
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
            <span className="field-hint">
              已载入 {files.length} 个样本文件；需要文件时先上传，再点预设或直接描述任务
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

function PreviewBlock({ preview }: { preview: AgentPreview }) {
  return (
    <div className="job-chat-preview">
      <div className="job-chat-preview-head">
        <span>{workloadLabel(preview.workload)}</span>
        <span>{preview.task_count === null ? "任务数待定" : "预计 " + preview.task_count + " 个任务"}</span>
      </div>
      <code className="job-chat-preview-params">{JSON.stringify(preview.params)}</code>
      {preview.note ? <p>{preview.note}</p> : null}
    </div>
  );
}
