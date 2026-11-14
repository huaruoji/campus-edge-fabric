import { useEffect, useId, useRef, useState } from "react";
import type { FormEvent, KeyboardEvent } from "react";
import type { AgentToolTrace, ApiClient, ChatMessage } from "../api";
import { CefApiError } from "../api";
import { ErrorBox, LoadingLine, Section } from "./ui";
import "../chat.css";

const MAX_CONTEXT = 12;
const MAX_HISTORY = 100;
const DEMO_COUNT = 12;
const DEMO_PROMPT = "用内置样本跑一次多设备协作演示。把 12 个样本处理为长边 512 像素的 WebP，调用 preview_job 准备任务，我会在任务详情查看每台设备的完成量。";

interface Entry {
  id: string;
  role: ChatMessage["role"] | "status";
  content: string;
  trace?: AgentToolTrace[];
  jobId?: string;
}

function entryId(): string {
  return Array.from(crypto.getRandomValues(new Uint32Array(4)), (part) => part.toString(16).padStart(8, "0")).join("");
}

function readHistory(key: string): Entry[] {
  try {
    const entries: unknown = JSON.parse(localStorage.getItem(key) ?? "[]");
    if (!Array.isArray(entries)) return [];
    return entries.filter((entry) =>
      entry && typeof entry.id === "string" && typeof entry.content === "string" &&
      ["user", "assistant", "status"].includes(entry.role)
    ).slice(-MAX_HISTORY);
  } catch {
    return [];
  }
}

async function historyKey(baseUrl: string, token: string): Promise<string | null> {
  if (!window.crypto?.subtle) return null;
  const bytes = new TextEncoder().encode(JSON.stringify([baseUrl, token.trim()]));
  const hash = await window.crypto.subtle.digest("SHA-256", bytes);
  const hex = Array.from(new Uint8Array(hash), (byte) => byte.toString(16).padStart(2, "0")).join("");
  return "cef.assistant.history." + hex;
}

async function makeDemoFiles(): Promise<File[]> {
  const canvas = document.createElement("canvas");
  canvas.width = 640;
  canvas.height = 480;
  const paint = canvas.getContext("2d");
  if (!paint) throw new Error("无法生成演示样本，请刷新页面后重试。");
  const files: File[] = [];
  for (let index = 0; index < DEMO_COUNT; index += 1) {
    paint.fillStyle = `hsl(${index * 30} 45% 30%)`;
    paint.fillRect(0, 0, canvas.width, canvas.height);
    paint.fillStyle = "#d8e5ff";
    paint.fillRect(20 + index * 13, 120, 220, 180);
    const blob = await new Promise<Blob>((resolve, reject) => {
      canvas.toBlob((result) => {
        if (result) resolve(result);
        else reject(new Error("无法生成演示样本，请刷新页面后重试。"));
      }, "image/png");
    });
    files.push(new File([blob], `cef-demo-${index + 1}.png`, { type: blob.type }));
  }
  return files;
}

