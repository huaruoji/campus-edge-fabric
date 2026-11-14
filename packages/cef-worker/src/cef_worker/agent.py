"""The worker agent main loop.

    register -> { heartbeat thread | poll loop -> ThreadPoolExecutor(slots) }

Everything the coordinator knows about a device comes from here: capabilities on
register, load on every heartbeat, and one row per task attempt on completion.
"""

from __future__ import annotations

import logging
import os
import platform
import shutil
import socket
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any

from . import __version__
from .client import ApiError, WorkerAPI
from .config import WorkerConfig
from .monitor import ResourceMonitor
from .workloads import TaskContext, Workload, WorkloadError, load_adapters

log = logging.getLogger("cef.worker")


def _safe_name(name: str) -> str:
    return os.path.basename(name).replace("..", "_") or "input"


class Agent:
    def __init__(self, cfg: WorkerConfig) -> None:
        self.cfg = cfg
        self.api = WorkerAPI(cfg.server, cfg.token)
        self.monitor = ResourceMonitor()
        self.adapters: dict[str, Workload] = load_adapters(
            enabled=cfg.workloads or None, model_dir=cfg.model_dir, blender_bin=cfg.blender_bin
        )
        self.worker_id: str | None = None
        self.slots = max(1, cfg.slots)
        self.max_batch = max(1, cfg.max_batch)
        self.suggested_capacity = self.max_batch
        self._executor = ThreadPoolExecutor(max_workers=self.slots, thread_name_prefix="cef-task")
        self._active: dict[str, Future[Any]] = {}
        self._active_lock = threading.Lock()
        self._stop = threading.Event()
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None
        self._drain_requested = threading.Event()
        self.completed = 0
        self.failed = 0
        self.last_sample = self.monitor.sample()

    # ------------------------------------------------------------------ setup
    def _register_payload(self) -> dict[str, Any]:
        specs = []
        for adapter in self.adapters.values():
            try:
                spec = adapter.probe()
            except Exception as exc:  # noqa: BLE001
                spec = {"kind": adapter.kind, "available": False, "reason": str(exc)}
            specs.append(
                {
                    "kind": adapter.kind,
                    "available": bool(spec.get("available")),
                    "version": spec.get("version"),
                    "reason": spec.get("reason"),
                }
            )
        hostname = socket.gethostname()
        name = self.cfg.name or hostname
        return {
            "worker_id": self.worker_id,
            "name": name,
            "hostname": hostname,
            "os": f"{platform.system()} {platform.release()}",
            "cpu_count": os.cpu_count() or 1,
            "tags": self.cfg.tags,
            "slots": self.slots,
            "max_batch": self.max_batch,
            "workloads": specs,
            "meta": {
                "python": platform.python_version(),
                "agent": __version__,
                "machine": platform.machine(),
                "gpu": self.last_sample.gpu_name,
            },
        }

    def register(self) -> None:
        resp = self.api.register(self._register_payload())
        self.worker_id = resp["worker_id"]
        self.cfg.heartbeat_interval_s = max(0.1, float(resp["heartbeat_interval_s"]))
        self.cfg.poll_interval_s = max(0.1, float(resp["poll_interval_s"]))
        self.suggested_capacity = self.max_batch
        log.info(
            "registered as %s (%s) with workloads: %s",
            self.worker_id,
            self.cfg.server,
            ", ".join(f"{a.kind}{'' if a.probe().get('available') else '(unavailable)'}" for a in self.adapters.values()),
        )

    # -------------------------------------------------------------- heartbeat
    def _heartbeat_loop(self) -> None:
        while not self._heartbeat_stop.is_set():
            try:
                sample = self.monitor.sample()
                self.last_sample = sample
                with self._active_lock:
                    active = list(self._active.keys())
                body = {
                    "cpu_pct": sample.cpu_pct,
                    "mem_pct": sample.mem_pct,
                    "gpu_pct": sample.gpu_pct,
                    "running": len(active),
                }
                resp = self.api.heartbeat(self.worker_id or "", body, active)
                if resp.get("drain"):
                    self._drain_requested.set()
            except ApiError as exc:
                if exc.status == 404:
                    log.warning("coordinator forgot this worker, re-registering")
                    try:
                        self.register()
                    except Exception:  # noqa: BLE001
                        log.warning("re-register failed, will retry")
                else:
                    log.warning("heartbeat failed: %s", exc)
            except Exception as exc:  # noqa: BLE001
                log.warning("heartbeat error: %s", exc)
            self._heartbeat_stop.wait(self.cfg.heartbeat_interval_s)

    # -------------------------------------------------------------------- loop
    def run(self) -> None:
        self.cfg.ensure_dirs()
        self.register()
        hb = threading.Thread(target=self._heartbeat_loop, name="cef-heartbeat", daemon=True)
        self._heartbeat_thread = hb
        hb.start()
        log.info("slots=%d max_batch=%d tags=%s", self.slots, self.max_batch, self.cfg.tags or "-")
        try:
            while not self._stop.is_set():
                self._reap_finished()
                if self._drain_requested.is_set() and not self._active:
                    log.info("drained, exiting")
                    break
                capacity = self._capacity()
                if capacity > 0:
                    self._lease(capacity)
                self._stop.wait(self.cfg.poll_interval_s)
        except KeyboardInterrupt:
            log.info("interrupted")
        finally:
            self.shutdown()

    def _capacity(self) -> int:
        if self._stop.is_set() or self._drain_requested.is_set():
            return 0
        with self._active_lock:
            running = len(self._active)
        free = self.slots - running
        if free <= 0:
            return 0
        sample = self.last_sample
        cpu = sample.cpu_pct or 0.0
        gpu = sample.gpu_pct or 0.0
        if cpu > self.cfg.max_cpu_pct or gpu > self.cfg.max_gpu_pct:
            return 1 if running == 0 else 0  # owner is using the machine: take at most one task
        return max(1, min(free, self.suggested_capacity, self.max_batch))

    def _lease(self, capacity: int) -> None:
        available = [kind for kind, a in self.adapters.items() if a.available()]
        if not available:
            return
        try:
            resp = self.api.poll(self.worker_id or "", capacity, available, self.cfg.tags)
        except ApiError as exc:
            log.warning("poll failed: %s", exc)
            return
        except Exception as exc:  # noqa: BLE001
            log.warning("poll error: %s", exc)
            time.sleep(1.0)
            return
        self.suggested_capacity = max(1, min(self.max_batch, int(resp.get("suggested_capacity") or self.max_batch)))
        leases = resp.get("leases") or []
        if leases:
            log.info("leased %d task(s) (capacity %d)", len(leases), capacity)
        for lease in leases:
            with self._active_lock:
                self._active[lease["task_id"]] = self._executor.submit(self._execute, lease)

    def _reap_finished(self) -> None:
        with self._active_lock:
            done = [tid for tid, fut in self._active.items() if fut.done()]
            for tid in done:
                fut = self._active.pop(tid)
                try:
                    fut.result()
                except Exception:  # noqa: BLE001 - already logged in _execute
                    pass

    # --------------------------------------------------------------- execution
    def _execute(self, lease: dict[str, Any]) -> None:
        task_id = lease["task_id"]
        kind = lease["workload"]
        adapter = self.adapters.get(kind)
        workdir = self.cfg.data_dir / "work" / task_id
        indir, outdir = workdir / "in", workdir / "out"
        indir.mkdir(parents=True, exist_ok=True)
        outdir.mkdir(parents=True, exist_ok=True)
        log_ = logging.getLogger(f"cef.task.{task_id[-6:]}")
        started = time.perf_counter()
        if adapter is None or not adapter.available():
            self._fail(lease, f"workload {kind} not available on this device", retryable=True)
            shutil.rmtree(workdir, ignore_errors=True)
            return
        try:
            inputs: list[Path] = []
            names: list[str] = []
            for i, ref in enumerate(lease.get("inputs") or []):
                safe = _safe_name(ref.get("name") or ref["sha256"][:8])
                dest = indir / f"{i:02d}_{safe}"
                self.api.fetch_blob(ref["sha256"], dest)
                inputs.append(dest)
                names.append(ref.get("name") or safe)
            ctx = TaskContext(
                task_id=task_id,
                job_id=lease["job_id"],
                idx=int(lease["idx"]),
                attempt=int(lease["attempt"]),
                params=dict(lease.get("params") or {}),
                inputs=inputs,
                input_names=names,
                workdir=workdir,
                outdir=outdir,
                log=log_,
            )
            result = adapter.run(ctx)
            duration = result.duration_s or (time.perf_counter() - started)
            ok, reason = self.api.complete(
                self.worker_id or "",
                task_id,
                {"attempt": int(lease["attempt"]), "duration_s": round(duration, 4), "output": result.meta},
                result.files,
            )
            if ok:
                self.completed += 1
                log.info("✓ %s %s idx=%d %.3fs", kind, task_id[-8:], ctx.idx, duration)
            else:
                log.warning("result rejected (%s) for %s — lease lost, result discarded", reason, task_id)
        except WorkloadError as exc:
            log.warning("✗ %s %s: %s", kind, task_id[-8:], exc)
            self._fail(lease, str(exc), retryable=exc.retryable)
        except ApiError as exc:
            log.warning("✗ %s %s transport: %s", kind, task_id[-8:], exc)
            self._fail(lease, f"transport error: {exc}", retryable=True)
        except Exception as exc:  # noqa: BLE001
            log.exception("unexpected error running %s", task_id)
            self._fail(lease, f"{type(exc).__name__}: {exc}", retryable=True)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    def _fail(self, lease: dict[str, Any], error: str, retryable: bool) -> None:
        try:
            self.api.fail(self.worker_id or "", lease["task_id"], int(lease["attempt"]), error, retryable)
            self.failed += 1
        except ApiError as exc:
            log.warning("could not report failure for %s: %s", lease["task_id"], exc)

    # ---------------------------------------------------------------- shutdown
    def shutdown(self) -> None:
        self._stop.set()
        with self._active_lock:
            pending = list(self._active.keys())
        if pending:
            log.info("waiting for %d in-flight task(s)", len(pending))
        # Stop admitting work first, but keep leases alive until the final upload.
        # _stop controls polling; heartbeat has a separate lifecycle.
        try:
            self._executor.shutdown(wait=True, cancel_futures=False)
        finally:
            self._heartbeat_stop.set()
            if self._heartbeat_thread is not None:
                self._heartbeat_thread.join(timeout=65)
        if self.worker_id:
            try:
                self.api.drain(self.worker_id)
                log.info("drained: %d completed, %d failed locally", self.completed, self.failed)
            except Exception:  # noqa: BLE001
                pass
        self.api.close()
