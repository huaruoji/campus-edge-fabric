import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import type { FormEvent, KeyboardEvent } from "react";
import type { AgentChatResponse, ApiClient, ChatMessage, JobSpec, SubmitResponse, Workload } from "../api";
import { formatNumber } from "../format";
import { ErrorBox, LoadingLine } from "./ui";
import "../chat.css";

const MAX_MESSAGES = 12;
const MAX_MESSAGE_LENGTH = 4000;
const WORKLOAD_LABELS: Record<string, string> = {
  thumbnail: "图片缩略图",
  infer: "图片目标检测",
  blender: "Blender 动画渲染",
};
const UNIT_LABELS: Record<string, string> = { images: "张", frames: "帧", tasks: "个任务" };
const PARAM_LABELS: Record<string, string> = {
  size: "尺寸",
  fmt: "格式",
  quality: "质量",
  model: "模型",
  imgsz: "输入尺寸",
  conf: "置信度",
  max_det: "检测上限",
  frames: "帧范围",
  resolution: "分辨率比例",
  samples: "采样数",
  engine: "渲染引擎",
};

interface Props {
  api: ApiClient;
  files: readonly File[];
  workloads: readonly Workload[];
  submitJob: (spec: JobSpec) => Promise<SubmitResponse>;
  submitting: boolean;
  onSubmitted: (jobId: string) => void;
}

interface ConversationMessage extends ChatMessage {
  id: number;
}

interface ChatResult {
  response: AgentChatResponse;
  files: readonly File[];
}

