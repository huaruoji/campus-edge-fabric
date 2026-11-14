"""Wire models for API v1. Single source of truth for both sides of the wire."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .states import JobState, TaskState, WorkerState


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore")


# ---------------------------------------------------------------- blobs / inputs
class BlobRef(_Model):
    sha256: str
    name: str
    size: int = 0
    url: str | None = None


# ------------------------------------------------------------------------ submit
class InputSpec(_Model):
    name: str


class JobSpec(_Model):
    workload: str
    params: dict[str, Any] = Field(default_factory=dict)
    name: str | None = None
    submitter: str | None = None
    #: name or id of a registered dataset; when set, no files need uploading
    dataset: str | None = None
    priority: int = 0
    required_tag: str | None = None
    inputs: list[InputSpec] = Field(default_factory=list)


class JobCreated(_Model):
    job_id: str
    total: int
    state: JobState


class BlobBrief(_Model):
    sha256: str
    name: str = ""
    size: int = 0
    url: str | None = None


class TaskView(_Model):
    task_id: str
    idx: int
    state: TaskState
    worker: str | None = None
    worker_name: str | None = None
    attempt: int = 0
    duration_s: float | None = None
    started_at: float | None = None
    finished_at: float | None = None
    error: str | None = None
    output_sha256: str | None = None
    inputs: list[BlobBrief] = Field(default_factory=list)
    outputs: list[BlobBrief] = Field(default_factory=list)
    summary: str | None = None


class PerWorkerStat(_Model):
    worker_id: str
    name: str | None = None
    completed: int = 0
    failed: int = 0
    mean_duration_s: float | None = None
    total_duration_s: float = 0.0
    share: float = 0.0  # fraction of this job's successful tasks


class JobSummary(_Model):
    job_id: str
    name: str | None = None
    workload: str
    state: JobState
    total: int
    succeeded: int = 0
    failed: int = 0
    queued: int = 0
    created_at: float
    finished_at: float | None = None


class JobDetail(JobSummary):
    eta_s: float | None = None
    params_text: str = ""
    input_summary: str = ""
    submitter: str | None = None
    per_worker: list[PerWorkerStat] = Field(default_factory=list)
    tasks: list[TaskView] = Field(default_factory=list)
    required_tag: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class JobList(_Model):
    jobs: list[JobSummary]


# ---------------------------------------------------------------------- workers
class WorkloadSupport(_Model):
    kind: str
    available: bool = True
    version: str | None = None
    reason: str | None = None


class RegisterRequest(_Model):
    worker_id: str | None = None
    name: str
    hostname: str = ""
    os: str = ""
    cpu_count: int = 1
    tags: list[str] = Field(default_factory=list)
    slots: int = 1
    max_batch: int = 4
    workloads: list[WorkloadSupport] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)


class RegisterResponse(_Model):
    worker_id: str
    lease_ttl_s: int = 90
    heartbeat_interval_s: int = 5
    poll_interval_s: float = 1.0


class HeartbeatStats(_Model):
    cpu_pct: float | None = None
    mem_pct: float | None = None
    gpu_pct: float | None = None
    disk_free_gb: float | None = None
    disk_total_gb: float | None = None
    running: int = 0


class HeartbeatRequest(_Model):
    stats: HeartbeatStats = Field(default_factory=HeartbeatStats)
    leases: list[str] = Field(default_factory=list)


class HeartbeatResponse(_Model):
    lease_ttl_s: int = 90
    poll_now: bool = True
    drain: bool = False


class PollRequest(_Model):
    capacity: int = Field(1, ge=0, le=64)
    workloads: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class Lease(_Model):
    task_id: str
    job_id: str
    idx: int
    attempt: int
    workload: str
    params: dict[str, Any] = Field(default_factory=dict)
    inputs: list[BlobRef] = Field(default_factory=list)
    lease_expires_at: float
    lease_ttl_s: int


class PollResponse(_Model):
    leases: list[Lease] = Field(default_factory=list)
    #: adaptive hint: how many tasks this worker should ask for next time
    suggested_capacity: int = 1


class CompleteMeta(_Model):
    attempt: int
    duration_s: float
    output: dict[str, Any] = Field(default_factory=dict)
    output_names: list[str] = Field(default_factory=list)


class CompleteResponse(_Model):
    ok: bool = True
    credited: bool = False
    reason: str | None = None


class FailRequest(_Model):
    attempt: int
    error: str
    retryable: bool = True


class FailResponse(_Model):
    requeued: bool = False
    attempts_left: int = 0


class DrainResponse(_Model):
    worker_id: str
    released: int = 0


class WorkerView(_Model):
    worker_id: str
    name: str
    hostname: str = ""
    os: str = ""
    cpu_count: int = 1
    tags: list[str] = Field(default_factory=list)
    slots: int = 1
    state: WorkerState = WorkerState.OFFLINE
    last_seen_s_ago: float | None = None
    cpu_pct: float | None = None
    mem_pct: float | None = None
    gpu_pct: float | None = None
    disk_free_gb: float | None = None
    disk_total_gb: float | None = None
    running: int = 0
    completed_total: int = 0
    failed_total: int = 0
    mean_duration_s: float | None = None
    throughput_per_min: float | None = None
    workloads: list[WorkloadSupport] = Field(default_factory=list)


class WorkerList(_Model):
    workers: list[WorkerView]


# ------------------------------------------------------------------------ stats
class UtilPoint(_Model):
    ts: float
    cpu_pct: float | None = None
    gpu_pct: float | None = None


class WorkerStats(_Model):
    worker_id: str
    name: str | None = None
    util_series: list[UtilPoint] = Field(default_factory=list)
    mean_cpu_pct: float | None = None
    mean_gpu_pct: float | None = None


class PoolStats(_Model):
    now: float
    window_s: int
    workers_online: int = 0
    jobs_total: int = 0
    jobs_running: int = 0
    #: window-scoped (last `window_s` seconds)
    tasks_succeeded: int = 0
    tasks_failed: int = 0
    tasks_queued: int = 0
    #: lifetime counters, so the dashboard never has to guess what "0" means
    tasks_succeeded_total: int = 0
    tasks_failed_total: int = 0
    retries_total: int = 0
    tasks_per_min: float | None = None
    p50_duration_s: float | None = None
    p95_duration_s: float | None = None
    retries: int = 0
    workers: list[WorkerStats] = Field(default_factory=list)
    bottleneck: str | None = None


class WorkloadInfo(_Model):
    kind: str
    unit: str
    params_schema: dict[str, Any]
    available_on: list[str] = Field(default_factory=list)


class WorkloadList(_Model):
    workloads: list[WorkloadInfo]


class Health(_Model):
    ok: bool = True
    version: str
    uptime_s: float
    workers_online: int


class ErrorInfo(_Model):
    code: str
    message: str


class ErrorResponse(_Model):
    error: ErrorInfo


class ManifestEntry(_Model):
    task_id: str
    idx: int
    name: str | None = None
    worker_id: str | None = None
    worker_name: str | None = None
    attempt: int = 0
    duration_s: float | None = None
    output_sha256: str | None = None
    output_files: list[str] = Field(default_factory=list)
    error: str | None = None


class JobManifest(_Model):
    job_id: str
    name: str | None = None
    workload: str
    params: dict[str, Any] = Field(default_factory=dict)
    state: JobState
    total: int
    succeeded: int
    failed: int
    created_at: float
    finished_at: float | None = None
    generated_at: float
    entries: list[ManifestEntry] = Field(default_factory=list)


SORT_DESC = Literal["asc", "desc"]

# ---------------------------------------------------------------- agent (chat)
class ChatMessage(_Model):
    role: Literal["user", "assistant", "system"]
    content: str


class AgentFile(_Model):
    name: str
    size: int = 0


class AgentContext(_Model):
    files: list[AgentFile] = Field(default_factory=list)
    job_id: str | None = None


class AgentChatRequest(_Model):
    messages: list[ChatMessage] = Field(default_factory=list)
    context: AgentContext = Field(default_factory=AgentContext)
    author: str = ""
    room: str = "pool"


class AgentPreview(_Model):
    workload: str
    params: dict[str, Any] = Field(default_factory=dict)
    task_count: int | None = None
    note: str = ""


class AgentToolTrace(_Model):
    name: str
    args: dict[str, Any] = Field(default_factory=dict)
    ok: bool = True
    summary: str = ""


class AgentChatResponse(_Model):
    reply: str
    preview: AgentPreview | None = None
    tool_trace: list[AgentToolTrace] = Field(default_factory=list)
    model: str = ""
    usage: dict[str, Any] | None = None
    user_message_id: str | None = None
    assistant_message_id: str | None = None


class DatasetSummary(_Model):
    id: str
    name: str
    description: str | None = None
    count: int = 0
    bytes: int = 0
    created_at: float = 0.0
    updated_at: float = 0.0


class DatasetDetail(DatasetSummary):
    files: list[BlobBrief] = Field(default_factory=list)


class DatasetList(_Model):
    datasets: list[DatasetSummary] = Field(default_factory=list)


class ChatEntry(_Model):
    """One persisted transcript line, shared by everyone holding the token."""

    id: str
    room: str = "pool"
    role: Literal["user", "assistant"]
    author: str = ""
    content: str
    trace: list[AgentToolTrace] | None = None
    preview: AgentPreview | None = None
    job_id: str | None = None
    created_at: float


class ChatList(_Model):
    messages: list[ChatEntry] = Field(default_factory=list)
    latest: float = 0.0


class TagSummary(_Model):
    tag: str
    category: str = "free"
    total: int = 0
    online: int = 0


class TagList(_Model):
    tags: list[TagSummary] = Field(default_factory=list)


class WorkerPatch(_Model):
    tags: list[str] = Field(default_factory=list)


class WorkerPatchResponse(_Model):
    worker_id: str
    tags: list[str] = Field(default_factory=list)
    rejected_tags: list[str] = Field(default_factory=list)
