"""Thin sync HTTP client for the CEF coordinator.

Used by the `cef` CLI, by notebooks/SDK users and (for the read paths) by tests.
Keep it dependency-light: httpx only, no async.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import httpx

from .models import (
    Health,
    JobCreated,
    JobDetail,
    JobList,
    PoolStats,
    WorkerList,
    WorkloadList,
)

API = "/api/v1"


class CefError(RuntimeError):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(f"{status} {code}: {message}")
        self.status = status
        self.code = code
        self.message = message


class CefClient:
    def __init__(self, base_url: str, token: str | None = None, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        # trust_env=False: LAN/Tailscale traffic must not be routed through a
        # developer machine's HTTP proxy settings.
        self._client = httpx.Client(base_url=self.base_url, timeout=timeout, trust_env=False)

    # ------------------------------------------------------------------ plumbing
    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        h = {"Accept": "application/json"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        if extra:
            h.update(extra)
        return h

    def _raise(self, resp: httpx.Response) -> None:
        if resp.is_success:
            return
        code, message = "http_error", resp.text[:400]
        try:
            body = resp.json()
            info = body.get("error", body)
            code = info.get("code", code)
            message = info.get("message", message)
        except Exception:  # noqa: BLE001 - best effort error extraction
            pass
        raise CefError(resp.status_code, code, message)

    def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        resp = self._client.request(method, path, headers=self._headers(), **kwargs)
        self._raise(resp)
        return resp

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> CefClient:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # --------------------------------------------------------------------- reads
    def health(self) -> Health:
        return Health.model_validate(self.request("GET", f"{API}/health").json())

    def workloads(self) -> WorkloadList:
        return WorkloadList.model_validate(self.request("GET", f"{API}/workloads").json())

    def workers(self) -> WorkerList:
        return WorkerList.model_validate(self.request("GET", f"{API}/workers").json())

    def stats(self, window: int = 300) -> PoolStats:
        return PoolStats.model_validate(self.request("GET", f"{API}/stats", params={"window": window}).json())

    def list_jobs(self, limit: int = 50, state: str | None = None) -> JobList:
        params: dict[str, Any] = {"limit": limit}
        if state:
            params["state"] = state
        return JobList.model_validate(self.request("GET", f"{API}/jobs", params=params).json())

    def get_job(self, job_id: str) -> JobDetail:
        return JobDetail.model_validate(self.request("GET", f"{API}/jobs/{job_id}").json())

    # ------------------------------------------------------------------- actions
    def submit_job(
        self,
        files: Sequence[Path | str],
        workload: str,
        params: dict[str, Any] | None = None,
        name: str | None = None,
        priority: int = 0,
        required_tag: str | None = None,
        extra_spec: dict[str, Any] | None = None,
    ) -> JobCreated:
        paths = [Path(f) for f in files]
        spec: dict[str, Any] = {
            "workload": workload,
            "params": params or {},
            "name": name,
            "priority": priority,
            "required_tag": required_tag,
            "inputs": [{"name": p.name} for p in paths],
        }
        if extra_spec:
            spec.update(extra_spec)
        handles = []
        try:
            for p in paths:
                handles.append(("files", (p.name, p.open("rb"))))
            data = {"spec": json.dumps(spec)}
            resp = self._client.post(
                f"{API}/jobs",
                data=data,
                files=handles,
                headers=self._headers(),
                timeout=max(60.0, self._client.timeout.read or 60.0),
            )
            self._raise(resp)
            return JobCreated.model_validate(resp.json())
        finally:
            for _, (_, fh) in handles:
                fh.close()

    def cancel_job(self, job_id: str) -> None:
        self.request("POST", f"{API}/jobs/{job_id}/cancel")

    def manifest(self, job_id: str) -> dict[str, Any]:
        return self.request("GET", f"{API}/jobs/{job_id}/manifest").json()

    def download_result(self, job_id: str, dest: Path | str) -> Path:
        dest = Path(dest)
        resp = self._client.get(f"{API}/jobs/{job_id}/result", headers=self._headers())
        self._raise(resp)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(resp.content)
        return dest

    # --------------------------------------------------------------------- utils
    def wait(
        self, job_id: str, poll_s: float = 1.0, timeout_s: float | None = None
    ) -> Iterator[JobDetail]:
        """Yield job snapshots until the job reaches a terminal state."""
        deadline = None if timeout_s is None else time.monotonic() + timeout_s
        while True:
            job = self.get_job(job_id)
            yield job
            if job.state in ("succeeded", "partial", "failed", "canceled"):
                return
            if deadline is not None and time.monotonic() > deadline:
                raise TimeoutError(f"job {job_id} did not finish in {timeout_s}s")
            time.sleep(poll_s)