export function JobChat({ api, token }: { api: ApiClient; token: string }) {
  const [draft, setDraft] = useState("");
  const [entries, setEntries] = useState<Entry[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [storageKey, setStorageKey] = useState<string | null>(null);
  const [pending, setPending] = useState<"chat" | "demo" | null>(null);
  const [error, setError] = useState<unknown>(null);
  const request = useRef<AbortController | null>(null);
  const transcript = useRef<HTMLDivElement>(null);
  const composerId = useId();

  useEffect(() => {
    let active = true;
    void historyKey(api.baseUrl, token).catch(() => null).then((key) => {
      if (!active) return;
      setStorageKey(key);
      setEntries(key ? readHistory(key) : []);
      setLoaded(true);
    });
    return () => {
      active = false;
      request.current?.abort();
    };
  }, [api.baseUrl, token]);

  useEffect(() => {
    const container = transcript.current;
    if (container) container.scrollTop = container.scrollHeight;
  }, [entries, pending]);

  function updateHistory(next: Entry[]) {
    const recent = next.slice(-MAX_HISTORY);
    setEntries(recent);
    if (!storageKey) return;
    try {
      if (recent.length) localStorage.setItem(storageKey, JSON.stringify(recent));
      else localStorage.removeItem(storageKey);
    } catch {
      setStorageKey(null);
    }
  }

  async function send(mode: "chat" | "demo") {
    const content = mode === "demo" ? DEMO_PROMPT : draft.trim();
    if (!content || !loaded || request.current) return;

    const controller = new AbortController();
    request.current = controller;
    let next: Entry[] = [...entries, { id: entryId(), role: "user", content }];
    updateHistory(next);
    setPending(mode);
    setError(null);
    if (mode === "chat") setDraft("");

    try {
      const files = mode === "demo" ? await makeDemoFiles() : [];
      if (controller.signal.aborted) return;
      const messages = next.flatMap(({ role, content }): ChatMessage[] =>
        role === "status" ? [] : [{ role, content }]
      ).slice(-MAX_CONTEXT);
      const response = await api.chat({
        messages,
        context: { files: files.map(({ name, size }) => ({ name, size })) },
      }, controller.signal);
      if (controller.signal.aborted) return;
      next = [...next, {
        id: entryId(),
        role: "assistant",
        content: response.reply,
        trace: response.tool_trace,
      }];
      updateHistory(next);

      if (mode === "demo") {
        const preview = response.preview;
        if (
          preview?.workload !== "thumbnail" || preview.task_count !== files.length ||
          preview.params.size !== 512 || preview.params.fmt !== "webp" ||
          !response.tool_trace.some((step) => step.name === "preview_job" && step.ok)
        ) {
          throw new CefApiError("invalid", "助手未返回与演示匹配的任务预览。请重新运行演示。");
        }
        const job = await api.submitJob({
          name: "算力池协作演示",
          workload: preview.workload,
          params: preview.params,
          inputs: files.map(({ name }) => ({ name })),
        }, files);
        if (controller.signal.aborted) return;
        updateHistory([...next, {
          id: entryId(),
          role: "status",
          content: `已向算力池提交 ${job.total ?? files.length} 个任务，在线设备会按能力领取。`,
          jobId: job.job_id,
        }]);
      }
    } catch (cause) {
      if (!controller.signal.aborted) {
        setError(cause);
        if (mode === "demo") {
          updateHistory([...next, {
            id: entryId(),
            role: "status",
            content: "本次演示未收到任务创建确认。可先查看任务列表，再决定是否重试。",
          }]);
        }
      }
    } finally {
      if (request.current === controller) {
        request.current = null;
        if (!controller.signal.aborted) setPending(null);
      }
    }
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void send("chat");
  }

  function handleComposerKey(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing || event.keyCode === 229) return;
    event.preventDefault();
    event.currentTarget.form?.requestSubmit();
  }

  return (
    <Section title="算力助手">
      <div className="job-chat">
        <div className="job-chat-demo">
          <div>
            <h3>演示任务</h3>
            <p>处理 12 个内置样本，查看每台设备完成了多少任务。</p>
          </div>
          <button type="button" className="btn btn--primary" disabled={!loaded || pending !== null} onClick={() => void send("demo")}>
            提交演示任务
          </button>
        </div>

        {entries.length > 0 ? (
          <div className="job-chat-transcript" ref={transcript} role="log" aria-label="聊天记录" tabIndex={0}>
            <ol className="job-chat-messages">
              {entries.map((entry) => (
                <li key={entry.id} className={`job-chat-message job-chat-message--${entry.role}`}>
                  <span className="job-chat-role">{entry.role === "user" ? "你" : entry.role === "assistant" ? "算力助手" : "任务状态"}</span>
                  <p>{entry.content}</p>
                  {entry.jobId ? <a href={`#/jobs/${encodeURIComponent(entry.jobId)}`}>查看任务详情</a> : null}
                  {entry.trace?.length ? (
                    <details className="job-chat-trace">
                      <summary>工具调用记录（{entry.trace.length}）</summary>
                      <ol>
                        {entry.trace.map((step, index) => (
                          <li key={index}>
                            <div className="job-chat-tool-head">
                              <code>{step.name}</code>
                              <span className={step.ok ? "job-chat-tool-ok" : "job-chat-tool-error"}>{step.ok ? "成功" : "失败"}</span>
                            </div>
                            {step.summary ? <p>{step.summary}</p> : null}
                          </li>
                        ))}
                      </ol>
                    </details>
                  ) : null}
                </li>
              ))}
            </ol>
          </div>
        ) : null}
        {pending ? <div role="status"><LoadingLine text={pending === "demo" ? "正在准备并提交演示任务…" : "算力助手正在处理…"} /></div> : null}
        {error ? <ErrorBox error={error} /> : null}

        <form className="job-chat-form" onSubmit={handleSubmit}>
          <label htmlFor={composerId} className="field-label">给算力助手发消息</label>
          <textarea
            id={composerId}
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
              {storageKey ? "聊天记录保存在此浏览器" : "聊天记录仅保留在当前页面"} · 每次发送最近 12 条消息
            </span>
            <div className="job-chat-buttons">
              {entries.length ? (
                <button type="button" className="btn btn--ghost" disabled={pending !== null} onClick={() => {
                  updateHistory([]);
                  setError(null);
                }}>清空记录</button>
              ) : null}
              <button type="submit" className="btn" disabled={!loaded || !draft.trim() || pending !== null}>发送</button>
            </div>
          </div>
        </form>
      </div>
    </Section>
  );
}