export function JobChat({ api, files, workloads, submitJob, submitting, onSubmitted }: Props) {
  const [draft, setDraft] = useState("");
  const [messages, setMessages] = useState<ConversationMessage[]>([]);
  const [result, setResult] = useState<ChatResult | null>(null);
  const [waiting, setWaiting] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const messageId = useRef(0);
  const request = useRef<AbortController | null>(null);
  const submissionGeneration = useRef(0);
  const transcript = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const hintId = useId();
  const composerId = useId();

  const stalePreview = Boolean(result?.response.preview && result.files !== files);
  const preview = stalePreview ? null : result?.response.preview;
  const toolTrace = result?.response.tool_trace;
  const workloadUnit = preview && workloads.find((item) => item.kind === preview.workload)?.unit;
  const unit = workloadUnit ?? (preview?.workload === "blender" ? "frames" : "images");
  const fileHint = files.length === 0
    ? "请先添加输入文件，再发送需求以更新预览。"
    : preview?.workload === "blender" && (files.length !== 1 || !/\.blend$/i.test(files[0].name))
      ? "Blender 渲染需要且仅需一个 .blend 文件。"
      : "";
  const visibleMessages = waiting ? messages.slice(-(MAX_MESSAGES - 1)) : messages;

  useLayoutEffect(() => {
    setDraft("");
    setMessages([]);
    setResult(null);
    setError(null);
    setConfirming(false);
    return () => { submissionGeneration.current += 1; };
  }, [api]);

  useLayoutEffect(() => {
    setWaiting(false);
    return () => {
      request.current?.abort();
      request.current = null;
    };
  }, [api, files]);

  useEffect(() => {
    const container = transcript.current;
    if (container) container.scrollTop = container.scrollHeight;
  }, [messages, waiting]);

  async function sendMessage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const content = draft.trim();
    if (!content || request.current || submitting) return;

    const controller = new AbortController();
    request.current = controller;
    const user: ConversationMessage = { id: messageId.current++, role: "user", content };
    const nextMessages = [...messages, user].slice(-MAX_MESSAGES);

    setError(null);
    setResult(null);
    setWaiting(true);
    try {
      const response = await api.chat({
        messages: nextMessages.map(({ role, content }) => ({ role, content })),
        context: { files: files.map(({ name, size }) => ({ name, size })) },
      }, controller.signal);
      if (controller.signal.aborted) return;
      const assistant: ConversationMessage = { id: messageId.current++, role: "assistant", content: response.reply };
      setMessages([...nextMessages, assistant].slice(-MAX_MESSAGES));
      setDraft("");
      setResult({ response, files });
    } catch (cause) {
      if (!controller.signal.aborted) setError(cause);
    } finally {
      if (request.current === controller) {
        request.current = null;
        setWaiting(false);
      }
    }
  }

  function handleComposerKey(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing || event.keyCode === 229) return;
    event.preventDefault();
    event.currentTarget.form?.requestSubmit();
  }

  async function confirmPreview() {
    if (!preview || fileHint || submitting || waiting) return;
    const generation = submissionGeneration.current;
    setConfirming(true);
    setError(null);
    try {
      const job = await submitJob({
        workload: preview.workload,
        params: preview.params,
        inputs: files.map(({ name }) => ({ name })),
      });
      if (generation === submissionGeneration.current) onSubmitted(job.job_id);
    } catch (cause) {
      if (generation === submissionGeneration.current) setError(cause);
    } finally {
      if (generation === submissionGeneration.current) setConfirming(false);
    }
  }

  return (
    <section className="job-chat" aria-labelledby={titleId}>
      <div className="job-chat-head">
        <h3 id={titleId}>用一句话提交</h3>
        <p>告诉 AI 助手怎么处理这些文件，确认预览后再提交。</p>
      </div>

      <div className="job-chat-transcript" ref={transcript} role="log" aria-label="聊天记录" tabIndex={0}>
        {visibleMessages.length > 0 || waiting ? (
          <ol className="job-chat-messages">
            {visibleMessages.map((message) => (
              <li key={message.id} className={"job-chat-message job-chat-message--" + message.role}>
                <span className="job-chat-role">{message.role === "user" ? "你" : "AI 助手"}</span>
                <p>{message.content}</p>
              </li>
            ))}
            {waiting ? (
              <li className="job-chat-message job-chat-message--user">
                <span className="job-chat-role">你</span>
                <p>{draft.trim()}</p>
              </li>
            ) : null}
          </ol>
        ) : null}
        {waiting ? <LoadingLine text="AI 助手正在处理…" /> : null}
      </div>

      <form className="job-chat-form" onSubmit={sendMessage}>
        <label htmlFor={composerId} className="field-label">描述你想做的任务</label>
        <textarea
          id={composerId}
          aria-describedby={hintId}
          className="textarea"
          value={draft}
          rows={2}
          maxLength={MAX_MESSAGE_LENGTH}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={handleComposerKey}
          readOnly={waiting}
          placeholder="把这批图压成 512 的 WebP"
        />
        <div className="job-chat-composer-actions">
          <span className="field-hint" id={hintId}>仅保留最近 {MAX_MESSAGES} 条消息</span>
          <button type="submit" className="btn btn--primary" disabled={!draft.trim() || waiting || submitting}>
            {waiting ? "发送中…" : "发送"}
          </button>
        </div>
      </form>

      {error ? <ErrorBox error={error} /> : null}
      {stalePreview ? (
        <p className="job-chat-note" role="status">文件列表已更新。请重新发送需求，生成新的任务预览。</p>
      ) : null}

      {preview ? (
        <div className="job-chat-preview">
          <div className="job-chat-preview-head">
            <h4>任务预览</h4>
            <span className="job-chat-count">
              {preview.task_count === null ? "待确定任务数" : formatNumber(preview.task_count) + " " + (UNIT_LABELS[unit] ?? unit)}
            </span>
          </div>
          <dl className="job-chat-preview-fields">
            <div>
              <dt>工作负载</dt>
              <dd>{WORKLOAD_LABELS[preview.workload] ?? preview.workload} <code>{preview.workload}</code></dd>
            </div>
            {Object.entries(preview.params).map(([name, value]) => (
              <div key={name}>
                <dt>{PARAM_LABELS[name] ?? name}</dt>
                <dd className="mono">{typeof value === "string" ? value : JSON.stringify(value)}</dd>
              </div>
            ))}
          </dl>
          {preview.note ? <p className="job-chat-note">{preview.note}</p> : null}
          <div className="job-chat-preview-actions">
            {fileHint ? <span className="field-hint">{fileHint}</span> : null}
            <button
              type="button"
              className="btn btn--primary"
              disabled={Boolean(fileHint) || submitting || waiting}
              onClick={confirmPreview}
            >
              {confirming ? "提交中…" : "确认提交"}
            </button>
          </div>
        </div>
      ) : null}

      {toolTrace?.length ? (
        <details className="job-chat-trace">
          <summary>工具调用记录（{toolTrace.length}）</summary>
          <ol>
            {toolTrace.map((step, index) => (
              <li key={index}>
                <div className="job-chat-tool-head">
                  <code>{step.name}</code>
                  <span className={step.ok ? "job-chat-tool-ok" : "job-chat-tool-error"}>{step.ok ? "成功" : "失败"}</span>
                </div>
                {step.summary ? <p>{step.summary}</p> : null}
                {Object.keys(step.args).length > 0 ? <pre>{JSON.stringify(step.args, null, 2)}</pre> : null}
              </li>
            ))}
          </ol>
        </details>
      ) : null}
    </section>
  );
}
