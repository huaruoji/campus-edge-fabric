"""Chat persistence, tag governance, and job explainability."""

from __future__ import annotations

import json

import pytest
from cef_coordinator.app import create_app
from cef_coordinator.config import Settings
from cef_protocol.describe import describe_job, describe_task
from cef_protocol.tags import categorize, normalize_tags
from cef_worker.client import WorkerAPI
from fastapi.testclient import TestClient


@pytest.fixture()
def wire(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, token="t", max_attempts=2))
    http = TestClient(app)
    http.headers["Authorization"] = "Bearer t"
    api = WorkerAPI("http://testserver", "t")
    api.close()
    api._client = http
    yield api, http, tmp_path


def submit(http, name="a.jpg", size=2048, submitter="alice"):
    spec = {"workload": "thumbnail", "params": {"size": 512}, "name": "demo", "submitter": submitter}
    return http.post(
        "/api/v1/jobs",
        data={"spec": json.dumps(spec)},
        files={"files": (name, b"x" * size, "image/jpeg")},
    )


# ------------------------------------------------------------------- describe
def test_describe_job_and_task():
    assert describe_job("thumbnail", {"size": 512, "fmt": "webp", "quality": 80}) == "缩略图，长边 512px，WEBP，质量 80"
    assert "目标检测" in describe_job("infer", {"model": "yolo11n"})
    assert "第 1-24 帧" in describe_job("blender", {"frames": "1-24"})
    meta = {"src_size": [900, 600], "out_size": [512, 341], "format": "webp", "out_bytes": 18432}
    assert describe_task("thumbnail", {}, meta) == "900×600 → 512×341，WEBP 18KB"
    assert describe_task("infer", {}, {"detections": 3, "provider": "CPUExecutionProvider"}) == "3 个目标，CPUExecutionProvider"
    assert describe_task("blender", {"frame": 42}, {}) == "第 42 帧"
    assert describe_task("thumbnail", {}, {}) is None


# ----------------------------------------------------------------------- tags
def test_normalize_tags_edges():
    kept, rejected = normalize_tags(["GPU", " gpu ", "gpu_x", "a b", "", "x" * 40, "hW:RTX5060"])
    # "gpu " de-duplicates onto "gpu"; the 40-char tag is truncated to 24, not rejected
    assert kept == ["a-b", "gpu", "gpu-x", "hw:rtx5060", "x" * 24]
    assert rejected == []  # blanks are dropped early, not reported
    assert normalize_tags([f"t{i}" for i in range(10)])[1] == ["t8", "t9"]  # cap of 8
    assert normalize_tags(["Role:GPU"])[0] == ["role:gpu"]
    assert categorize("bench-jochi") == "bench"
    assert categorize("hw:rtx5060") == "hw"
    assert categorize("cuda") == "free"


def test_register_normalizes_and_patch_replaces(wire):
    api, http, _ = wire
    worker = api.register({"name": "pc", "tags": ["GPU", "Lab PC"], "workloads": [{"kind": "thumbnail", "available": True}]})["worker_id"]
    tags = {w["worker_id"]: w["tags"] for w in http.get("/api/v1/workers").json()["workers"]}
    assert tags[worker] == ["gpu", "lab-pc"]

    resp = http.patch(f"/api/v1/workers/{worker}", json={"tags": ["role:gpu", "  ", "HW:RTX5060"]})
    assert resp.status_code == 200
    assert resp.json()["tags"] == ["hw:rtx5060", "role:gpu"]

    aggregated = http.get("/api/v1/tags").json()["tags"]
    assert {t["tag"] for t in aggregated} == {"hw:rtx5060", "role:gpu"}


# ----------------------------------------------------------------------- chat
def test_chat_roundtrip_and_incremental_fetch(wire):
    _, http, _ = wire
    first = http.get("/api/v1/chat/messages").json()
    assert first["messages"] == [] and first["latest"] == 0

    from cef_coordinator.api import _save_chat

    app_settings = http.app.state.settings
    _save_chat(app_settings.db_path, "pool", "user", "alice", "把图压到 512")
    _save_chat(app_settings.db_path, "pool", "assistant", "算力助手", "已准备好，请确认")

    body = http.get("/api/v1/chat/messages").json()
    assert [m["role"] for m in body["messages"]] == ["user", "assistant"]
    assert body["messages"][0]["author"] == "alice"
    latest = body["latest"]

    _save_chat(app_settings.db_path, "pool", "user", "bob", "再来一轮")
    delta = http.get(f"/api/v1/chat/messages?after={latest}").json()
    assert [m["content"] for m in delta["messages"]] == ["再来一轮"]

    removed = http.delete("/api/v1/chat/messages?older_than_days=0").json()
    assert removed["removed"] == 0  # newest 200 are always kept


def test_agent_chat_requires_configuration(wire):
    _, http, _ = wire
    resp = http.post("/api/v1/agent/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code in (502, 503)


# ------------------------------------------------------------------ explain
def test_job_detail_explains_what_it_did(wire):
    api, http, tmp_path = wire
    job_id = submit(http).json()["job_id"]
    worker = api.register({"name": "pc", "workloads": [{"kind": "thumbnail", "available": True}]})["worker_id"]
    lease = api.poll(worker, 1, ["thumbnail"], [])["leases"][0]

    out = tmp_path / "out.webp"
    out.write_bytes(b"webp-bytes")
    meta = {
        "attempt": 1,
        "duration_s": 0.031,
        "output": {"kind": "thumbnail", "src_size": [900, 600], "out_size": [512, 341],
                   "format": "webp", "out_bytes": 10},
    }
    ok, _ = api.complete(worker, lease["task_id"], meta, [out])
    assert ok

    job = http.get(f"/api/v1/jobs/{job_id}").json()
    assert job["submitter"] == "alice"
    assert job["params_text"].startswith("缩略图")
    assert job["input_summary"] == "1 个文件，共 2KB"
    task = job["tasks"][0]
    assert task["inputs"][0]["name"] == "a.jpg"
    assert task["outputs"][0]["name"] == "out.webp"
    assert task["outputs"][0]["url"].startswith("/api/v1/blobs/")
    assert task["summary"] == "900×600 → 512×341，WEBP 10B"

    blob = http.get(task["outputs"][0]["url"])
    assert blob.status_code == 200
    assert blob.headers["content-type"].startswith("image/webp")
    input_blob = http.get(task["inputs"][0]["url"])
    assert input_blob.headers["content-type"].startswith("image/jpeg")


def test_restart_does_not_wipe_operator_tags(wire):
    """A worker restart re-registers with no --tags; that must not revert a PATCH."""
    api, http, _ = wire
    worker = api.register({"name": "pc", "tags": [], "workloads": [{"kind": "thumbnail", "available": True}]})["worker_id"]
    http.patch(f"/api/v1/workers/{worker}", json={"tags": ["bench-pc", "engine:cpu"]})
    api.register({"worker_id": worker, "name": "pc", "tags": [], "workloads": [{"kind": "thumbnail", "available": True}]})
    tags = {w["worker_id"]: w["tags"] for w in http.get("/api/v1/workers").json()["workers"]}
    assert tags[worker] == ["bench-pc", "engine:cpu"]

    # an explicit --tags on the command line is still an intent we honour
    api.register({"worker_id": worker, "name": "pc", "tags": ["ENGINE:CUDA"], "workloads": [{"kind": "thumbnail", "available": True}]})
    tags = {w["worker_id"]: w["tags"] for w in http.get("/api/v1/workers").json()["workers"]}
    assert tags[worker] == ["engine:cuda"]
