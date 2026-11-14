/**
 * CEF API v1 客户端。
 * 契约唯一来源：docs/api.md（冻结版）。页面只允许通过本文件访问后端。
 *
 * 设计要点：
 * - 所有类型定义集中在本文件，组件不重复声明接口形状；
 * - 响应做防御式归一化：后端缺字段 / 404 / 返回 HTML 时也不会让界面崩掉；
 * - 错误统一为 CefApiError，toUserMessage() 给出明确中文提示。
 */

/* ---------------------------------- 类型 ---------------------------------- */

export type JobState = "queued" | "running" | "succeeded" | "partial" | "failed" | "canceled";
export type TaskState = "queued" | "leased" | "succeeded" | "failed" | "canceled";
export type WorkerState = "online" | "draining" | "offline";

/** JSON Schema 子集（workloads.params_schema），只声明界面会用到的字段。 */
export interface JsonSchemaObject {
  type?: string | string[];
  title?: string;
  description?: string;
  default?: unknown;
  enum?: unknown[];
  minimum?: number;
  maximum?: number;
  items?: JsonSchemaObject;
  properties?: Record<string, JsonSchemaObject>;
  required?: string[];
  [key: string]: unknown;
}

export interface Worker {
  worker_id: string;
  name: string;
  hostname?: string;
  os?: string;
  cpu_count?: number;
  tags: string[];
  slots?: number;
  state: WorkerState;
  last_seen_s_ago: number | null;
  cpu_pct: number | null;
  mem_pct: number | null;
  gpu_pct: number | null;
  running?: number;
  completed_total?: number;
  mean_duration_s: number | null;
  throughput_per_min?: number;
}

export interface JobSummary {
  job_id: string;
  name?: string;
  workload?: string;
  state: JobState;
  total?: number;
  succeeded?: number;
  failed?: number;
  queued?: number;
  created_at?: number;
  finished_at?: number | null;
  eta_s?: number | null;
}

export interface PerWorkerStat {
  worker_id: string;
  name?: string;
  completed: number;
  mean_duration_s: number | null;
  total_duration_s: number | null;
  failed?: number;
  /** 后端给出的成功任务占比（0~1）；老版本可能没有该字段。 */
  share?: number;
}

export interface JobTask {
  task_id: string;
  idx: number;
  state: TaskState;
  worker?: string | null;
  worker_name?: string | null;
  attempt?: number;
  duration_s: number | null;
}

/** GET /api/v1/jobs/{job_id} 的完整响应。 */
export interface JobDetail extends JobSummary {
  per_worker: PerWorkerStat[];
  tasks: JobTask[];
}

export interface StatsJobs {
  [key: string]: number | undefined;
  total?: number;
  queued?: number;
  running?: number;
  succeeded?: number;
  failed?: number;
  partial?: number;
  canceled?: number;
}

export interface StatsWorker {
  worker_id: string;
  util_series?: { ts: number; cpu_pct?: number | null; gpu_pct?: number | null }[];
}

export interface StatsResponse {
  now?: number;
  jobs: StatsJobs;
  tasks_per_min: number | null;
  p50_duration_s: number | null;
  workers: StatsWorker[];
  bottleneck: string | null;
  workers_online?: number | null;
}

export interface Workload {
  kind: string;
  available_on: string[];
  params_schema?: JsonSchemaObject;
  unit?: string;
}

export interface HealthResponse {
  ok: boolean;
  version?: string;
  uptime_s?: number;
  workers_online?: number;
}

/** POST /api/v1/jobs 的 multipart 中的 spec 字段。 */
export interface JobSpec {
  workload: string;
  params: Record<string, unknown>;
  name?: string;
  priority?: number;
  required_tag?: string | null;
  inputs: { name: string }[];
}

export interface SubmitResponse {
  job_id: string;
  total?: number;
  state?: JobState;
}

export interface DownloadPayload {
  blob: Blob;
  filename: string;
}

export interface ListJobsOptions {
  limit?: number;
  state?: JobState;
}

/* --------------------------------- 错误处理 -------------------------------- */

export type ApiErrorKind =
  | "network"
  | "unauthorized"
  | "not_found"
  | "conflict"
  | "invalid"
  | "server"
  | "http"
  | "parse"
  | "aborted";

export class CefApiError extends Error {
  readonly kind: ApiErrorKind;
  readonly status?: number;
  readonly code?: string;

  constructor(kind: ApiErrorKind, message: string, extra: { status?: number; code?: string } = {}) {
    super(message);
    this.name = "CefApiError";
    this.kind = kind;
    this.status = extra.status;
    this.code = extra.code;
  }
}

