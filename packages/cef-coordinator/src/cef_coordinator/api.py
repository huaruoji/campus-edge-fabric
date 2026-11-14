"""HTTP API v1. Controllers only: all state transitions live in scheduler.py."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Annotated, Any

from cef_protocol import new_id
from cef_protocol.models import (
    CompleteMeta,
    CompleteResponse,
    FailRequest,
    FailResponse,
    Health,
    HeartbeatRequest,
    HeartbeatResponse,
    JobCreated,
    JobDetail,
    JobList,
    JobManifest,
    JobSpec,
    JobSummary,
    ManifestEntry,
    PerWorkerStat,
    PollRequest,
    PollResponse,
    PoolStats,
    RegisterRequest,
    RegisterResponse,
    TaskView,
    UtilPoint,
    WorkerList,
    WorkerStats,
    WorkerView,
    WorkloadInfo,
    WorkloadList,
    WorkloadSupport,
)
from cef_protocol.states import JobState, TaskState
from cef_protocol.workloads import WORKLOAD_UNITS, InvalidParams, param_schema, parse_frames, validate_params
from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response

from . import db, scheduler, store
from .config import Settings

router = APIRouter(prefix="/api/v1")


# ------------------------------------------------------------------ plumbing
def settings_of(request: Request) -> Settings:
    return request.app.state.settings  # type: ignore[no-any-return]


def blobs_of(request: Request) -> store.BlobStore:
    return request.app.state.blobs  # type: ignore[no-any-return]


def _err(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"error": {"code": code, "message": message}})


def _blob_rows(conn: sqlite3.Connection, refs: list[tuple[str, str, int]]) -> None:
    now = time.time()
    for sha, _name, size in refs:
        conn.execute(
            "INSERT OR IGNORE INTO blobs (sha256, size, path, created_at) VALUES (?,?,?,?)",
            (sha, size, "", now),
        )


async def _spool(upload: UploadFile, store_: store.BlobStore) -> tuple[str, str, int]:
    sha, size = store_.put_stream(upload.file)
    return sha, upload.filename or sha[:12], size


# --------------------------------------------------------------------- health
@router.get("/health", response_model=Health)
def health(request: Request) -> Health:
    s = settings_of(request)
    conn = db.reading(s.db_path)
    try:
        online = db.one(conn, "SELECT COUNT(*) n FROM workers WHERE state != 'offline'")["n"]
    finally:
        conn.close()
    return Health(
        ok=True,
        version=request.app.state.version,
        uptime_s=round(time.time() - request.app.state.started_at, 2),
        workers_online=int(online),
    )


# ----------------------------------------------------------------------- jobs
@router.post("/jobs", status_code=201, response_model=JobCreated)
async def create_job(
    request: Request,
    spec: Annotated[str, Form()],
    files: Annotated[list[UploadFile], File()] = [],  # noqa: B006 - FastAPI marker
) -> JobCreated:
    s = settings_of(request)
    bs = blobs_of(request)
    try:
        job_spec = JobSpec.model_validate_json(spec)
    except ValueError as exc:
        raise _err(400, "invalid_params", f"bad spec: {exc}") from exc
    try:
        params_model = validate_params(job_spec.workload, job_spec.params)
    except InvalidParams as exc:
        raise _err(400, "invalid_params", str(exc)) from exc
    params = params_model.model_dump()

    stored: list[tuple[str, str, int]] = []
    tasks: list[tuple[int, dict[str, Any], list[dict[str, Any]]]] = []

    if job_spec.workload == "blender":
        if len(files) != 1:
            raise _err(400, "invalid_params", "blender jobs need exactly one .blend file")
        try:
            frames = parse_frames(params["frames"])
        except InvalidParams as exc:
            raise _err(400, "invalid_params", str(exc)) from exc
        sha, name, size = await _spool(files[0], bs)
        stored.append((sha, name, size))
        ref = {"sha256": sha, "name": name, "size": size}
        for i, frame in enumerate(frames):
            tasks.append((i, {**params, "frame": frame}, [ref]))
    else:
        if not files:
            raise _err(400, "invalid_params", f"workload {job_spec.workload} needs at least one input file")
        if job_spec.inputs and len(job_spec.inputs) != len(files):
            raise _err(400, "invalid_params", "inputs[] length must match uploaded files")
        for i, upload in enumerate(files):
            sha, name, size = await _spool(upload, bs)
            stored.append((sha, name, size))
            if job_spec.inputs:
                name = job_spec.inputs[i].name or name
            tasks.append((i, params, [{"sha256": sha, "name": name, "size": size}]))

    job_id = new_id("job")
    now = time.time()
    with db.transaction(s.db_path) as conn:
        _blob_rows(conn, stored)
        conn.execute(
            """INSERT INTO jobs (id,name,workload,params,state,priority,required_tag,submitter,created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                job_id,
                job_spec.name,
                job_spec.workload,
                json.dumps(params, ensure_ascii=False),
                str(JobState.QUEUED),
                int(job_spec.priority),
                job_spec.required_tag,
                None,
                now,
            ),
        )
        for idx, task_params, inputs in tasks:
            conn.execute(
                """INSERT INTO tasks (id,job_id,idx,state,attempt,max_attempts,params,inputs,queued_at,updated_at)
                   VALUES (?,?,?,?,0,?,?,?,?,?)""",
                (
                    f"{job_id}-{idx:06d}",
                    job_id,
                    idx,
                    str(TaskState.QUEUED),
                    s.max_attempts,
                    json.dumps(task_params, ensure_ascii=False),
                    json.dumps(inputs, ensure_ascii=False),
                    now,
                    now,
                ),
            )
    return JobCreated(job_id=job_id, total=len(tasks), state=JobState.QUEUED)


