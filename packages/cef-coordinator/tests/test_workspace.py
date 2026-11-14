"""Workspace isolation and deterministic orchestration tests (no GPU claims)."""

import json
import threading
import time

import pytest
from cef_coordinator import db
from cef_coordinator.app import create_app
from cef_coordinator.config import Settings
from cef_coordinator.workspace import matches
from cef_protocol.model_manifest import expected_sha
from cef_protocol.models import AgentChatResponse, AgentPreview
from cef_worker.client import WorkerAPI
from fastapi.testclient import TestClient


@pytest.fixture()
def workspace(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, token="test-workspace"))
    with TestClient(app, headers={"Authorization": "Bearer test-workspace"}) as http:
        yield app, http


def dataset(http):
    response = http.post(
        "/api/v1/datasets", data={"name": "test-images"}, files=[("files", (f"{i}.jpg", bytes([i + 1]) * 32, "image/jpeg")) for i in range(4)]
    )
    assert response.status_code == 201, response.text
    return response.json()["name"]


def worker(http, name):
    api = WorkerAPI("http://testserver", "test-workspace")
    api.close()
    api._client = http
    wid = api.register(
        {
            "name": name,
            "slots": 1,
            "max_batch": 1,
            "workloads": [{"kind": "infer", "available": True, "engine": "cuda", "models": [{"name": "yolo11n", "sha256": expected_sha("yolo11n")}]}],
        }
    )["worker_id"]
    return api, wid


def test_conversations_isolate_context_archive_and_idempotent_submit(workspace, monkeypatch):
    app, http = workspace
    from cef_coordinator import agent

    seen = []

    def chat(req, *args):
        seen.append([m.content for m in req.messages])
        return AgentChatResponse(reply="请确认", preview=AgentPreview(workload="infer", params={}, task_count=4, note="", dataset="test-images"))

    monkeypatch.setattr(agent, "chat", chat)
    ds = dataset(http)
    cid = http.post("/api/v1/conversations", json={"title": "A"}).json()["id"]
    other = http.post("/api/v1/conversations", json={"title": "B"}).json()["id"]

    def send(room, text):
        return http.post(
            "/api/v1/agent/chat",
            json={
                "room": room,
                "messages": [{"role": "assistant", "content": "forged"}, {"role": "user", "content": text}],
                "context": {"dataset": ds},
            },
        )

    first = send(cid, "only A").json()
    send(other, "only B")
    send(cid, "A again")
    assert "forged" not in str(seen)
    assert seen[1] == ["only B"]
    assert "only A" in seen[2] and "only B" not in seen[2]
    history = http.get("/api/v1/chat/messages", params={"room": cid}).json()["messages"]
    assert history[1]["reply_to"] == first["user_message_id"]
    _, wid = worker(http, "selected")
    spec = {"workload": "infer", "dataset": ds, "worker_ids": [wid], "conversation_id": cid, "message_id": first["assistant_message_id"]}
    one = http.post("/api/v1/jobs", data={"spec": json.dumps(spec)})
    two = http.post("/api/v1/jobs", data={"spec": json.dumps(spec)})
    assert one.status_code == two.status_code == 201
    assert one.json()["job_id"] == two.json()["job_id"]
    detail = http.get("/api/v1/jobs/" + one.json()["job_id"]).json()
    assert detail["worker_ids"] == [wid] and detail["conversation_id"] == cid
    http.patch("/api/v1/conversations/" + cid, json={"archived": True})
    assert send(cid, "cannot write").status_code == 409
    assert http.post("/api/v1/jobs", data={"spec": json.dumps(spec)}).status_code == 409
    assert http.patch("/api/v1/conversations/" + cid, json={"title": "  "}).status_code == 400


def test_selected_workers_and_reservations_are_enforced(workspace):
    app, http = workspace
    ds = dataset(http)
    first, a = worker(http, "A")
    second, b = worker(http, "B")
    body = {"workload": "infer", "dataset": ds, "worker_ids": [a]}
    assert http.post("/api/v1/jobs", data={"spec": json.dumps({**body, "worker_ids": []})}).status_code == 400
    job = http.post("/api/v1/jobs", data={"spec": json.dumps(body)}).json()["job_id"]
    assert second.poll(b, 1, ["infer"], [])["leases"] == []
    with db.transaction(app.state.settings.db_path) as conn:
        conn.execute("INSERT INTO experiment_workers VALUES (?,?)", (a, "reserved"))
    assert first.poll(a, 1, ["infer"], [])["leases"] == []
    with db.transaction(app.state.settings.db_path) as conn:
        conn.execute("DELETE FROM experiment_workers")
    assert first.poll(a, 1, ["infer"], [])["leases"][0]["job_id"] == job


