"""The scheduler: pull-based leases with adaptive batching and fencing.

Design notes (these are the parts worth defending in a review):

* **Pull, not push.** A worker asks for work when it has capacity, so a fast or
  idle machine automatically takes more tasks. No coordinator-side knowledge of
  "how busy is that laptop right now" is needed for correctness.
* **Lease + fencing token.** Every grant increments `attempt`. A result is only
  credited when `(task_id, attempt, worker_id)` still matches the live lease.
  Retried tasks therefore cannot be double-counted, and a zombie worker that
  comes back after a network partition is rejected with 409 instead of corrupting
  the job.
* **Adaptive batch.** `capacity = target_lease_s / ewma(task_duration)`, clamped
  by the worker's `max_batch` and halved when the device reports high CPU/GPU
  load. Long tasks get capacity 1 (no point in leasing 8 frames to a 40 s render).
* **Recovery by expiry, not by notification.** A dead worker is indistinguishable
  from a slow one; we never rely on a "goodbye" message. When the lease expires
  the reaper requeues the task, up to `max_attempts`.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterable
from typing import Any

from cef_protocol import new_id
from cef_protocol.models import Lease
from cef_protocol.states import JobState, TaskState

from . import db
from .config import Settings


def _now() -> float:
    return time.time()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


# --------------------------------------------------------------------- workers
def upsert_worker(db_path, reg: dict[str, Any], settings: Settings) -> sqlite3.Row:
    now = _now()
    with db.transaction(db_path) as conn:
        existing = None
        if reg.get("worker_id"):
            existing = db.one(conn, "SELECT * FROM workers WHERE id=?", (reg["worker_id"],))
        if existing is None:
            worker_id = reg.get("worker_id") or new_id("w")
            conn.execute(
                """INSERT INTO workers (id,name,hostname,os,cpu_count,tags,workloads,slots,max_batch,
                                        state,last_seen,registered_at,meta)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    worker_id,
                    reg.get("name") or "worker",
                    reg.get("hostname", ""),
                    reg.get("os", ""),
                    int(reg.get("cpu_count") or 1),
                    _json(reg.get("tags") or []),
                    _json(reg.get("workloads") or []),
                    max(1, int(reg.get("slots") or 1)),
                    max(1, int(reg.get("max_batch") or 4)),
                    "online",
                    now,
                    now,
                    _json(reg.get("meta") or {}),
                ),
            )
            _event(conn, "worker_registered", worker_id=worker_id, message=reg.get("name", ""))
        else:
            worker_id = existing["id"]
            # A restart must not wipe tags an operator set through PATCH: only a
            # non-empty --tags on the command line is treated as an explicit intent.
            incoming = reg.get("tags") or []
            tags_json = _json(incoming) if incoming else (existing["tags"] or "[]")
            conn.execute(
                """UPDATE workers SET name=?,hostname=?,os=?,cpu_count=?,tags=?,workloads=?,
                       slots=?,max_batch=?,state='online',last_seen=?,meta=? WHERE id=?""",
                (
                    reg.get("name") or existing["name"],
                    reg.get("hostname", existing["hostname"]),
                    reg.get("os", existing["os"]),
                    int(reg.get("cpu_count") or existing["cpu_count"] or 1),
                    tags_json,
                    _json(reg.get("workloads") or []),
                    max(1, int(reg.get("slots") or existing["slots"])),
                    max(1, int(reg.get("max_batch") or existing["max_batch"])),
                    now,
                    _json(reg.get("meta") or {}),
                    worker_id,
                ),
            )
            _event(conn, "worker_reconnected", worker_id=worker_id)
        row = db.one(conn, "SELECT * FROM workers WHERE id=?", (worker_id,))
    assert row is not None
    return row


