import { useRef, useState } from "react";
import type { ChangeEvent, DragEvent, FormEvent } from "react";
import type { ApiClient, JobSpec, Workload } from "../api";
import { CefApiError } from "../api";
import { defaultParamsFromSchema, describeSchemaFields } from "../format";
import type { PollState } from "../hooks/usePolling";
import { JobChat } from "./JobChat";
import { ErrorBox, Section } from "./ui";
import "../submit.css";

const WORKLOADS: Workload[] = [
  { kind: "thumbnail", available_on: [], unit: "images" },
  { kind: "infer", available_on: [], unit: "images" },
  { kind: "blender", available_on: [], unit: "frames" },
];

const WORKLOAD_LABELS: Record<string, string> = {
  thumbnail: "图片缩略图",
  infer: "图片目标检测",
  blender: "Blender 动画渲染",
};

const PRESETS = [
  {
    kind: "thumbnail",
    title: "网页缩略图 512",
    description: "长边 512 px · WebP",
    params: { size: 512, fmt: "webp", quality: 80 },
  },
  {
    kind: "infer",
    title: "批量检测 640",
    description: "输入 640 px · YOLO11n",
    params: { imgsz: 640, model: "yolo11n" },
  },
  {
    kind: "blender",
    title: "动画渲染 1080p",
    description: "原始分辨率 · 第 1–10 帧",
    params: { resolution: 100, frames: "1-10" },
  },
] satisfies { kind: string; title: string; description: string; params: Record<string, unknown> }[];

type MainField =
  | { name: string; label: string; defaultValue: string; type: "text" }
  | { name: string; label: string; defaultValue: number; type: "number"; min: number; max: number };

const MAIN_FIELDS: Record<string, MainField> = {
  thumbnail: { name: "size", label: "最长边（px）", defaultValue: 256, type: "number", min: 32, max: 2048 },
  infer: { name: "imgsz", label: "检测输入尺寸（px）", defaultValue: 640, type: "number", min: 320, max: 1280 },
  blender: { name: "frames", label: "帧范围", defaultValue: "1-10", type: "text" },
};

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

function parseParams(text: string): Record<string, unknown> | null {
  try {
    const parsed: unknown = text.trim() ? JSON.parse(text) : {};
    return typeof parsed === "object" && parsed !== null && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : null;
  } catch {
    return null;
  }
}

