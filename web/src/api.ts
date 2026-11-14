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

/** 一个输入/输出文件的引用（GET /api/v1/jobs/{id} 的 tasks[].inputs|outputs）。 */
export interface BlobBrief {
  sha256: string;
  name: string;
  size: number;
  /** 可直接用同一 token 拉取的相对路径，通常是 /api/v1/blobs/{sha256}。 */
  url: string;
}

export interface JobTask {
  task_id: string;
  idx: number;
  state: TaskState;
  worker?: string | null;
  worker_name?: string | null;
  attempt?: number;
  duration_s: number | null;
  error?: string;
  started_at?: number | null;
  finished_at?: number | null;
  output_sha256?: string;
  /** 该子任务的输入文件（展开行时用）。 */
  inputs: BlobBrief[];
  /** 该子任务的产物（展开行时用）。 */
  outputs: BlobBrief[];
  /** 后端给出的一句话结果摘要。 */
  summary?: string;
}

/** GET /api/v1/jobs/{job_id} 的完整响应。 */
export interface JobDetail extends JobSummary {
  /** 人类可读的参数描述，例如「长边 512px，WebP，质量 80」。 */
  params_text: string;
  /** 输入文件摘要，例如「16 张图片，共 3.2 MB」。 */
  input_summary: string;
  /** 提交者昵称。 */
  submitter: string | null;
  /** 提交时指定的设备标签（若有）。 */
  required_tag?: string | null;
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
  /** 累计成功任务数（与上面的窗口值不同） */
  succeeded_total?: number;
  /** 累计失败任务数 */
  failed_total?: number;
  /** 累计重试次数 */
  retries_total?: number;
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

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

export interface AgentChatRequest {
  messages: ChatMessage[];
  context: { files: { name: string; size: number }[] };
  /** 发送者昵称（可空），服务端落库时作为消息作者。 */
  author?: string;
  /** 共享对话房间，默认 pool。 */
  room?: string;
}

/** 服务端落库的一条共享对话消息（GET /api/v1/chat/messages）。 */
export interface ServerChatMessage {
  id: string;
  room: string;
  role: "user" | "assistant";
  author: string;
  content: string;
  trace: AgentToolTrace[] | null;
  preview: AgentPreview | null;
  job_id: string | null;
  created_at: number;
}

/** GET /api/v1/chat/messages 的响应。latest 是最新一条的 created_at（无消息为 0）。 */
export interface ChatPage {
  messages: ServerChatMessage[];
  latest: number;
}

export interface AgentPreview {
  workload: string;
  params: Record<string, unknown>;
  task_count: number | null;
  note: string;
}

export interface AgentToolTrace {
  name: string;
  args: Record<string, unknown>;
  ok: boolean;
  summary: string;
}

export interface AgentChatResponse {
  reply: string;
  preview: AgentPreview | null;
  tool_trace: AgentToolTrace[];
  model?: string;
  usage: Record<string, unknown> | null;
  /** 服务端落库后这条用户消息的 id（用于把乐观气泡替换成真实消息）。 */
  user_message_id?: string;
  /** 服务端落库后这条助手回复的 id。 */
  assistant_message_id?: string;
}

/** 标签分类，与后端 cef_protocol.tags.categorize 一致。 */
export type TagCategory = "role" | "hw" | "site" | "bench" | "free";

export const TAG_CATEGORY_ORDER: readonly TagCategory[] = ["role", "hw", "site", "bench", "free"];

export const TAG_CATEGORY_LABELS: Record<TagCategory, string> = {
  role: "角色",
  hw: "硬件",
  site: "场地",
  bench: "基准",
  free: "其他",
};

/** 依据前缀判断标签分类（GET /api/v1/tags 不可用时的本地兜底）。 */
export function tagCategory(tag: string): TagCategory {
  if (tag.startsWith("bench-")) return "bench";
  if (tag.startsWith("role:")) return "role";
  if (tag.startsWith("hw:")) return "hw";
  if (tag.startsWith("site:")) return "site";
  return "free";
}

/** GET /api/v1/tags 的一项。 */
export interface TagStat {
  tag: string;
  category: TagCategory;
  /** 注册了该标签的设备总数（含离线）。 */
  total: number;
  /** 注册了该标签且当前在线的设备数。 */
  online: number;
}

export interface WorkerTagUpdate {
  /** 规范化后实际保存在设备上的标签（升序）。 */
  tags: string[];
  /** 被服务端拒绝的标签（格式非法或超过 8 个上限）。 */
  rejected: string[];
}

export interface DownloadPayload {
  blob: Blob;
  filename: string;
}

export interface ListJobsOptions {
  limit?: number;
  state?: JobState;
}

export interface ChatMessagesOptions {
  room?: string;
  /** created_at 浮点游标，只取严格更新的消息；不传则取最近 limit 条。 */
  after?: number | null;
  limit?: number;
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
    if (error.status === 503 && error.code === "agent_unavailable") {
      return {
        title: "AI 助手暂不可用",
        detail: "当前 Coordinator 尚未配置 AI 助手。请联系管理员。",
      };
    }
    if (error.status === 502 && error.code === "agent_error") {
      return {
        title: "AI 助手连接模型服务失败",
        detail: "请稍后重新发送。",
      };
    }
    switch (error.kind) {
      case "network":
        return {
          title: "无法连接 Coordinator",
          detail: "请检查网络和左下角的连接设置，确认 Coordinator 已启动后重试。",
        };
      case "unauthorized":
        return {
          title: "认证失败：Token 无效或缺失",
          detail: "请在左下角连接设置里填写有效的 Token，然后点击「应用」。",
        };
      case "not_found":
        if (error.code === "not_found") {
          return {
            title: "找不到这条记录（404）",
            detail: "请返回列表刷新，或确认记录是否已移除。",
          };
        }
        return {
          title: "接口不存在（404）",
          detail: "请检查左下角的服务地址，或联系管理员确认 Coordinator 版本。",
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

function normalizeBlobBrief(raw: unknown): BlobBrief | null {
  const r = asRecord(raw);
  if (!r) return null;
  const sha256 = asString(r.sha256);
  if (!sha256) return null;
  return {
    sha256,
    name: asString(r.name) ?? sha256.slice(0, 12),
    size: asNumber(r.size) ?? 0,
    // 老版本后端可能不给 url，用 blob 路径兜底。
    url: asString(r.url) ?? "/api/v1/blobs/" + encodeURIComponent(sha256),
  };
}

function normalizeTask(raw: unknown, index: number): JobTask | null {
  const r = asRecord(raw);
  if (!r) return null;
  const inputsRaw = Array.isArray(r.inputs) ? r.inputs : [];
  const outputsRaw = Array.isArray(r.outputs) ? r.outputs : [];
  return {
    task_id: asString(r.task_id) ?? "task-" + index,
    idx: asNumber(r.idx) ?? index,
    state: isTaskState(r.state) ? r.state : "queued",
    worker: asString(r.worker) ?? null,
    worker_name: asString(r.worker_name) ?? null,
    attempt: asNumber(r.attempt),
    duration_s: asNumberOrNull(r.duration_s),
    error: asString(r.error),
    started_at: asNumberOrNull(r.started_at),
    finished_at: asNumberOrNull(r.finished_at),
    output_sha256: asString(r.output_sha256),
    inputs: inputsRaw.map(normalizeBlobBrief).filter(isNotNull),
    outputs: outputsRaw.map(normalizeBlobBrief).filter(isNotNull),
    summary: asString(r.summary),
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
    params_text: asString(r.params_text) ?? "",
    input_summary: asString(r.input_summary) ?? "",
    submitter: asString(r.submitter) ?? null,
    required_tag: asString(r.required_tag) ?? null,
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
    ["tasks_succeeded_total", "succeeded_total"],
    ["tasks_failed_total", "failed_total"],
    ["retries_total", "retries_total"],
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

function normalizeAgentChat(raw: unknown): AgentChatResponse {
  const response = asRecord(raw);
  const reply = asString(response?.reply);
  if (!response || !reply) throw new CefApiError("parse", "AI 助手响应缺少回复内容。");

  let preview: AgentPreview | null = null;
  if (response.preview != null) {
    const data = asRecord(response.preview);
    const workload = asString(data?.workload);
    const params = asRecord(data?.params);
    if (!data || !workload || !params) {
      throw new CefApiError("parse", "AI 助手返回的任务预览缺少工作负载或参数。");
    }
    const count = asNumberOrNull(data.task_count);
    preview = {
      workload,
      params,
      task_count: count !== null && Number.isInteger(count) && count >= 0 ? count : null,
      note: asString(data.note) ?? "",
    };
  }

  return {
    reply,
    preview,
    tool_trace: normalizeToolTraceList(response.tool_trace),
    model: asString(response.model),
    usage: asRecord(response.usage),
    user_message_id: asString(response.user_message_id),
    assistant_message_id: asString(response.assistant_message_id),
  };
}

function normalizeToolTrace(raw: unknown): AgentToolTrace | null {
  const data = asRecord(raw);
  const name = asString(data?.name);
  if (!data || !name) return null;
  return {
    name,
    args: asRecord(data.args) ?? {},
    ok: data.ok === true,
    summary: asString(data.summary) ?? "",
  };
}

function normalizeToolTraceList(raw: unknown): AgentToolTrace[] {
  const list = Array.isArray(raw) ? raw : [];
  return list.map(normalizeToolTrace).filter(isNotNull);
}

/** 宽松解析预览：对话历史里坏掉的预览不应该让整条消息消失。 */
function normalizePreview(raw: unknown): AgentPreview | null {
  const data = asRecord(raw);
  if (!data) return null;
  const workload = asString(data.workload);
  const params = asRecord(data.params);
  if (!workload || !params) return null;
  const count = asNumberOrNull(data.task_count);
  return {
    workload,
    params,
    task_count: count !== null && Number.isInteger(count) && count >= 0 ? count : null,
    note: asString(data.note) ?? "",
  };
}

/** GET /api/v1/chat/messages 的单条消息；缺 id/内容时丢弃。 */
function normalizeServerChatMessage(raw: unknown): ServerChatMessage | null {
  const r = asRecord(raw);
  if (!r) return null;
  const id = asString(r.id);
  const content = asString(r.content);
  const role = r.role === "user" || r.role === "assistant" ? r.role : null;
  if (!id || content === undefined || !role) return null;
  return {
    id,
    room: asString(r.room) ?? "pool",
    role,
    author: asString(r.author) ?? "",
    content,
    trace: Array.isArray(r.trace) ? normalizeToolTraceList(r.trace) : null,
    preview: normalizePreview(r.preview),
    job_id: asString(r.job_id) ?? null,
    created_at: asNumber(r.created_at) ?? 0,
  };
}

function normalizeChatPage(raw: unknown): ChatPage {
  const r = asRecord(raw);
  if (!r) throw new CefApiError("parse", "对话接口返回的不是 JSON 对象。");
  const list = Array.isArray(r.messages) ? r.messages : [];
  const messages = list.map(normalizeServerChatMessage).filter(isNotNull);
  const latest = asNumber(r.latest) ?? messages.reduce((max, item) => Math.max(max, item.created_at), 0);
  return { messages, latest };
}

function normalizeTagStat(raw: unknown): TagStat | null {
  const r = asRecord(raw);
  if (!r) return null;
  const tag = asString(r.tag);
  if (!tag) return null;
  const category = r.category === "role" || r.category === "hw" || r.category === "site"
    || r.category === "bench" || r.category === "free"
    ? r.category
    : tagCategory(tag);
  return {
    tag,
    category,
    total: asNumber(r.total) ?? 0,
    online: asNumber(r.online) ?? 0,
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
  getChatMessages(options?: ChatMessagesOptions, signal?: AbortSignal): Promise<ChatPage>;
  getTags(signal?: AbortSignal): Promise<TagStat[]>;
  setWorkerTags(workerId: string, tags: string[]): Promise<WorkerTagUpdate>;
  /** 带 Authorization 拉取 blob（<img> 带不了请求头，需要先取成 Blob/objectURL）。 */
  fetchBlob(pathOrUrl: string, signal?: AbortSignal): Promise<Blob>;
  chat(request: AgentChatRequest, signal?: AbortSignal): Promise<AgentChatResponse>;
  submitJob(spec: JobSpec, files: File[]): Promise<SubmitResponse>;
  cancelJob(jobId: string): Promise<void>;
  forgetWorker(workerId: string): Promise<void>;
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

    async getChatMessages(options: ChatMessagesOptions = {}, signal?: AbortSignal): Promise<ChatPage> {
      const qs = new URLSearchParams();
      qs.set("room", options.room ?? "pool");
      if (typeof options.after === "number" && Number.isFinite(options.after) && options.after > 0) {
        qs.set("after", String(options.after));
      }
      qs.set("limit", String(options.limit ?? 200));
      const raw = await requestJson("/chat/messages?" + qs.toString(), { method: "GET" }, signal);
      return normalizeChatPage(raw);
    },

    async getTags(signal?: AbortSignal): Promise<TagStat[]> {
      const raw = await requestJson("/tags", { method: "GET" }, signal);
      const r = asRecord(raw);
      const list = r && Array.isArray(r.tags) ? r.tags : [];
      return list.map(normalizeTagStat).filter(isNotNull);
    },

    async setWorkerTags(workerId: string, tags: string[]): Promise<WorkerTagUpdate> {
      const raw = await requestJson("/workers/" + encodeURIComponent(workerId), {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tags }),
      });
      const r = asRecord(raw) ?? {};
      const saved = asStringArray(r.tags);
      return {
        tags: saved.length > 0 ? saved : tags,
        // 后端字段名是 rejected_tags；兼容旧写法 rejected。
        rejected: asStringArray(r.rejected_tags ?? r.rejected),
      };
    },

    async fetchBlob(pathOrUrl: string, signal?: AbortSignal): Promise<Blob> {
      const url = /^https?:\/\//i.test(pathOrUrl)
        ? pathOrUrl
        : baseUrl + (pathOrUrl.startsWith("/") ? pathOrUrl : "/" + pathOrUrl);
      let res: Response;
      try {
        res = await fetch(url, { method: "GET", headers: authHeaders(), signal });
      } catch (error) {
        if (isAbortError(error)) throw new CefApiError("aborted", "请求已取消。");
        throw new CefApiError("network", "无法访问 " + (baseUrl || "（未配置地址）") + "。");
      }
      if (!res.ok) {
        const body = await parseBody(res);
        throw httpError(res.status, body);
      }
      return res.blob();
    },

    async chat(request: AgentChatRequest, signal?: AbortSignal): Promise<AgentChatResponse> {
      const raw = await requestJson("/agent/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(request),
      }, signal);
      return normalizeAgentChat(raw);
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

    async forgetWorker(workerId: string): Promise<void> {
      await requestJson("/workers/" + encodeURIComponent(workerId), { method: "DELETE" });
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
