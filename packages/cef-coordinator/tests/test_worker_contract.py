"""Worker SDK against the real ASGI coordinator, including fencing and errors."""
import json
import runpy
from pathlib import Path

import pytest
from cef_coordinator.app import create_app
from cef_coordinator.config import Settings
from cef_worker.client import WorkerAPI
from fastapi.testclient import TestClient


@pytest.fixture()
def wire(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, token="contract-test", max_attempts=2))
    with TestClient(app) as http:
        api = WorkerAPI("http://testserver", "contract-test")
        api.close()
        api._client = http
        http.headers["Authorization"] = "Bearer contract-test"
        yield api, http


def register(api, name="one"):
    return api.register({"name": name, "workloads": [{"kind": "thumbnail", "available": True}]})["worker_id"]


def submit(http):
    response = http.post("/api/v1/jobs", data={"spec": json.dumps({"workload": "thumbnail"})},
                         files={"files": ("input.txt", b"input-bytes", "application/octet-stream")})
    assert response.status_code == 201
    return response.json()["job_id"]


def test_real_sdk_round_trip_and_duplicate_fencing(wire, tmp_path):
    api, http = wire
    worker = register(api)
    job = submit(http)
    lease, = api.poll(worker, 1, ["thumbnail"], [])['leases']
    assert lease["attempt"] == 1
    assert api.heartbeat(worker, {"running": 1}, [lease["task_id"]])["drain"] is False
    dest = api.fetch_blob(lease["inputs"][0]["sha256"], tmp_path / "input")
    assert dest.read_bytes() == b"input-bytes"
    output = tmp_path / "output.txt"
    output.write_text("result", encoding="utf-8")
    meta = {"attempt": 1, "duration_s": 0.1, "output": {"test": True}}
    assert api.complete(worker, lease["task_id"], meta, [output]) == (True, None)
    assert api.complete(worker, lease["task_id"], meta, [output]) == (False, "stale_attempt")
    manifest = http.get(f"/api/v1/jobs/{job}/manifest").json()
    assert manifest["succeeded"] == 1
    assert manifest["entries"][0]["worker_id"] == worker


def test_capacity_capability_drain_and_attempts(wire):
    api, http = wire
    worker = register(api)
    submit(http)
    assert api.poll(worker, 0, ["thumbnail"], [])["leases"] == []
    assert api.poll(worker, 1, ["infer"], [])["leases"] == []
    lease, = api.poll(worker, 1, ["thumbnail"], [])["leases"]
    assert api.fail(worker, lease["task_id"], 1, "retry", True) == {"requeued": True, "attempts_left": 1}
    next_lease, = api.poll(worker, 1, ["thumbnail"], [])["leases"]
    assert next_lease["attempt"] == 2
    assert api.complete(worker, lease["task_id"], {"attempt": 1, "duration_s": 1}, []) == (False, "stale_attempt")
    api.drain(worker)
    assert api.poll(worker, 1, ["thumbnail"], [])["leases"] == []
    assert api.heartbeat(worker, {}, [])["drain"] is True


@pytest.mark.parametrize("path,body,status,code", [
    ("/workers/register", {}, 422, "invalid_params"),
    ("/workers/missing/heartbeat", {}, 404, "not_found"),
    ("/workers/missing/poll", {"capacity": 65}, 422, "invalid_params"),
])
def test_error_envelope(wire, path, body, status, code):
    _, http = wire
    response = http.post("/api/v1" + path, json=body)
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert "detail" not in response.json()


def test_unauthorized_and_bad_complete_meta(wire):
    api, http = wire
    assert http.post("/api/v1/workers/register", json={"name": "x"},
                     headers={"Authorization": "Bearer invalid"}).status_code == 401
    worker = register(api)
    response = http.post(f"/api/v1/workers/{worker}/tasks/missing/complete", data={"meta": "not-json"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_params"


def test_frozen_schema_matches_shared_models():
    root = Path(__file__).resolve().parents[3]
    generated = runpy.run_path(str(root / "scripts/export_worker_schema.py"))["snapshot"]()
    assert generated == json.loads((root / "docs/worker-schema-v1.json").read_text(encoding="utf-8"))