export function JobSubmitForm({ api, workloads, onSubmitted }: Props) {
  const workloadList = workloads.data?.length ? workloads.data : WORKLOADS;
  const [kind, setKind] = useState("thumbnail");
  const [paramsDraft, setParamsDraft] = useState<{ kind: string; text: string } | null>(null);
  const [preset, setPreset] = useState<string | null>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [requiredTag, setRequiredTag] = useState("");
  const [jobName, setJobName] = useState("");
  const [submission, setSubmission] = useState<"manual" | "chat" | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [dragging, setDragging] = useState(false);
  const submitLock = useRef(false);

  const selected = workloadList.find((item) => item.kind === kind) ?? workloadList[0];
  const defaults = defaultParamsFromSchema(selected.params_schema);
  const paramsText = paramsDraft?.kind === selected.kind ? paramsDraft.text : JSON.stringify(defaults, null, 2);
  const params = parseParams(paramsText);
  const field = MAIN_FIELDS[selected.kind];
  const fieldValue = field && params ? (params[field.name] ?? field.defaultValue) : "";
  const schemaHints = describeSchemaFields(selected.params_schema);
  const totalSize = files.reduce((sum, file) => sum + file.size, 0);

  function changeWorkload(nextKind: string) {
    setKind(nextKind);
    setParamsDraft(null);
    setPreset(null);
    setError(null);
  }

  function choosePreset(template: (typeof PRESETS)[number]) {
    const schema = workloadList.find((item) => item.kind === template.kind)?.params_schema;
    const nextParams = { ...defaultParamsFromSchema(schema), ...template.params };
    setKind(template.kind);
    setParamsDraft({ kind: template.kind, text: JSON.stringify(nextParams, null, 2) });
    setPreset(template.kind);
    setError(null);
  }

  function changeParams(text: string) {
    setParamsDraft({ kind: selected.kind, text });
    setPreset(null);
    setError(null);
  }

  function changeMainField(event: ChangeEvent<HTMLInputElement>) {
    if (!field || !params) return;
    const { value, valueAsNumber } = event.target;
    changeParams(JSON.stringify({
      ...params,
      [field.name]: field.type === "number" && value !== "" ? valueAsNumber : value,
    }, null, 2));
  }

  function addFiles(incoming: FileList | File[]) {
    if (submitLock.current || incoming.length === 0) return;
    const picked = Array.from(incoming);
    setFiles((previous) => {
      const byNameAndSize = new Map(previous.map((file) => [file.name + ":" + file.size, file]));
      for (const file of picked) byNameAndSize.set(file.name + ":" + file.size, file);
      return Array.from(byNameAndSize.values());
    });
    setError(null);
  }

  function handleFileInput(event: ChangeEvent<HTMLInputElement>) {
    if (event.target.files) addFiles(event.target.files);
    event.target.value = "";
  }

  function handleDrop(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    setDragging(false);
    addFiles(event.dataTransfer.files);
  }

  function removeFile(index: number) {
    if (submitLock.current) return;
    setFiles((previous) => previous.filter((_, itemIndex) => itemIndex !== index));
    setError(null);
  }

  async function submitJob(spec: JobSpec, source: "manual" | "chat") {
    if (submitLock.current) throw new CefApiError("conflict", "有一批任务正在提交，请稍候。");
    submitLock.current = true;
    setSubmission(source);
    try {
      return await api.submitJob(spec, files);
    } finally {
      submitLock.current = false;
      setSubmission(null);
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitLock.current) return;
    setError(null);

    if (!files.length) {
      setError(new CefApiError("invalid", "请先选择输入文件。"));
      return;
    }
    if (selected.kind === "blender" && (files.length !== 1 || !/\.blend$/i.test(files[0].name))) {
      setError(new CefApiError("invalid", "动画渲染需要上传一个 .blend 文件。"));
      return;
    }
    if (!params) {
      setError(new CefApiError("invalid", "请在高级参数中输入有效的 JSON 对象。"));
      return;
    }

    const spec: JobSpec = {
      workload: selected.kind,
      params,
      inputs: files.map((file) => ({ name: file.name })),
    };
    if (jobName.trim()) spec.name = jobName.trim();
    if (requiredTag.trim()) spec.required_tag = requiredTag.trim();

    try {
      const response = await submitJob(spec, "manual");
      onSubmitted(response.job_id);
    } catch (cause) {
      setError(cause);
    }
  }

  return (
    <Section title="提交任务" subtitle="选好文件，说一句你想怎么处理。">
      <div className="submit-layout">
        <div className="submit-files">
          <label
            className={"dropzone" + (dragging ? " dropzone--active" : "")}
            onDragOver={(event) => {
              event.preventDefault();
              if (!submission) setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={handleDrop}
          >
            <input
              className="file-input"
              type="file"
              multiple
              onChange={handleFileInput}
              disabled={submission !== null}
              aria-labelledby="submit-file-label"
              aria-describedby="submit-file-hint"
            />
            <span className="dropzone-title" id="submit-file-label">选择输入文件</span>
            <span className="dropzone-hint" id="submit-file-hint">拖入一批图片或一个 .blend 文件，也可以点击选择。</span>
          </label>
          <div className="submit-file-count" role="status">
            {files.length ? `${files.length} 个文件 · ${formatBytes(totalSize)}` : "尚未选择文件"}
          </div>
          {files.length > 0 ? (
            <ul className="file-list">
              {files.map((file, index) => (
                <li key={file.name + ":" + file.size} className="file-item">
                  <span className="file-name" title={file.name}>{file.name}</span>
                  <span className="file-size">{formatBytes(file.size)}</span>
                  <button
                    type="button"
                    className="btn btn--sm btn--ghost"
                    aria-label={`移除 ${file.name}`}
                    disabled={submission !== null}
                    onClick={() => removeFile(index)}
                  >
                    移除
                  </button>
                </li>
              ))}
            </ul>
          ) : null}
        </div>

        <JobChat
          api={api}
          files={files}
          workloads={workloadList}
          submitJob={(spec) => submitJob(spec, "chat")}
          submitting={submission !== null}
          onSubmitted={onSubmitted}
        />

        <form className="job-form submit-manual" onSubmit={handleSubmit} aria-labelledby="submit-manual-title">
          <h3 id="submit-manual-title">手动提交</h3>
          <div className="submit-presets" aria-label="常用模板" role="group">
            {PRESETS.map((template) => (
              <button
                key={template.kind}
                type="button"
                className="submit-preset"
                aria-pressed={preset === template.kind}
                disabled={submission !== null || !workloadList.some((item) => item.kind === template.kind)}
                onClick={() => choosePreset(template)}
              >
                <span>{template.title}</span>
                <small>{template.description}</small>
              </button>
            ))}
          </div>
          {error ? <ErrorBox error={error} /> : null}

          <div className="submit-basic">
            <label className="field">
              <span className="field-label">处理类型</span>
              <select className="select" value={selected.kind} onChange={(event) => changeWorkload(event.target.value)}>
                {workloadList.map((item) => (
                  <option key={item.kind} value={item.kind}>{WORKLOAD_LABELS[item.kind] ?? item.kind}</option>
                ))}
              </select>
            </label>
            {field ? (
              <label className="field">
                <span className="field-label">{field.label}</span>
                <input
                  className="input"
                  type={field.type}
                  min={field.type === "number" ? field.min : undefined}
                  max={field.type === "number" ? field.max : undefined}
                  value={typeof fieldValue === "string" || typeof fieldValue === "number" ? fieldValue : ""}
                  onChange={changeMainField}
                  disabled={!params}
                  required
                />
              </label>
            ) : null}
          </div>
          {preset === "blender" ? (
            <p className="field-hint submit-preset-note">使用场景原始分辨率。要输出 1080p，请将 .blend 场景设为 1920 × 1080。</p>
          ) : null}

          <details className="submit-advanced">
            <summary>高级参数</summary>
            <div className="submit-advanced-body">
              <div className="submit-basic">
                <label className="field">
                  <span className="field-label">任务名称（可选）</span>
                  <input className="input" value={jobName} onChange={(event) => setJobName(event.target.value)} maxLength={120} />
                </label>
                <label className="field">
                  <span className="field-label">指定设备标签（可选）</span>
                  <input className="input" value={requiredTag} onChange={(event) => setRequiredTag(event.target.value)} maxLength={64} />
                </label>
              </div>
              <div className="form-row form-row--params">
                <label className="field">
                  <span className="field-label">参数（JSON）</span>
                  <textarea
                    className="textarea mono"
                    rows={6}
                    value={paramsText}
                    onChange={(event) => changeParams(event.target.value)}
                    spellCheck={false}
                    aria-invalid={params === null}
                  />
                  {params === null ? <span className="field-hint">请输入有效的 JSON 对象。</span> : null}
                </label>
                {schemaHints.length ? (
                  <div className="schema-hints">
                    <div className="schema-hints-title">参数说明</div>
                    <ul>
                      {schemaHints.map((hint) => (
                        <li key={hint.name}>
                          <code>{hint.name}</code>
                          <span className="schema-default">默认 {JSON.stringify(hint.defaultValue ?? null)}</span>
                          {hint.description ? <span className="schema-desc">{hint.description}</span> : null}
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null}
              </div>
            </div>
          </details>

          <div className="form-actions">
            <button type="submit" className="btn" disabled={submission !== null || files.length === 0}>
              {submission === "manual" ? "提交中…" : "提交任务"}
            </button>
            <span className="form-hint">
              {files.length === 0 ? "请先选择输入文件。" : "提交后可在任务详情查看进度。"}
            </span>
          </div>
        </form>
      </div>
    </Section>
  );
}
