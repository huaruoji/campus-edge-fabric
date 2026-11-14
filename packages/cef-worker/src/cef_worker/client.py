"""HTTP client used by the agent (worker side of API v1)."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import httpx
from cef_protocol.models import (
    CompleteMeta,
    CompleteResponse,
    DrainResponse,
    FailRequest,
    FailResponse,
    HeartbeatRequest,
    HeartbeatResponse,
    PollRequest,
    PollResponse,
    RegisterRequest,
    RegisterResponse,
)

API = "/api/v1"


class ApiError(RuntimeError):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(f"{status} {code}: {message}")
        self.status = status
        self.code = code
        self.message = message


class WorkerAPI:
    def __init__(self, server: str, token: str, timeout: float = 60.0) -> None:
        self.server = server.rstrip("/")
        self.token = token
        self._client = httpx.Client(base_url=self.server, timeout=timeout, trust_env=False)

    def close(self) -> None:
        self._client.close()

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def _call(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        resp = self._client.request(method, f"{API}{path}", headers=self._headers(), **kwargs)
        if resp.status_code >= 400:
            code, msg = "http_error", resp.text[:300]
            try:
                info = resp.json().get("error", {})
                code, msg = info.get("code", code), info.get("message", msg)
            except Exception:  # noqa: BLE001
                pass
            raise ApiError(resp.status_code, code, msg)
        return resp

    # ------------------------------------------------------------------ worker
    def register(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = RegisterRequest.model_validate(payload).model_dump(mode="json")
        return RegisterResponse.model_validate(self._call("POST", "/workers/register", json=body).json()).model_dump()

    def heartbeat(self, worker_id: str, stats: dict[str, Any], leases: list[str]) -> dict[str, Any]:
        body = HeartbeatRequest(stats=stats, leases=leases).model_dump(mode="json")
        response = self._call("POST", f"/workers/{worker_id}/heartbeat", json=body)
        return HeartbeatResponse.model_validate(response.json()).model_dump()

    def poll(self, worker_id: str, capacity: int, workloads: list[str], tags: list[str]) -> dict[str, Any]:
        body = PollRequest(capacity=capacity, workloads=workloads, tags=tags).model_dump(mode="json")
        response = self._call("POST", f"/workers/{worker_id}/poll", json=body)
        return PollResponse.model_validate(response.json()).model_dump()

    def complete(
        self, worker_id: str, task_id: str, meta: dict[str, Any], files: list[Path]
    ) -> tuple[bool, str | None]:
        meta = CompleteMeta.model_validate(meta).model_dump(mode="json")
        handles = []
        try:
            for f in files:
                handles.append(("files", (f.name, f.open("rb"), "application/octet-stream")))
            data = {"meta": CompleteMeta.model_validate(meta).model_dump_json()}
            resp = self._client.post(
                f"{API}/workers/{worker_id}/tasks/{task_id}/complete",
                data=data,
                files=handles,
                headers=self._headers(),
                timeout=300.0,
            )
        finally:
            for _, (_, fh, _ct) in handles:
                fh.close()
        if resp.status_code == 409:
            return False, "stale_attempt"
        if resp.status_code >= 400:
            raise ApiError(resp.status_code, "complete_failed", resp.text[:300])
        result = CompleteResponse.model_validate(resp.json())
        return result.ok and result.credited, result.reason

    def fail(self, worker_id: str, task_id: str, attempt: int, error: str, retryable: bool) -> dict[str, Any]:
        body = FailRequest(attempt=attempt, error=error[:2000], retryable=retryable).model_dump(mode="json")
        response = self._call("POST", f"/workers/{worker_id}/tasks/{task_id}/fail", json=body)
        return FailResponse.model_validate(response.json()).model_dump()

    def drain(self, worker_id: str) -> None:
        response = self._call("POST", f"/workers/{worker_id}/drain")
        DrainResponse.model_validate(response.json())

    # -------------------------------------------------------------------- blobs
    def fetch_blob(self, sha: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        with self._client.stream("GET", f"{API}/blobs/{sha}", headers=self._headers()) as resp:
            if resp.status_code >= 400:
                raise ApiError(resp.status_code, "blob_fetch_failed", sha)
            with dest.open("wb") as fh:
                for chunk in resp.iter_bytes():
                    digest.update(chunk)
                    fh.write(chunk)
        if digest.hexdigest() != sha:
            dest.unlink(missing_ok=True)
            raise ApiError(0, "checksum_mismatch", f"blob {sha} corrupted in transit")
        return dest

    def health(self) -> dict[str, Any]:
        return self._call("GET", "/health").json()
