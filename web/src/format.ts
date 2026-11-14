import type { JobState, JobSummary, JsonSchemaObject, TaskState, WorkerState } from "./api";

/** 时间/数值/状态的展示辅助函数，纯函数，方便单测。 */

export const JOB_STATE_LABELS: Record<JobState, string> = {
  queued: "排队中",
  running: "运行中",
  succeeded: "已完成",
  partial: "部分成功",
  failed: "失败",
  canceled: "已取消",
};

export const TASK_STATE_LABELS: Record<TaskState, string> = {
  queued: "等待",
  leased: "执行中",
  succeeded: "完成",
  failed: "失败",
};

export const WORKER_STATE_LABELS: Record<WorkerState, string> = {
  online: "在线",
  draining: "排空中",
  offline: "离线",
};

export function isTerminalJobState(state: JobState | undefined): boolean {
  if (!state) return false;
  return state === "succeeded" || state === "partial" || state === "failed" || state === "canceled";
}

export function formatNumber(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return value.toLocaleString("zh-CN", { maximumFractionDigits: digits, minimumFractionDigits: 0 });
}

export function formatPct(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return value.toFixed(1) + "%";
}

export function clampPct(value: number | null | undefined): number | null {
  if (value === null || value === undefined || !Number.isFinite(value)) return null;
  return Math.max(0, Math.min(100, value));
}

/** 0.31 → "310 ms"；41.2 → "41.2 s"；125 → "2 分 05 秒"。 */
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds) || seconds < 0) return "—";
  if (seconds === 0) return "0 ms";
  if (seconds < 1) return Math.round(seconds * 1000) + " ms";
  if (seconds < 60) return seconds.toFixed(2) + " s";
  const minutes = Math.floor(seconds / 60);
  const rest = Math.floor(seconds % 60);
  return minutes + " 分 " + String(rest).padStart(2, "0") + " 秒";
}

export function formatEta(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds) || seconds < 0) return "—";
  if (seconds < 1) return "即将完成";
  return "约 " + formatDuration(seconds);
}

export function formatRelativeSeconds(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds) || seconds < 0) return "—";
  if (seconds < 2) return "刚刚";
  if (seconds < 60) return Math.round(seconds) + " 秒前";
  if (seconds < 3600) return Math.round(seconds / 60) + " 分钟前";
  if (seconds < 86400) return Math.round(seconds / 3600) + " 小时前";
  return Math.round(seconds / 86400) + " 天前";
}

/** Unix epoch 秒 → 本地时间字符串。 */
export function formatUnix(ts: number | null | undefined): string {
  if (ts === null || ts === undefined || !Number.isFinite(ts) || ts <= 0) return "—";
  return new Date(ts * 1000).toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

export function formatClock(ts: number | null | undefined): string {
  if (ts === null || ts === undefined || !Number.isFinite(ts)) return "—";
  return new Date(ts).toLocaleTimeString("zh-CN", { hour12: false });
}

export interface JobProgress {
  done: number;
  total: number;
  pct: number;
}

export function jobProgress(job: Pick<JobSummary, "total" | "succeeded" | "failed">): JobProgress {
  const total = job.total ?? 0;
  const done = (job.succeeded ?? 0) + (job.failed ?? 0);
  return { done, total, pct: total > 0 ? Math.min(100, (done / total) * 100) : 0 };
}

export function sharePct(part: number | null | undefined, total: number | null | undefined): number | null {
  if (part === null || part === undefined || total === null || total === undefined) return null;
  if (!Number.isFinite(part) || !Number.isFinite(total) || total <= 0) return null;
  return (part / total) * 100;
}

/* ----------------------------- workload 参数辅助 ---------------------------- */

function defaultValueFor(prop: JsonSchemaObject): unknown {
  if (Object.prototype.hasOwnProperty.call(prop, "default")) return prop.default;
  if (Array.isArray(prop.enum) && prop.enum.length > 0) return prop.enum[0];
  const type = Array.isArray(prop.type) ? prop.type[0] : prop.type;
  switch (type) {
    case "integer":
    case "number":
      return typeof prop.minimum === "number" ? prop.minimum : 0;
    case "boolean":
      return false;
    case "string":
      return "";
    case "array":
      return [];
    case "object":
      return prop.properties ? defaultParamsFromSchema(prop) : {};
    default:
      return null;
  }
}

/** 按 params_schema 生成参数默认值（优先 schema.default，其次 enum[0] / 类型零值）。 */
export function defaultParamsFromSchema(schema: JsonSchemaObject | undefined): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  if (!schema?.properties) return out;
  for (const [key, prop] of Object.entries(schema.properties)) {
    if (typeof prop !== "object" || prop === null) continue;
    out[key] = defaultValueFor(prop);
  }
  return out;
}

export interface SchemaFieldHint {
  name: string;
  type: string;
  description: string;
  defaultValue: unknown;
}

export function describeSchemaFields(schema: JsonSchemaObject | undefined): SchemaFieldHint[] {
  if (!schema?.properties) return [];
  const hints: SchemaFieldHint[] = [];
  for (const [name, prop] of Object.entries(schema.properties)) {
    if (typeof prop !== "object" || prop === null) continue;
    const type = Array.isArray(prop.type) ? prop.type.join(" | ") : prop.type ?? "any";
    const enumText = Array.isArray(prop.enum) && prop.enum.length > 0 ? " 可选值：" + prop.enum.map(String).join(" / ") : "";
    hints.push({
      name,
      type,
      description: (prop.description ?? prop.title ?? "") + enumText,
      defaultValue: defaultValueFor(prop),
    });
  }
  return hints;
}