export function isAbortError(error: unknown): boolean {
  if (typeof error !== "object" || error === null) return false;
  return (error as { name?: unknown }).name === "AbortError";
}

export interface UserMessage {
  title: string;
  detail: string;
}

/** 把任意异常翻译成明确的中文提示，避免出现 "Failed to fetch" 之类的英文白屏。 */
export function toUserMessage(error: unknown): UserMessage {
  if (error instanceof CefApiError) {
    switch (error.kind) {
      case "network":
        return {
          title: "无法连接 Coordinator",
          detail: error.message + " 请确认后端已启动（默认 127.0.0.1:8080）、地址与端口正确，并且当前网络可达。",
        };
      case "unauthorized":
        return {
          title: "认证失败：Token 无效或缺失",
          detail: "请在顶部连接条填写与后端 CEF_TOKEN 一致的 Token，然后点「应用」。",
        };
      case "not_found":
        return {
          title: "接口不存在（404）",
          detail: "当前 Coordinator 可能尚未实现该接口。界面已按空状态渲染，不影响其它面板。",
        };
      case "conflict":
        return { title: "状态冲突（409）", detail: error.message };
      case "invalid":
        return { title: "请求参数被拒绝", detail: error.message };
      case "server":
        return { title: "Coordinator 内部错误", detail: error.message };
      case "parse":
        return {
          title: "响应格式无法解析",
          detail: error.message + " 若返回的是 HTML 页面，通常是该路径不存在或后端尚未挂载接口。",
        };
      case "aborted":
        return { title: "请求已取消", detail: "刷新页面或重试即可。" };
      case "http":
        return { title: "请求失败", detail: error.message };
    }
  }
  if (error instanceof Error) {
    return { title: "发生未知错误", detail: error.message };
  }
  return { title: "发生未知错误", detail: String(error) };
}

/* --------------------------------- 归一化工具 -------------------------------- */

const JOB_STATES: readonly JobState[] = ["queued", "running", "succeeded", "partial", "failed", "canceled"];
const TASK_STATES: readonly TaskState[] = ["queued", "leased", "succeeded", "failed", "canceled"];
const WORKER_STATES: readonly WorkerState[] = ["online", "draining", "offline"];

function isJobState(value: unknown): value is JobState {
  return typeof value === "string" && (JOB_STATES as readonly string[]).includes(value);
}

function isTaskState(value: unknown): value is TaskState {
  return typeof value === "string" && (TASK_STATES as readonly string[]).includes(value);
}

