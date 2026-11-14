import { useRef, useState } from "react";
import type { AgentPreview, ApiClient, DatasetSummary, JobSpec } from "../api";
import { workloadLabel } from "../presets";
import { ErrorBox } from "./ui";

/** 预览只描述计划；用户核对实际数据来源后，才上传文件并创建任务。 */
export function TaskPreview({ api, preview, files, dataset, requiredTag, existingJobId, messageId }: {
  api: ApiClient;
  preview: AgentPreview;
  files: File[];
  dataset?: DatasetSummary;
  requiredTag: string;
  existingJobId: string | null;
  messageId?: string;
}) {
  const [submitting, setSubmitting] = useState(false);
  const storageKey = messageId ? `cef.preview.job:${api.baseUrl}:${messageId}` : null;
  const [submittedId, setSubmittedId] = useState<string | null>(() => {
    try { return storageKey ? sessionStorage.getItem(storageKey) : null; }
    catch { return null; }
  });
  const [error, setError] = useState<unknown>(null);
  const pendingRef = useRef(false);
  // 共享历史中的预览绑定原数据集，不能静默换成当前用户选的另一个数据集。
  const datasetName = preview.dataset || dataset?.name;
  const datasetCount = dataset && dataset.name === datasetName ? dataset.count : null;
  const count = datasetName ? datasetCount : files.length;
  const jobId = existingJobId || submittedId;
  const invalidFiles = !datasetName && (files.length === 0 || (preview.workload === "blender" && files.length !== 1));

  async function submit(): Promise<void> {
    if (pendingRef.current || jobId || invalidFiles || !api.hasToken) return;
    pendingRef.current = true;
    setSubmitting(true);
    setError(null);
    const spec: JobSpec = {
      workload: preview.workload,
      params: preview.params,
      required_tag: requiredTag || null,
      inputs: datasetName ? [] : files.map(({ name }) => ({ name })),
      ...(datasetName ? { dataset: datasetName } : {}),
    };
    try {
      const result = await api.submitJob(spec, datasetName ? [] : files);
      setSubmittedId(result.job_id);
      // 返回助手或刷新页面后仍显示已提交结果，避免重复点击同一份预览。
      try { if (storageKey) sessionStorage.setItem(storageKey, result.job_id); }
      catch { /* 存储禁用时保留当前组件中的结果。 */ }
    } catch (cause) {
      setError(cause);
    } finally {
      pendingRef.current = false;
      setSubmitting(false);
    }
  }

  return <div className="job-chat-preview">
    <div className="job-chat-preview-head">
      <span>{workloadLabel(preview.workload)}</span>
      <span>{preview.task_count === null ? "任务数待定" : `助手预计 ${preview.task_count} 个任务`}</span>
    </div>
    <code className="job-chat-preview-params">{JSON.stringify(preview.params)}</code>
    {preview.note ? <p>{preview.note}</p> : null}
    {jobId ? <p role="status">已提交任务：<a href={"#/jobs/" + encodeURIComponent(jobId)}>查看新任务详情</a></p> : <>
      <p>本次提交：{datasetName ? `数据集 ${datasetName}` : `当前所选 ${files.length} 个文件`}
        {datasetName && count !== null ? `（${count} 个文件）` : ""}；设备标签：{requiredTag || "不限制"}。</p>
      {!datasetName && files.length > 0 ? <p className="field-hint">{files.slice(0, 5).map((file) => file.name).join("、")}{files.length > 5 ? "…" : ""}</p> : null}
      {preview.workload !== "blender" && count !== null && preview.task_count !== null && count !== preview.task_count
        ? <p className="field-hint">实际输入为 {count} 个文件，任务数以本次输入为准。</p> : null}
      {invalidFiles ? <p className="field-hint">{preview.workload === "blender" ? "请先上传一个 .blend 文件，或选择对应的数据集。" : "请先上传待处理文件，或选择已登记数据集。"}</p> : null}
      {error ? <ErrorBox error={error} compact /> : null}
      <button type="button" className="btn btn--sm btn--primary" disabled={submitting || invalidFiles || !api.hasToken} onClick={() => void submit()}>
        {submitting ? "正在提交…" : "确认创建任务"}
      </button>
    </>}
  </div>;
}
