import { useEffect, useRef, useState } from "react";
import type { ChangeEvent, DragEvent, FormEvent } from "react";
import type { ApiClient, JobSpec, Workload } from "../api";
import { CefApiError, toUserMessage } from "../api";
import { defaultParamsFromSchema, describeSchemaFields } from "../format";
import type { PollState } from "../hooks/usePolling";
import { ErrorBox, Section } from "./ui";

const FALLBACK_WORKLOADS: Workload[] = [
  { kind: "thumbnail", available_on: [], unit: "images" },
  { kind: "infer", available_on: [], unit: "images" },
  { kind: "blender", available_on: [], unit: "frames" },
];

interface Props {
  api: ApiClient;
  workloads: PollState<Workload[]>;
  onSubmitted: (jobId: string) => void;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return bytes + " B";
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
  return (bytes / (1024 * 1024)).toFixed(2) + " MB";
}

export function JobSubmitForm({ api, workloads, onSubmitted }: Props) {
  const workloadList = workloads.data && workloads.data.length > 0 ? workloads.data : FALLBACK_WORKLOADS;
  const [kind, setKind] = useState(workloadList[0]?.kind ?? "thumbnail");
  const [paramsText, setParamsText] = useState("{}");
  const [files, setFiles] = useState<File[]>([]);
  const [requiredTag, setRequiredTag] = useState("");
  const [jobName, setJobName] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [dragging, setDragging] = useState(false);
  const lastKindRef = useRef("");
  const fileInputRef = useRef<HTMLInputElement>(null);

  const selected = workloadList.find((item) => item.kind === kind) ?? workloadList[0];

  // 首次拿到 workload（或切换 workload）时，按 params_schema 填充默认参数。
  // key 里带上数据来源：/workloads 迟到时要用真实 schema 覆盖内置兜底值，
  // 但同一来源的轮询刷新不能覆盖用户已经改过的参数。
  useEffect(() => {
    if (!selected) return;
    const source = workloads.data ? "live" : "fallback";
    const key = selected.kind + ":" + source;
    if (lastKindRef.current === key) return;
    lastKindRef.current = key;
    setParamsText(JSON.stringify(defaultParamsFromSchema(selected.params_schema), null, 2));
  }, [selected, workloads.data]);

  // 当前选中的 workload 不在最新列表里时回退到第一项。
  useEffect(() => {
    if (workloadList.length > 0 && !workloadList.some((item) => item.kind === kind)) {
      setKind(workloadList[0].kind);
    }
  }, [workloadList, kind]);

  const schemaHints = describeSchemaFields(selected?.params_schema);
  const totalSize = files.reduce((sum, file) => sum + file.size, 0);

  function addFiles(incoming: FileList | File[] | null) {
    if (!incoming) return;
    const picked = Array.from(incoming);
    if (picked.length === 0) return;
    setFiles((previous) => {
      const map = new Map<string, File>();
      for (const file of previous) map.set(file.name + ":" + file.size, file);
      for (const file of picked) map.set(file.name + ":" + file.size, file);
      return Array.from(map.values());
    });
    setError(null);
  }

  function handleFileInput(event: ChangeEvent<HTMLInputElement>) {
    addFiles(event.target.files);
    event.target.value = "";
  }

  function handleDrop(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    setDragging(false);
    addFiles(event.dataTransfer.files);
  }

  function handleDragOver(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    setDragging(true);
  }

  function removeFile(index: number) {
    setFiles((previous) => previous.filter((_, itemIndex) => itemIndex !== index));
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);

    if (files.length === 0) {
      setError(new CefApiError("invalid", "请至少选择一个输入文件（对应 spec.inputs）。"));
      return;
    }

    let parsed: unknown;
    try {
      parsed = paramsText.trim() ? (JSON.parse(paramsText) as unknown) : {};
    } catch (cause) {
      const reason = cause instanceof Error ? cause.message : String(cause);
      setError(new CefApiError("invalid", "参数不是合法 JSON：" + reason));
      return;
    }
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
      setError(new CefApiError("invalid", "参数必须是 JSON 对象，例如 {\"size\": 256}。"));
      return;
    }

    const spec: JobSpec = {
      workload: selected?.kind ?? kind,
      params: parsed as Record<string, unknown>,
      inputs: files.map((file) => ({ name: file.name })),
    };
    if (jobName.trim()) spec.name = jobName.trim();
    if (requiredTag.trim()) spec.required_tag = requiredTag.trim();

    setSubmitting(true);
    try {
      const response = await api.submitJob(spec, files);
      setFiles([]);
      setJobName("");
      onSubmitted(response.job_id);
    } catch (cause) {
      setError(cause);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Section
      title="提交任务"
      subtitle="POST /api/v1/jobs（multipart：spec + files）· 每个文件生成一个 Task，blender 按 params.frames 拆帧"
      actions={
        <a className="btn btn--sm btn--ghost" href="#/jobs">
          任务列表
        </a>
      }
    >
      {workloads.error ? (
        <div className="notice notice--warn">
          工作负载接口不可用（{toUserMessage(workloads.error).title}），已回退到内置列表 thumbnail / infer / blender。
        </div>
      ) : null}

      {error ? <ErrorBox error={error} /> : null}

      <form className="job-form" onSubmit={handleSubmit}>
        <div className="form-row">
          <label className="field">
            <span className="field-label">工作负载</span>
            <select className="select" value={selected?.kind ?? kind} onChange={(event) => setKind(event.target.value)}>
              {workloadList.map((item) => (
                <option key={item.kind} value={item.kind}>
                  {item.kind}
                  {item.unit ? " · " + item.unit : ""}
                </option>
              ))}
            </select>
            {selected && selected.available_on.length > 0 ? (
              <span className="field-hint">可用设备：{selected.available_on.join("、")}</span>
            ) : null}
          </label>

          <label className="field">
            <span className="field-label">任务名称（可选）</span>
            <input
              className="input"
              value={jobName}
              onChange={(event) => setJobName(event.target.value)}
              placeholder="例如 demo-batch-01"
              maxLength={120}
            />
          </label>

          <label className="field">
            <span className="field-label">required_tag（可选）</span>
            <input
              className="input"
              value={requiredTag}
              onChange={(event) => setRequiredTag(event.target.value)}
              placeholder="例如 gpu / beelink"
              maxLength={64}
            />
            <span className="field-hint">仅允许带该 tag 的 Worker 领取，用于基准测试。</span>
          </label>
        </div>

        <div className="form-row form-row--params">
          <label className="field field--grow">
            <span className="field-label">参数（JSON）</span>
            <textarea
              className="textarea mono"
              rows={10}
              value={paramsText}
              onChange={(event) => setParamsText(event.target.value)}
              spellCheck={false}
            />
            <span className="field-hint">
              {schemaHints.length > 0
                ? "已按 params_schema 预填默认值，可手动修改；提交后端会再次校验。"
                : "该工作负载未声明参数；如需参数请填写合法 JSON 对象，提交后端会校验。"}
            </span>
          </label>

          <div className="schema-hints">
            <div className="schema-hints-title">参数说明</div>
            {schemaHints.length === 0 ? (
              <p className="schema-hints-empty">该工作负载未声明参数（或 /workloads 接口不可用）。</p>
            ) : (
              <ul>
                {schemaHints.map((hint) => (
                  <li key={hint.name}>
                    <code>{hint.name}</code>
                    <span className="schema-type">{hint.type}</span>
                    {hint.description ? <span className="schema-desc">{hint.description}</span> : null}
                    <span className="schema-default">默认 {JSON.stringify(hint.defaultValue ?? null)}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>

        <div className="form-row">
          <label
            className={"dropzone" + (dragging ? " dropzone--active" : "")}
            onDragOver={handleDragOver}
            onDragLeave={() => setDragging(false)}
            onDrop={handleDrop}
          >
            <input ref={fileInputRef} className="file-input" type="file" multiple onChange={handleFileInput} />
            <span className="dropzone-title">选择输入文件</span>
            <span className="dropzone-hint">
              可多选，也可拖拽到此处；顺序与 spec.inputs 对应。当前 {files.length} 个文件 · {formatBytes(totalSize)}
            </span>
          </label>
        </div>

        {files.length > 0 ? (
          <ul className="file-list">
            {files.map((file, index) => (
              <li key={file.name + ":" + file.size + ":" + index} className="file-item">
                <span className="file-name" title={file.name}>
                  {file.name}
                </span>
                <span className="file-size">{formatBytes(file.size)}</span>
                <button type="button" className="btn btn--sm btn--ghost" onClick={() => removeFile(index)}>
                  移除
                </button>
              </li>
            ))}
          </ul>
        ) : null}

        <div className="form-actions">
          <button type="submit" className="btn btn--primary" disabled={submitting || files.length === 0}>
            {submitting ? "提交中…" : "提交任务"}
          </button>
          <span className="form-hint">
            {files.length === 0 ? "请先选择至少一个输入文件。" : "提交成功后自动跳转到任务详情页。"}
          </span>
        </div>
      </form>
    </Section>
  );
}