@router.get("/jobs", response_model=JobList)
def list_jobs(request: Request, limit: int = Query(50, ge=1, le=500), state: str | None = None) -> JobList:
    s = settings_of(request)
    conn = db.reading(s.db_path)
    try:
        sql = "SELECT * FROM jobs"
        args: list[Any] = []
        if state:
            sql += " WHERE state=?"
            args.append(state)
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(limit)
        jobs = db.rows(conn, sql, tuple(args))
        out = []
        for j in jobs:
            counts = _counts(conn, j["id"])
            out.append(_summary(j, counts))
    finally:
        conn.close()
    return JobList(jobs=out)


@router.get("/jobs/{job_id}", response_model=JobDetail)
def get_job(request: Request, job_id: str, tasks_limit: int = Query(500, ge=0, le=5000)) -> JobDetail:
    s = settings_of(request)
    conn = db.reading(s.db_path)
    try:
        job = db.one(conn, "SELECT * FROM jobs WHERE id=?", (job_id,))
        if job is None:
            raise _err(404, "not_found", f"job {job_id} not found")
        counts = _counts(conn, job_id)
        summary = _summary(job, counts)
        names = {r["id"]: r["name"] for r in db.rows(conn, "SELECT id,name FROM workers")}
        per_worker = _per_worker(conn, job_id, counts["succeeded"], names)
        tasks = [
            TaskView(
                task_id=r["id"],
                idx=r["idx"],
                state=r["state"],
                worker=r["worker_id"],
                worker_name=names.get(r["worker_id"] or ""),
                attempt=r["attempt"],
                duration_s=r["duration"],
                started_at=r["started_at"],
                finished_at=r["finished_at"],
                error=r["error"],
                output_sha256=r["output_sha"],
            )
            for r in db.rows(conn, "SELECT * FROM tasks WHERE job_id=? ORDER BY idx LIMIT ?", (job_id, tasks_limit))
        ]
        eta = _eta(conn, job, counts)
        return JobDetail(
            **summary.model_dump(),
            eta_s=eta,
            per_worker=per_worker,
            tasks=tasks,
            required_tag=job["required_tag"],
            params=json.loads(job["params"] or "{}"),
        )
    finally:
        conn.close()