function isWorkerState(value: unknown): value is WorkerState {
  return typeof value === "string" && (WORKER_STATES as readonly string[]).includes(value);
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function asString(value: unknown): string | undefined {
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

function asNumber(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function asNumberOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function asStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === "string");
}

function isNotNull<T>(value: T | null): value is T {
  return value !== null;
}

function normalizeWorker(raw: unknown, index: number): Worker {
  const r = asRecord(raw) ?? {};
  const workerId = asString(r.worker_id) ?? asString(r.id) ?? "worker-" + index;
  return {
    worker_id: workerId,
    name: asString(r.name) ?? asString(r.hostname) ?? workerId,
    hostname: asString(r.hostname),
    os: asString(r.os),
    cpu_count: asNumber(r.cpu_count),
    tags: asStringArray(r.tags),
    slots: asNumber(r.slots),
    state: isWorkerState(r.state) ? r.state : "offline",
    last_seen_s_ago: asNumberOrNull(r.last_seen_s_ago),
    cpu_pct: asNumberOrNull(r.cpu_pct),
    mem_pct: asNumberOrNull(r.mem_pct),
    gpu_pct: asNumberOrNull(r.gpu_pct),
    running: asNumber(r.running),
    completed_total: asNumber(r.completed_total),
    mean_duration_s: asNumberOrNull(r.mean_duration_s),
    throughput_per_min: asNumber(r.throughput_per_min),
  };
}

function normalizeJobSummary(raw: unknown): JobSummary | null {
  const r = asRecord(raw);
  if (!r) return null;
  const jobId = asString(r.job_id) ?? asString(r.id);
  if (!jobId) return null;
  return {
    job_id: jobId,
    name: asString(r.name),
    workload: asString(r.workload),
    state: isJobState(r.state) ? r.state : "queued",
    total: asNumber(r.total),
    succeeded: asNumber(r.succeeded),
    failed: asNumber(r.failed),
    queued: asNumber(r.queued),
    created_at: asNumber(r.created_at),
    finished_at: asNumberOrNull(r.finished_at),
    eta_s: asNumberOrNull(r.eta_s),
  };
}

function normalizePerWorker(raw: unknown, index: number): PerWorkerStat | null {
  const r = asRecord(raw);
  if (!r) return null;
  const workerId = asString(r.worker_id) ?? "worker-" + index;
  return {
    worker_id: workerId,
    name: asString(r.name) ?? workerId,
    completed: asNumber(r.completed) ?? 0,
    mean_duration_s: asNumberOrNull(r.mean_duration_s),
    total_duration_s: asNumberOrNull(r.total_duration_s),
    failed: asNumber(r.failed),
    share: asNumber(r.share),
  };
}

function normalizeTask(raw: unknown, index: number): JobTask | null {
  const r = asRecord(raw);
  if (!r) return null;
  return {
    task_id: asString(r.task_id) ?? "task-" + index,
    idx: asNumber(r.idx) ?? index,
    state: isTaskState(r.state) ? r.state : "queued",
    worker: asString(r.worker) ?? null,
    worker_name: asString(r.worker_name) ?? null,
    attempt: asNumber(r.attempt),
    duration_s: asNumberOrNull(r.duration_s),
  };
}

function normalizeJobDetail(raw: unknown): JobDetail {
  const summary = normalizeJobSummary(raw);
  if (!summary) {
    throw new CefApiError("parse", "任务详情响应缺少 job_id 字段。");
  }
  const r = asRecord(raw) ?? {};
  const perWorkerRaw = Array.isArray(r.per_worker) ? r.per_worker : [];
  const tasksRaw = Array.isArray(r.tasks) ? r.tasks : [];
  return {
    ...summary,
    per_worker: perWorkerRaw.map(normalizePerWorker).filter(isNotNull),
    tasks: tasksRaw.map(normalizeTask).filter(isNotNull),
  };
}

function normalizeStatsWorker(raw: unknown, index: number): StatsWorker | null {
  const r = asRecord(raw);
  if (!r) return null;
  const seriesRaw = Array.isArray(r.util_series) ? r.util_series : [];
  const util_series = seriesRaw
    .map((point) => {
      const p = asRecord(point);
      if (!p) return null;
      const ts = asNumber(p.ts);
      if (ts === undefined) return null;
      return { ts, cpu_pct: asNumberOrNull(p.cpu_pct), gpu_pct: asNumberOrNull(p.gpu_pct) };
    })
    .filter(isNotNull);
  return {
    worker_id: asString(r.worker_id) ?? "worker-" + index,
    util_series,
  };
}

function normalizeStats(raw: unknown): StatsResponse {
  const r = asRecord(raw);
  if (!r) {
    throw new CefApiError("parse", "统计接口返回的不是 JSON 对象。");
  }
  const jobs: StatsJobs = {};
  const jobsRaw = asRecord(r.jobs);
  if (jobsRaw) {
    for (const [key, value] of Object.entries(jobsRaw)) {
      if (typeof value === "number" && Number.isFinite(value)) jobs[key] = value;
    }
  }
  // 兼容后端 PoolStats 的扁平字段（docs 里画的是 jobs:{...}，实现是 jobs_total 等）。
  const flatJobs: Array<[string, keyof StatsJobs]> = [
    ["jobs_total", "total"],
    ["jobs_running", "running"],
    ["tasks_succeeded", "succeeded"],
    ["tasks_failed", "failed"],
    ["tasks_queued", "queued"],
  ];
  for (const [sourceKey, targetKey] of flatJobs) {
    const value = asNumber(r[sourceKey]);
    if (value !== undefined && jobs[targetKey] === undefined) jobs[targetKey] = value;
  }
  const workersRaw = Array.isArray(r.workers) ? r.workers : [];
  return {
    now: asNumber(r.now),
    jobs,
    tasks_per_min: asNumberOrNull(r.tasks_per_min),
    p50_duration_s: asNumberOrNull(r.p50_duration_s),
    workers: workersRaw.map(normalizeStatsWorker).filter(isNotNull),
    bottleneck: asString(r.bottleneck) ?? null,
    workers_online: asNumberOrNull(r.workers_online),
  };
}

function normalizeWorkload(raw: unknown): Workload | null {
  const r = asRecord(raw);
  if (!r) return null;
  const kind = asString(r.kind);
  if (!kind) return null;
  const schema = asRecord(r.params_schema);
  return {
    kind,
    available_on: asStringArray(r.available_on),
    params_schema: schema ? (schema as JsonSchemaObject) : undefined,
    unit: asString(r.unit),
  };
}

function extractApiError(raw: unknown): { code?: string; message?: string } {
  const rec = asRecord(raw);
  const err = rec ? asRecord(rec.error) : null;
  if (!err) return {};
  return { code: asString(err.code), message: asString(err.message) };
}

function filenameFromDisposition(value: string | null): string | undefined {
  if (!value) return undefined;
  const star = /filename\*=UTF-8''([^;]+)/i.exec(value);
  if (star?.[1]) {
    try {
      return decodeURIComponent(star[1]);
    } catch {
      return star[1];
    }
  }
  const plain = /filename="?([^";]+)"?/i.exec(value);
  return plain?.[1];
}