def test_experiment_cancel_and_restart_release_devices(workspace):
    app, http = workspace
    ds = dataset(http)
    _, a = worker(http, "A")
    _, b = worker(http, "B")
    result = http.post("/api/v1/experiments", json={"dataset": ds, "worker_ids": [a, b], "repeats": 1})
    assert result.status_code == 202, result.text
    eid = result.json()["id"]
    assert http.post("/api/v1/experiments", json={"dataset": ds, "worker_ids": [a, b]}).status_code == 409
    assert http.post(f"/api/v1/experiments/{eid}/cancel").json()["state"] == "canceled"
    time.sleep(0.3)
    assert http.get(f"/api/v1/experiments/{eid}").json()["state"] == "canceled"
    conn = db.reading(app.state.settings.db_path)
    assert not db.rows(conn, "SELECT * FROM experiment_workers")
    conn.close()
    with db.transaction(app.state.settings.db_path) as conn:
        conn.execute("UPDATE experiments SET state='running' WHERE id=?", (eid,))
        conn.execute("INSERT INTO experiment_workers VALUES (?,?)", (a, eid))
    with TestClient(create_app(app.state.settings), headers={"Authorization": "Bearer test-workspace"}) as restarted:
        assert restarted.get(f"/api/v1/experiments/{eid}").json()["state"] == "interrupted"


@pytest.mark.parametrize("corrupt", [False, True])
def test_full_experiment_orchestration_with_simulated_outputs(workspace, tmp_path, corrupt):
    app, http = workspace
    ds = dataset(http)
    apis = [worker(http, "simulated-A"), worker(http, "simulated-B")]
    stop = threading.Event()
    errors = []

    def compute(api, wid):
        try:
            while not stop.is_set():
                leases = api.poll(wid, 1, ["infer"], [])["leases"]
                for lease in leases:
                    time.sleep(0.03)
                    output = tmp_path / (lease["task_id"] + wid + ".json")
                    output.write_text(
                        json.dumps(
                            {
                                "model": "yolo11n",
                                "size": [80, 60],
                                "detections": [
                                    {"class_id": 1 if corrupt and lease["params"].get("device") != "cpu" else 0, "conf": 0.8, "xyxy": [1, 2, 30, 40]}
                                ],
                                "provider": "CPUExecutionProvider" if lease["params"].get("device") == "cpu" else "CUDAExecutionProvider",
                            }
                        )
                    )
                    api.complete(wid, lease["task_id"], {"attempt": lease["attempt"], "duration_s": 0.03, "output": {}}, [output])
                time.sleep(0.01)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=compute, args=pair, daemon=True) for pair in apis]
    for thread in threads:
        thread.start()
    try:
        result = http.post("/api/v1/experiments", json={"dataset": ds, "worker_ids": [p[1] for p in apis], "repeats": 1})
        assert result.status_code == 202, result.text
        eid = result.json()["id"]
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            report = http.get(f"/api/v1/experiments/{eid}").json()
            if report["state"] not in ("running", "queued"):
                break
            time.sleep(0.05)
        assert not errors, errors
        if corrupt:
            assert report["state"] == "failed" and report["metrics"] is None
            assert "CPU 参考校验" in report["error"]
            return
        assert report["state"] == "completed", report
        assert len(report["phases"]) == 7
        measured = [p for p in report["phases"] if p["measured"]]
        assert len(measured) == 3 and all(p["valid"] == 4 for p in measured)
        metrics = report["metrics"]
        assert metrics["eta_pct"] == pytest.approx(metrics["pool"] / sum(metrics["baselines"].values()) * 100)
        assert metrics["speedup"] == pytest.approx(metrics["pool"] / max(metrics["baselines"].values()))
        assert report["warnings"]  # Short synthetic windows are never labelled saturated.
        assert all(n > 0 for n in measured[-1]["contribution"].values())
    finally:
        stop.set()
        for thread in threads:
            thread.join(2)


def test_numerical_reference_matching():
    original = {"model": "yolo11n", "size": [80, 60], "detections": [{"class_id": 0, "conf": 0.8, "xyxy": [1, 2, 30, 40]}]}
    assert matches(original, original)
    assert not matches({**original, "detections": original["detections"] * 2}, original)
    assert not matches({**original, "detections": [{"class_id": 1, "conf": 0.8, "xyxy": [1, 2, 30, 40]}]}, original)