@router.post("/jobs/{job_id}/cancel", status_code=202)
def cancel_job(request: Request, job_id: str) -> dict[str, Any]:
    s = settings_of(request)
    now = time.time()
    with db.transaction(s.db_path) as conn:
        job = db.one(conn, "SELECT id FROM jobs WHERE id=?", (job_id,))
        if job is None:
            raise _err(404, "not_found", f"job {job_id} not found")
        n = conn.execute(
            "UPDATE tasks SET state='canceled', updated_at=? WHERE job_id=? AND state IN ('queued','leased')",
            (now, job_id),
        ).rowcount
        conn.execute("UPDATE jobs SET state='canceled', finished_at=? WHERE id=?", (now, job_id))
        scheduler._event(conn, "job_canceled", job_id=job_id, message=f"{n} tasks canceled")
    return {"job_id": job_id, "canceled_tasks": n}


@router.get("/jobs/{job_id}/manifest", response_model=JobManifest)
def job_manifest(request: Request, job_id: str) -> JobManifest:
    s = settings_of(request)
    conn = db.reading(s.db_path)
    try:
        job = db.one(conn, "SELECT * FROM jobs WHERE id=?", (job_id,))
        if job is None:
            raise _err(404, "not_found", f"job {job_id} not found")
        counts = _counts(conn, job_id)
        return _manifest(conn, job, counts)
    finally:
        conn.close()