/** 补全协议、去掉末尾斜杠与误粘贴的 /api/v1。 */
export function normalizeBaseUrl(raw: string): string {
  let value = raw.trim();
  if (!value) return "";
  if (!/^https?:\/\//i.test(value)) value = "http://" + value;
  value = value.replace(/\/+$/, "");
  value = value.replace(/\/api\/v1$/i, "");
  return value;
}

/* --------------------------------- 客户端 --------------------------------- */

export interface ApiClient {
  readonly baseUrl: string;
  readonly hasToken: boolean;
  getHealth(signal?: AbortSignal): Promise<HealthResponse>;
  getWorkers(signal?: AbortSignal): Promise<Worker[]>;
  getStats(windowSeconds?: number, signal?: AbortSignal): Promise<StatsResponse>;
  getWorkloads(signal?: AbortSignal): Promise<Workload[]>;
  listJobs(options?: ListJobsOptions, signal?: AbortSignal): Promise<JobSummary[]>;
  getJob(jobId: string, signal?: AbortSignal): Promise<JobDetail>;
  submitJob(spec: JobSpec, files: File[]): Promise<SubmitResponse>;
  cancelJob(jobId: string): Promise<void>;
  downloadResult(jobId: string): Promise<DownloadPayload>;
}

export function createApiClient(baseUrlRaw: string, tokenRaw: string): ApiClient {
  const baseUrl = normalizeBaseUrl(baseUrlRaw);
  const token = tokenRaw.trim();

  function authHeaders(extra?: HeadersInit): Headers {
    const headers = new Headers(extra);
    if (token) headers.set("Authorization", "Bearer " + token);
    return headers;
  }

  async function parseBody(res: Response): Promise<unknown> {
    const text = await res.text();
    if (!text) return null;
    try {
      return JSON.parse(text) as unknown;
    } catch {
      return text;
    }
  }

  function httpError(status: number, body: unknown): CefApiError {
    const { code, message } = extractApiError(body);
    const apiMessage = message ? "后端返回：" + message : "";
    switch (status) {
      case 401:
      case 403:
        return new CefApiError("unauthorized", "认证失败（HTTP " + status + "）。" + apiMessage, { status, code });
      case 404:
        return new CefApiError("not_found", "接口不存在（HTTP 404）。" + apiMessage, { status, code });
      case 409:
        return new CefApiError("conflict", "状态冲突（HTTP 409）。" + apiMessage, { status, code });
      case 400:
      case 422:
        return new CefApiError("invalid", "请求参数不合法（HTTP " + status + "）。" + apiMessage, { status, code });
      default:
        break;
    }
    if (status >= 500) {
      const proxyHint = message ? "" : "若通过开发代理访问，请确认 127.0.0.1:8080 的 Coordinator 已启动。";
      return new CefApiError("server", "Coordinator 返回 HTTP " + status + "。" + apiMessage + proxyHint, {
        status,
        code,
      });
    }
    return new CefApiError("http", "请求失败（HTTP " + status + "）。" + apiMessage, { status, code });
  }

  async function requestJson(path: string, init: RequestInit, signal?: AbortSignal): Promise<unknown> {
    let res: Response;
    try {
      res = await fetch(baseUrl + "/api/v1" + path, {
        ...init,
        headers: authHeaders(init.headers),
        signal,
      });
    } catch (error) {
      if (isAbortError(error)) throw new CefApiError("aborted", "请求已取消。");
      throw new CefApiError("network", "无法访问 " + (baseUrl || "（未配置地址）") + "。");
    }
    const body = await parseBody(res);
    if (!res.ok) throw httpError(res.status, body);
    if (typeof body === "string") {
      throw new CefApiError("parse", "服务端返回了非 JSON 内容（可能是 HTML 页面）。", { status: res.status });
    }
    return body;
  }

  return {
    baseUrl,
    hasToken: token.length > 0,

    async getHealth(signal?: AbortSignal): Promise<HealthResponse> {
      const raw = await requestJson("/health", { method: "GET" }, signal);
      const r = asRecord(raw);
      if (!r) throw new CefApiError("parse", "健康检查返回的不是 JSON 对象。");
      return {
        ok: r.ok !== false,
        version: asString(r.version),
        uptime_s: asNumber(r.uptime_s),
        workers_online: asNumber(r.workers_online),
      };
    },

    async getWorkers(signal?: AbortSignal): Promise<Worker[]> {
      const raw = await requestJson("/workers", { method: "GET" }, signal);
      const r = asRecord(raw);
      const list = r && Array.isArray(r.workers) ? r.workers : [];
      return list.map(normalizeWorker);
    },

    async getStats(windowSeconds = 300, signal?: AbortSignal): Promise<StatsResponse> {
      const raw = await requestJson("/stats?window=" + encodeURIComponent(String(windowSeconds)), { method: "GET" }, signal);
      return normalizeStats(raw);
    },

    async getWorkloads(signal?: AbortSignal): Promise<Workload[]> {
      const raw = await requestJson("/workloads", { method: "GET" }, signal);
      const r = asRecord(raw);
      const list = r && Array.isArray(r.workloads) ? r.workloads : [];
      return list.map(normalizeWorkload).filter(isNotNull);
    },

    async listJobs(options: ListJobsOptions = {}, signal?: AbortSignal): Promise<JobSummary[]> {
      const qs = new URLSearchParams();
      qs.set("limit", String(options.limit ?? 50));
      if (options.state) qs.set("state", options.state);
      const raw = await requestJson("/jobs?" + qs.toString(), { method: "GET" }, signal);
      const r = asRecord(raw);
      const list = r && Array.isArray(r.jobs) ? r.jobs : [];
      return list.map(normalizeJobSummary).filter(isNotNull);
    },

    async getJob(jobId: string, signal?: AbortSignal): Promise<JobDetail> {
      const raw = await requestJson("/jobs/" + encodeURIComponent(jobId), { method: "GET" }, signal);
      return normalizeJobDetail(raw);
    },

    async submitJob(spec: JobSpec, files: File[]): Promise<SubmitResponse> {
      const form = new FormData();
      form.append("spec", JSON.stringify(spec));
      for (const file of files) form.append("files", file, file.name);
      const raw = await requestJson("/jobs", { method: "POST", body: form });
      const r = asRecord(raw);
      if (!r) throw new CefApiError("parse", "提交接口返回的不是 JSON 对象。");
      const jobId = asString(r.job_id);
      if (!jobId) throw new CefApiError("parse", "提交成功但响应缺少 job_id 字段。");
      return {
        job_id: jobId,
        total: asNumber(r.total),
        state: isJobState(r.state) ? r.state : undefined,
      };
    },

    async cancelJob(jobId: string): Promise<void> {
      await requestJson("/jobs/" + encodeURIComponent(jobId) + "/cancel", { method: "POST" });
    },

    async downloadResult(jobId: string): Promise<DownloadPayload> {
      let res: Response;
      try {
        res = await fetch(baseUrl + "/api/v1/jobs/" + encodeURIComponent(jobId) + "/result", {
          method: "GET",
          headers: authHeaders(),
        });
      } catch (error) {
        if (isAbortError(error)) throw new CefApiError("aborted", "请求已取消。");
        throw new CefApiError("network", "无法访问 " + (baseUrl || "（未配置地址）") + "。");
      }
      if (!res.ok) {
        const body = await parseBody(res);
        throw httpError(res.status, body);
      }
      const blob = await res.blob();
      const filename =
        filenameFromDisposition(res.headers.get("content-disposition")) ?? jobId + ".zip";
      return { blob, filename };
    },
  };
}

/* --------------------------------- 设置存储 -------------------------------- */

export interface ConnectionSettings {
  baseUrl: string;
  token: string;
}

const STORAGE_BASE_URL = "cef.dashboard.baseUrl";
const STORAGE_TOKEN = "cef.dashboard.token";

function readStorage(key: string): string {
  try {
    return window.localStorage.getItem(key) ?? "";
  } catch {
    return "";
  }
}

export function loadSettings(): ConnectionSettings {
  const storedUrl = readStorage(STORAGE_BASE_URL);
  return {
    baseUrl: storedUrl || normalizeBaseUrl(window.location.origin),
    token: readStorage(STORAGE_TOKEN),
  };
}

export function saveSettings(settings: ConnectionSettings): void {
  try {
    window.localStorage.setItem(STORAGE_BASE_URL, normalizeBaseUrl(settings.baseUrl));
    window.localStorage.setItem(STORAGE_TOKEN, settings.token.trim());
  } catch {
    // 隐私模式 / 存储被禁用时静默失败，界面仍可用。
  }
}