def heartbeat(
    db_path, worker_id: str, stats: dict[str, Any], lease_ids: Iterable[str], settings: Settings
) -> sqlite3.Row | None:
    now = _now()
    ttl = settings.lease_ttl_s
    with db.transaction(db_path) as conn:
        row = db.one(conn, "SELECT * FROM workers WHERE id=?", (worker_id,))
        if row is None:
            return None
        conn.execute(
            "UPDATE workers SET last_seen=?, state=CASE WHEN state='offline' THEN 'online' ELSE state END WHERE id=?",
            (now, worker_id),
        )
        conn.execute(
            """INSERT INTO worker_stats (worker_id, ts, cpu_pct, mem_pct, gpu_pct,
                                         disk_free_gb, disk_total_gb, running)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                worker_id,
                now,
                stats.get("cpu_pct"),
                stats.get("mem_pct"),
                stats.get("gpu_pct"),
                stats.get("disk_free_gb"),
                stats.get("disk_total_gb"),
                int(stats.get("running") or 0),
            ),
        )
        lease_ids = list(lease_ids)
        if lease_ids:
            marks = ",".join("?" * len(lease_ids))
            conn.execute(
                f"""UPDATE tasks SET lease_expires=?, updated_at=?
                    WHERE worker_id=? AND state='leased' AND id IN ({marks})""",
                (now + ttl, now, worker_id, *lease_ids),
            )
        return db.one(conn, "SELECT * FROM workers WHERE id=?", (worker_id,))


def set_state(db_path, worker_id: str, state: str) -> None:
    with db.transaction(db_path) as conn:
        conn.execute("UPDATE workers SET state=? WHERE id=?", (state, worker_id))


def release_leases(db_path, worker_id: str, reason: str) -> int:
    """Graceful drain: push everything this worker holds (and has not started
    reporting on) back into the queue."""
    now = _now()
    with db.transaction(db_path) as conn:
        rows = db.rows(
            conn,
            "SELECT id, job_id, attempt FROM tasks WHERE worker_id=? AND state='leased'",
            (worker_id,),
        )
        for r in rows:
            conn.execute(
                """UPDATE tasks SET state='queued', worker_id=NULL, lease_expires=NULL,
                       started_at=NULL, updated_at=? WHERE id=? AND state='leased'""",
                (now, r["id"]),
            )
            conn.execute(
                "UPDATE leases SET closed_at=?, outcome=? WHERE task_id=? AND attempt=? AND closed_at IS NULL",
                (now, reason, r["id"], r["attempt"]),
            )
            _sync_job(conn, r["job_id"])
        return len(rows)


# ------------------------------------------------------------------------ claim
def suggested_capacity(worker: sqlite3.Row, settings: Settings) -> int:
    """Adaptive batch size: aim for `target_lease_s` of work per poll."""
    speed = worker["speed_s"]
    max_batch = max(1, int(worker["max_batch"] or 1))
    if not speed or speed <= 0:
        return max_batch
    want = int(round(settings.target_lease_s / float(speed)))
    return max(1, min(max_batch, want))


def claim(
    db_path, settings: Settings, worker: sqlite3.Row, capacity: int, workloads: list[str]
) -> list[Lease]:
    """Atomically lease up to `capacity` queued tasks compatible with this worker."""
    capacity = max(0, min(int(capacity), settings.poll_limit))
    if capacity == 0 or not workloads:
        return []
    now = _now()
    ttl = settings.lease_ttl_s
    tags: list[str] = json.loads(worker["tags"] or "[]")
    leases: list[Lease] = []
    with db.transaction(db_path) as conn:
        wl_marks = ",".join("?" * len(workloads))
        tag_clause = "j.required_tag IS NULL OR j.required_tag=''"
        args: list[Any] = [*workloads]
        if tags:
            tag_marks = ",".join("?" * len(tags))
            tag_clause += f" OR j.required_tag IN ({tag_marks})"
            args.extend(tags)
        args.append(capacity)
        candidates = db.rows(
            conn,
            f"""SELECT t.id AS task_id, t.job_id, t.idx, t.attempt, t.max_attempts,
                       t.params, t.inputs, j.workload
                FROM tasks t JOIN jobs j ON j.id = t.job_id
                WHERE t.state='queued'
                  AND j.state IN ('queued','running')
                  AND j.workload IN ({wl_marks})
                  AND ({tag_clause})
                ORDER BY j.priority DESC, j.created_at ASC, t.idx ASC
                LIMIT ?""",
            tuple(args),
        )
        for cand in candidates:
            attempt = int(cand["attempt"]) + 1
            updated = conn.execute(
                """UPDATE tasks SET state='leased', attempt=?, worker_id=?, lease_expires=?,
                       started_at=COALESCE(started_at, ?), updated_at=?
                   WHERE id=? AND state='queued'""",
                (attempt, worker["id"], now + ttl, now, now, cand["task_id"]),
            ).rowcount
            if not updated:
                continue  # lost the race against another worker, keep going
            conn.execute(
                "INSERT INTO leases (task_id, worker_id, attempt, granted_at, expires_at) VALUES (?,?,?,?,?)",
                (cand["task_id"], worker["id"], attempt, now, now + ttl),
            )
            _sync_job(conn, cand["job_id"])
            inputs = []
            for ref in json.loads(cand["inputs"] or "[]"):
                inputs.append(
                    {
                        "sha256": ref["sha256"],
                        "name": ref.get("name", ref["sha256"][:8]),
                        "size": int(ref.get("size") or 0),
                        "url": f"/api/v1/blobs/{ref['sha256']}",
                    }
                )
            leases.append(
                Lease(
                    task_id=cand["task_id"],
                    job_id=cand["job_id"],
                    idx=int(cand["idx"]),
                    attempt=attempt,
                    workload=cand["workload"],
                    params=json.loads(cand["params"] or "{}"),
                    inputs=inputs,
                    lease_expires_at=now + ttl,
                    lease_ttl_s=ttl,
                )
            )
            if len(leases) >= capacity:
                break
    return leases


# ------------------------------------------------------------------ completion
def complete_task(
    db_path,
    settings: Settings,
    worker_id: str,
    task_id: str,
    attempt: int,
    duration_s: float,
    output: dict[str, Any],
    output_sha: str | None,
) -> tuple[bool, str | None]:
    now = _now()
    with db.transaction(db_path) as conn:
        row = db.one(conn, "SELECT * FROM tasks WHERE id=?", (task_id,))
        if row is None:
            return False, "not_found"
        if row["state"] != TaskState.LEASED or int(row["attempt"]) != int(attempt) or row["worker_id"] != worker_id:
            _event(
                conn,
                "stale_result",
                worker_id=worker_id,
                job_id=row["job_id"],
                task_id=task_id,
                message=f"attempt {attempt} != live {row['attempt']} state={row['state']}",
            )
            return False, "stale_attempt"
        finished = now
        conn.execute(
            """UPDATE tasks SET state='succeeded', finished_at=?, duration=?, output=?, output_sha=?,
                   error=NULL, lease_expires=NULL, updated_at=?
               WHERE id=? AND attempt=? AND state='leased'""",
            (finished, max(0.0, float(duration_s)), _json(output), output_sha, finished, task_id, attempt),
        )
        conn.execute(
            "UPDATE leases SET closed_at=?, outcome='succeeded' WHERE task_id=? AND attempt=?",
            (finished, task_id, attempt),
        )
        conn.execute(
            """UPDATE workers SET speed_s = CASE WHEN speed_s IS NULL THEN ? ELSE 0.7*speed_s + 0.3*? END
               WHERE id=?""",
            (float(duration_s), float(duration_s), worker_id),
        )
        _sync_job(conn, row["job_id"])
    return True, None


def fail_task(
    db_path,
    settings: Settings,
    worker_id: str,
    task_id: str,
    attempt: int,
    error: str,
    retryable: bool,
) -> tuple[bool, bool, int]:
    """Returns (accepted, requeued, attempts_left)."""
    now = _now()
    with db.transaction(db_path) as conn:
        row = db.one(conn, "SELECT * FROM tasks WHERE id=?", (task_id,))
        if row is None:
            return False, False, 0
        if row["state"] != TaskState.LEASED or int(row["attempt"]) != int(attempt) or row["worker_id"] != worker_id:
            return False, False, 0
        attempts_left = max(0, int(row["max_attempts"]) - int(row["attempt"]))
        requeue = bool(retryable) and attempts_left > 0
        conn.execute(
            "UPDATE leases SET closed_at=?, outcome=? WHERE task_id=? AND attempt=?",
            (now, "requeued" if requeue else "failed", task_id, attempt),
        )
        if requeue:
            conn.execute(
                """UPDATE tasks SET state='queued', worker_id=NULL, lease_expires=NULL, error=?,
                       updated_at=? WHERE id=? AND attempt=?""",
                (error[:500], now, task_id, attempt),
            )
        else:
            conn.execute(
                """UPDATE tasks SET state='failed', finished_at=?, error=?, worker_id=NULL,
                       lease_expires=NULL, updated_at=? WHERE id=? AND attempt=?""",
                (now, error[:500], now, task_id, attempt),
            )
        _event(
            conn,
            "task_requeued" if requeue else "task_failed",
            worker_id=worker_id,
            job_id=row["job_id"],
            task_id=task_id,
            message=error[:200],
        )
        _sync_job(conn, row["job_id"])
    return True, requeue, attempts_left


# ------------------------------------------------------------------- recovery
def reap_expired_leases(db_path, settings: Settings) -> int:
    now = _now()
    requeued = 0
    with db.transaction(db_path) as conn:
        expired = db.rows(
            conn, "SELECT * FROM tasks WHERE state='leased' AND lease_expires IS NOT NULL AND lease_expires < ?", (now,)
        )
        if not expired:
            return 0
        for row in expired:
            attempts_left = max(0, int(row["max_attempts"]) - int(row["attempt"]))
            conn.execute(
                "UPDATE leases SET closed_at=?, outcome='expired' WHERE task_id=? AND attempt=? AND closed_at IS NULL",
                (now, row["id"], row["attempt"]),
            )
            if attempts_left > 0:
                requeued += 1
                conn.execute(
                    """UPDATE tasks SET state='queued', worker_id=NULL, lease_expires=NULL, started_at=NULL,
                           error=?, updated_at=? WHERE id=? AND state='leased'""",
                    ("lease expired", now, row["id"]),
                )
                _event(
                    conn,
                    "lease_expired",
                    worker_id=row["worker_id"],
                    job_id=row["job_id"],
                    task_id=row["id"],
                    message=f"attempt {row['attempt']} expired",
                )
            else:
                conn.execute(
                    """UPDATE tasks SET state='failed', worker_id=NULL, lease_expires=NULL,
                           finished_at=?, error=?, updated_at=? WHERE id=? AND state='leased'""",
                    (now, "lease expired, out of attempts", now, row["id"]),
                )
                _event(
                    conn,
                    "task_failed",
                    worker_id=row["worker_id"],
                    job_id=row["job_id"],
                    task_id=row["id"],
                    message="lease expired, out of attempts",
                )
            _sync_job(conn, row["job_id"])
    return requeued


def sweep_workers(db_path, settings: Settings) -> None:
    cutoff = _now() - settings.offline_after_s
    with db.transaction(db_path) as conn:
        conn.execute(
            "UPDATE workers SET state='offline' WHERE state IN ('online','draining') AND last_seen IS NOT NULL AND last_seen < ?",
            (cutoff,),
        )


def prune_stats(db_path, settings: Settings) -> None:
    cutoff = _now() - settings.stats_retention_s
    with db.transaction(db_path) as conn:
        conn.execute("DELETE FROM worker_stats WHERE ts < ?", (cutoff,))
        conn.execute("DELETE FROM events WHERE ts < ?", (cutoff,))


def _sync_job(conn: sqlite3.Connection, job_id: str) -> None:
    """Recompute derived job state after any task transition."""
    job = db.one(conn, "SELECT * FROM jobs WHERE id=?", (job_id,))
    if job is None or job["state"] == JobState.CANCELED:
        return
    counts = {r["state"]: r["n"] for r in db.rows(conn, "SELECT state, COUNT(*) n FROM tasks WHERE job_id=? GROUP BY state", (job_id,))}
    total = sum(counts.values())
    succeeded = counts.get(TaskState.SUCCEEDED, 0)
    failed = counts.get(TaskState.FAILED, 0) + counts.get(TaskState.CANCELED, 0)
    state = _classify(succeeded, failed, total)
    now = _now()
    started = job["started_at"]
    if started is None and (counts.get(TaskState.LEASED, 0) or succeeded):
        started = now
    finished = now if total and succeeded + failed >= total else None
    conn.execute(
        "UPDATE jobs SET state=?, started_at=?, finished_at=? WHERE id=?",
        (str(state), started, finished, job_id),
    )


def _classify(succeeded: int, failed: int, total: int) -> JobState:
    if total == 0:
        return JobState.FAILED
    if succeeded == total:
        return JobState.SUCCEEDED
    if succeeded + failed >= total:
        return JobState.PARTIAL if succeeded else JobState.FAILED
    return JobState.RUNNING


def _event(conn, kind: str, **fields: Any) -> None:
    conn.execute(
        "INSERT INTO events (ts, kind, worker_id, job_id, task_id, message) VALUES (?,?,?,?,?,?)",
        (
            _now(),
            kind,
            fields.get("worker_id"),
            fields.get("job_id"),
            fields.get("task_id"),
            fields.get("message"),
        ),
    )


def forget_worker(db_path, worker_id: str) -> bool:
    """Remove a device record (dashboard cleanup).

    Any lease it still holds is requeued first, so deleting a card can never
    strand a task. Utilization samples are dropped with it.
    """
    release_leases(db_path, worker_id, "worker_forgotten")
    with db.transaction(db_path) as conn:
        row = db.one(conn, "SELECT id FROM workers WHERE id=?", (worker_id,))
        if row is None:
            return False
        conn.execute("DELETE FROM worker_stats WHERE worker_id=?", (worker_id,))
        conn.execute("DELETE FROM workers WHERE id=?", (worker_id,))
        _event(conn, "worker_forgotten", worker_id=worker_id)
    return True


# ------------------------------------------------------------------ disk / gc
def blob_usage(db_path) -> tuple[int, int]:
    """(bytes, count) currently held by the blob store."""
    conn = db.reading(db_path)
    try:
        row = db.one(conn, "SELECT COALESCE(SUM(size),0) b, COUNT(*) n FROM blobs")
        return int(row["b"] or 0), int(row["n"] or 0)
    finally:
        conn.close()


def referenced_blobs(conn) -> set[str]:
    """Every sha still needed by some task (input or output)."""
    keep: set[str] = set()
    for row in db.rows(conn, "SELECT inputs, output, output_sha FROM tasks"):
        try:
            for ref in json.loads(row["inputs"] or "[]"):
                if ref.get("sha256"):
                    keep.add(ref["sha256"])
        except ValueError:
            pass
        try:
            payload = json.loads(row["output"] or "{}")
            for f in payload.get("files", []):
                if f.get("sha256"):
                    keep.add(f["sha256"])
        except ValueError:
            pass
        if row["output_sha"]:
            keep.add(row["output_sha"])
    return keep


def gc_blobs(db_path, settings, blob_dir) -> tuple[int, int]:
    """Delete blobs that are old AND no longer referenced by any task.

    Returns (deleted_count, deleted_bytes). Safe to run while jobs are in flight:
    anything a live task points at is kept, and queued tasks always have inputs.
    """
    cutoff = _now() - settings.blob_retention_days * 86400
    deleted = 0
    freed = 0
    stale: list[tuple[str, int]] = []
    with db.transaction(db_path) as conn:
        keep = referenced_blobs(conn)
        for row in db.rows(conn, "SELECT sha256, size FROM blobs WHERE created_at < ?", (cutoff,)):
            if row["sha256"] not in keep:
                stale.append((row["sha256"], int(row["size"] or 0)))
        for sha, _size in stale:
            conn.execute("DELETE FROM blobs WHERE sha256=?", (sha,))
    for sha, size in stale:
        path = blob_dir / sha[:2] / sha[2:4] / sha
        try:
            if path.exists():
                path.unlink()
                deleted += 1
                freed += size
        except OSError:
            continue
    if deleted:
        with db.transaction(db_path) as conn:
            _event(conn, "blobs_gc", message=f"deleted {deleted} blobs, {freed / 1e6:.1f} MB")
    return deleted, freed