@router.get("/jobs/{job_id}/result")
def job_result(request: Request, job_id: str) -> FileResponse:
    s = settings_of(request)
    bs = blobs_of(request)
    conn = db.reading(s.db_path)
    try:
        job = db.one(conn, "SELECT * FROM jobs WHERE id=?", (job_id,))
        if job is None:
            raise _err(404, "not_found", f"job {job_id} not found")
        counts = _counts(conn, job_id)
        manifest = _manifest(conn, job, counts)
        tasks = db.rows(conn, "SELECT * FROM tasks WHERE job_id=? ORDER BY idx", (job_id,))
    finally:
        conn.close()

    fd, tmp_name = tempfile.mkstemp(suffix=".zip", dir=str(s.data_dir))
    os.close(fd)
    with zipfile.ZipFile(tmp_name, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", manifest.model_dump_json(indent=2))
        for row in tasks:
            if not row["output"]:
                continue
            out = json.loads(row["output"])
            for f in out.get("files", []):
                src = bs.path_for(f["sha256"])
                if not src.exists():
                    continue
                zf.write(src, f"outputs/{row['idx']:06d}_{f['name']}")
            if out.get("meta"):
                zf.writestr(f"outputs/{row['idx']:06d}.json", json.dumps(out["meta"], ensure_ascii=False, indent=2))
    return FileResponse(
        tmp_name,
        media_type="application/zip",
        filename=f"{job['id']}.zip",
        background=_Cleanup(tmp_name),
    )


class _Cleanup:
    """Starlette background task that deletes the temp zip after the response."""

    def __init__(self, path: str) -> None:
        self.path = path

    async def __call__(self) -> None:
        try:
            Path(self.path).unlink()
        except OSError:
            pass


# -------------------------------------------------------------------- workers
@router.post("/workers/register", response_model=RegisterResponse)
def register_worker(request: Request, reg: RegisterRequest) -> RegisterResponse:
    s = settings_of(request)
    row = scheduler.upsert_worker(s.db_path, reg.model_dump(), s)
    return RegisterResponse(
        worker_id=row["id"],
        lease_ttl_s=s.lease_ttl_s,
        heartbeat_interval_s=s.heartbeat_interval_s,
        poll_interval_s=1.0,
    )


@router.post("/workers/{worker_id}/heartbeat", response_model=HeartbeatResponse)
def worker_heartbeat(request: Request, worker_id: str, hb: HeartbeatRequest) -> HeartbeatResponse:
    s = settings_of(request)
    row = scheduler.heartbeat(s.db_path, worker_id, hb.stats.model_dump(), hb.leases, s)
    if row is None:
        raise _err(404, "not_found", "unknown worker, re-register")
    return HeartbeatResponse(
        lease_ttl_s=s.lease_ttl_s,
        poll_now=True,
        drain=row["state"] == "draining",
    )


@router.post("/workers/{worker_id}/poll", response_model=PollResponse)
def worker_poll(request: Request, worker_id: str, poll: PollRequest) -> PollResponse:
    s = settings_of(request)
    conn = db.reading(s.db_path)
    try:
        worker = db.one(conn, "SELECT * FROM workers WHERE id=?", (worker_id,))
    finally:
        conn.close()
    if worker is None:
        raise _err(404, "not_found", "unknown worker, re-register")
    want = poll.capacity if poll.capacity else scheduler.suggested_capacity(worker, s)
    leases = scheduler.claim(s.db_path, s, worker, want, poll.workloads)
    latest = db.reading(s.db_path)
    try:
        row = db.one(latest, "SELECT * FROM workers WHERE id=?", (worker_id,))
    finally:
        latest.close()
    hint = scheduler.suggested_capacity(row, s) if row is not None else 1
    return PollResponse(leases=leases, suggested_capacity=hint)


@router.post("/workers/{worker_id}/tasks/{task_id}/complete", response_model=CompleteResponse)
async def task_complete(
    request: Request,
    worker_id: str,
    task_id: str,
    meta: Annotated[str, Form()],
    files: Annotated[list[UploadFile], File()] = [],  # noqa: B006
) -> CompleteResponse:
    s = settings_of(request)
    bs = blobs_of(request)
    try:
        cm = CompleteMeta.model_validate_json(meta)
    except ValueError as exc:
        raise _err(400, "invalid_params", f"bad meta: {exc}") from exc
    refs: list[tuple[str, str, int]] = []
    for upload in files:
        sha, size = bs.put_stream(upload.file)
        refs.append((sha, upload.filename or sha[:12], size))
    output = cm.output or {}
    output["files"] = [{"name": n, "sha256": sha, "size": size} for sha, n, size in refs]
    output_sha = refs[0][0] if len(refs) == 1 else (
        hashlib.sha256("".join(r[0] for r in refs).encode()).hexdigest() if refs else None
    )
    ok, reason = scheduler.complete_task(
        s.db_path, s, worker_id, task_id, cm.attempt, cm.duration_s, output, output_sha
    )
    if not ok:
        raise _err(409 if reason == "stale_attempt" else 404, str(reason), "result not credited")
    with db.transaction(s.db_path) as conn:
        _blob_rows(conn, refs)
    return CompleteResponse(ok=True, credited=True)


@router.post("/workers/{worker_id}/tasks/{task_id}/fail", response_model=FailResponse)
def task_fail(request: Request, worker_id: str, task_id: str, fail: FailRequest) -> FailResponse:
    s = settings_of(request)
    ok, requeued, left = scheduler.fail_task(
        s.db_path, s, worker_id, task_id, fail.attempt, fail.error, fail.retryable
    )
    if not ok:
        raise _err(409, "stale_attempt", "failure report ignored (lease lost)")
    return FailResponse(requeued=requeued, attempts_left=left)


@router.post("/workers/{worker_id}/drain", status_code=202)
def worker_drain(request: Request, worker_id: str) -> dict[str, Any]:
    s = settings_of(request)
    scheduler.set_state(s.db_path, worker_id, "draining")
    released = scheduler.release_leases(s.db_path, worker_id, "drained")
    return {"worker_id": worker_id, "released": released}


@router.get("/workers", response_model=WorkerList)
def list_workers(request: Request) -> WorkerList:
    s = settings_of(request)
    conn = db.reading(s.db_path)
    now = time.time()
    try:
        workers = db.rows(conn, "SELECT * FROM workers ORDER BY state, name")
        out: list[WorkerView] = []
        for w in workers:
            stats = db.one(conn, "SELECT * FROM worker_stats WHERE worker_id=? ORDER BY ts DESC LIMIT 1", (w["id"],))
            done = db.one(
                conn,
                "SELECT COUNT(*) n, AVG(duration) d, SUM(duration) t FROM tasks WHERE worker_id=? AND state='succeeded'",
                (w["id"],),
            )
            failed = db.one(
                conn, "SELECT COUNT(*) n FROM tasks WHERE worker_id=? AND state='failed'", (w["id"],)
            )
            recent = db.one(
                conn,
                "SELECT COUNT(*) n FROM tasks WHERE worker_id=? AND state='succeeded' AND finished_at > ?",
                (w["id"], now - 60),
            )
            out.append(
                WorkerView(
                    worker_id=w["id"],
                    name=w["name"],
                    hostname=w["hostname"] or "",
                    os=w["os"] or "",
                    cpu_count=w["cpu_count"] or 1,
                    tags=json.loads(w["tags"] or "[]"),
                    slots=w["slots"] or 1,
                    state=w["state"],
                    last_seen_s_ago=None if not w["last_seen"] else round(now - w["last_seen"], 1),
                    cpu_pct=stats["cpu_pct"] if stats else None,
                    mem_pct=stats["mem_pct"] if stats else None,
                    gpu_pct=stats["gpu_pct"] if stats else None,
                    running=stats["running"] if stats else 0,
                    completed_total=int(done["n"] or 0),
                    failed_total=int(failed["n"] or 0) if failed else 0,
                    mean_duration_s=round(float(done["d"]), 3) if done and done["d"] else None,
                    throughput_per_min=round(float(recent["n"] or 0), 1),
                    workloads=[WorkloadSupport.model_validate(x) for x in json.loads(w["workloads"] or "[]")],
                )
            )
    finally:
        conn.close()
    return WorkerList(workers=out)


@router.get("/workloads", response_model=WorkloadList)
def list_workloads(request: Request) -> WorkloadList:
    s = settings_of(request)
    conn = db.reading(s.db_path)
    try:
        workers = db.rows(conn, "SELECT name, workloads FROM workers WHERE state != 'offline'")
    finally:
        conn.close()
    kinds = sorted(str(kind) for kind in _workload_kinds())
    out = []
    for kind in kinds:
        available = [
            row["name"]
            for row in workers
            for support in json.loads(row["workloads"] or "[]")
            if support.get("kind") == kind and support.get("available", True)
        ]
        out.append(
            WorkloadInfo(
                kind=kind,
                unit=WORKLOAD_UNITS.get(kind, "tasks"),
                params_schema=param_schema(kind),
                available_on=sorted(set(available)),
            )
        )
    return WorkloadList(workloads=out)


def _workload_kinds() -> list[Any]:
    from cef_protocol.workloads import WORKLOADS

    return list(WORKLOADS)


# ---------------------------------------------------------------------- stats
@router.get("/stats", response_model=PoolStats)
def pool_stats(request: Request, window: int = Query(300, ge=10, le=86400)) -> PoolStats:
    s = settings_of(request)
    conn = db.reading(s.db_path)
    now = time.time()
    since = now - window
    try:
        tasks_succeeded = db.one(
            conn, "SELECT COUNT(*) n FROM tasks WHERE state='succeeded' AND finished_at > ?", (since,)
        )["n"]
        failed_in_window = db.one(
            conn, "SELECT COUNT(*) n FROM tasks WHERE state='failed' AND finished_at > ?", (since,)
        )["n"]
        queued = db.one(conn, "SELECT COUNT(*) n FROM tasks WHERE state='queued'")["n"]
        jobs_total = db.one(conn, "SELECT COUNT(*) n FROM jobs")["n"]
        jobs_running = db.one(conn, "SELECT COUNT(*) n FROM jobs WHERE state IN ('queued','running')")["n"]
        retries = db.one(
            conn, "SELECT COALESCE(SUM(attempt-1),0) n FROM tasks WHERE state='succeeded' AND finished_at > ?", (since,)
        )["n"]
        durations = [
            r["duration"]
            for r in db.rows(
                conn,
                "SELECT duration FROM tasks WHERE state='succeeded' AND finished_at > ? AND duration IS NOT NULL ORDER BY duration",
                (since,),
            )
        ]
        workers = db.rows(conn, "SELECT * FROM workers ORDER BY state, name")
        online = sum(1 for w in workers if w["state"] != "offline")
        per_worker: list[WorkerStats] = []
        for w in workers:
            series = db.rows(
                conn,
                "SELECT ts,cpu_pct,gpu_pct FROM worker_stats WHERE worker_id=? AND ts > ? ORDER BY ts",
                (w["id"], since),
            )
            step = max(1, len(series) // 120)
            points = [
                UtilPoint(ts=r["ts"], cpu_pct=r["cpu_pct"], gpu_pct=r["gpu_pct"]) for r in series[::step]
            ]
            cpus = [r["cpu_pct"] for r in series if r["cpu_pct"] is not None]
            gpus = [r["gpu_pct"] for r in series if r["gpu_pct"] is not None]
            per_worker.append(
                WorkerStats(
                    worker_id=w["id"],
                    name=w["name"],
                    util_series=points,
                    mean_cpu_pct=round(sum(cpus) / len(cpus), 1) if cpus else None,
                    mean_gpu_pct=round(sum(gpus) / len(gpus), 1) if gpus else None,
                )
            )
        stats = PoolStats(
            now=now,
            window_s=window,
            workers_online=online,
            jobs_total=int(jobs_total),
            jobs_running=int(jobs_running),
            tasks_succeeded=int(tasks_succeeded),
            tasks_failed=int(failed_in_window),
            tasks_queued=int(queued),
            tasks_per_min=round(tasks_succeeded / (window / 60.0), 2),
            p50_duration_s=round(durations[len(durations) // 2], 3) if durations else None,
            p95_duration_s=round(durations[int(len(durations) * 0.95) - 1 if len(durations) > 1 else 0], 3)
            if durations
            else None,
            retries=int(retries),
            workers=per_worker,
            bottleneck=_bottleneck(online, queued, int(failed_in_window), int(retries), per_worker),
        )
    finally:
        conn.close()
    return stats


def _bottleneck(online: int, queued: int, failed: int, retries: int, workers: list[WorkerStats]) -> str | None:
    if online == 0:
        return "没有在线设备：先启动 worker"
    if queued > 0 and online > 0:
        idle = [w.name or w.worker_id for w in workers if (w.mean_cpu_pct or 0) < 40]
        if idle:
            return f"有任务排队，但设备 {', '.join(idle[:3])} CPU 利用率偏低：检查 workload/tag 覆盖或单任务耗时"
    if retries > 0 and failed > 0:
        return "出现重试/失败：检查租约 TTL、设备稳定性或模型依赖"
    return None


@router.get("/blobs/{sha}")
def get_blob(request: Request, sha: str) -> Response:
    bs = blobs_of(request)
    path = bs.path_for(sha)
    if not path.exists():
        raise _err(404, "not_found", f"blob {sha} not found")
    return FileResponse(path, media_type="application/octet-stream", filename=sha)


@router.get("/events")
def list_events(request: Request, limit: int = Query(100, ge=1, le=1000)) -> dict[str, Any]:
    s = settings_of(request)
    conn = db.reading(s.db_path)
    try:
        rows = db.rows(conn, "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))
    finally:
        conn.close()
    return {"events": [dict(r) for r in rows]}


# --------------------------------------------------------------------- helpers
def _counts(conn: sqlite3.Connection, job_id: str) -> dict[str, int]:
    counts = {str(r["state"]): int(r["n"]) for r in db.rows(
        conn, "SELECT state, COUNT(*) n FROM tasks WHERE job_id=? GROUP BY state", (job_id,)
    )}
    total = sum(counts.values())
    queued = counts.get(TaskState.QUEUED, 0) + counts.get(TaskState.LEASED, 0)
    return {
        "total": total,
        "succeeded": counts.get(TaskState.SUCCEEDED, 0),
        "failed": counts.get(TaskState.FAILED, 0),
        "queued": queued,
        "canceled": counts.get(TaskState.CANCELED, 0),
    }


def _summary(job: sqlite3.Row, counts: dict[str, int]) -> JobSummary:
    return JobSummary(
        job_id=job["id"],
        name=job["name"],
        workload=job["workload"],
        state=job["state"],
        total=counts["total"],
        succeeded=counts["succeeded"],
        failed=counts["failed"],
        queued=counts["queued"],
        created_at=job["created_at"],
        finished_at=job["finished_at"],
    )


def _per_worker(
    conn: sqlite3.Connection, job_id: str, total_succeeded: int, names: dict[str, str]
) -> list[PerWorkerStat]:
    rows = db.rows(
        conn,
        """SELECT worker_id, COUNT(*) n, AVG(duration) d, SUM(duration) t
           FROM tasks WHERE job_id=? AND state='succeeded' AND worker_id IS NOT NULL
           GROUP BY worker_id ORDER BY n DESC""",
        (job_id,),
    )
    failed = {
        r["worker_id"]: r["n"]
        for r in db.rows(
            conn,
            "SELECT worker_id, COUNT(*) n FROM tasks WHERE job_id=? AND state='failed' GROUP BY worker_id",
            (job_id,),
        )
    }
    out = []
    for r in rows:
        n = int(r["n"])
        out.append(
            PerWorkerStat(
                worker_id=r["worker_id"],
                name=names.get(r["worker_id"]),
                completed=n,
                failed=int(failed.get(r["worker_id"], 0)),
                mean_duration_s=round(float(r["d"]), 3) if r["d"] else None,
                total_duration_s=round(float(r["t"] or 0.0), 3),
                share=round(n / total_succeeded, 4) if total_succeeded else 0.0,
            )
        )
    return out


def _eta(conn: sqlite3.Connection, job: sqlite3.Row, counts: dict[str, int]) -> float | None:
    remaining = counts["total"] - counts["succeeded"] - counts["failed"] - counts["canceled"]
    if remaining <= 0:
        return 0.0
    row = db.one(
        conn,
        "SELECT COUNT(*) n, MIN(finished_at) a, MAX(finished_at) b FROM tasks WHERE job_id=? AND state='succeeded'",
        (job["id"],),
    )
    if not row or not row["n"] or row["a"] is None:
        return None
    span = max((row["b"] or 0) - (row["a"] or 0), 0.001)
    rate = float(row["n"]) / span
    if rate <= 0:
        return None
    return round(remaining / rate, 1)


def _manifest(conn: sqlite3.Connection, job: sqlite3.Row, counts: dict[str, int]) -> JobManifest:
    names = {r["id"]: r["name"] for r in db.rows(conn, "SELECT id,name FROM workers")}
    entries = []
    for r in db.rows(conn, "SELECT * FROM tasks WHERE job_id=? ORDER BY idx", (job["id"],)):
        out = json.loads(r["output"]) if r["output"] else {}
        entries.append(
            ManifestEntry(
                task_id=r["id"],
                idx=r["idx"],
                name=(json.loads(r["inputs"] or "[]") or [{}])[0].get("name"),
                worker_id=r["worker_id"],
                worker_name=names.get(r["worker_id"] or ""),
                attempt=r["attempt"],
                duration_s=r["duration"],
                output_sha256=r["output_sha"],
                output_files=[f["name"] for f in out.get("files", [])],
                error=r["error"],
            )
        )
    return JobManifest(
        job_id=job["id"],
        name=job["name"],
        workload=job["workload"],
        params=json.loads(job["params"] or "{}"),
        state=job["state"],
        total=counts["total"],
        succeeded=counts["succeeded"],
        failed=counts["failed"],
        created_at=job["created_at"],
        finished_at=job["finished_at"],
        generated_at=time.time(),
        entries=entries,
    )
