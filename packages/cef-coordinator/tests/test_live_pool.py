"""Real loopback HTTP, real CLI worker processes and real Pillow output.

Two local processes are NOT evidence of two independent physical devices.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
import zipfile

import httpx
import pytest
import uvicorn
from cef_coordinator.app import create_app
from cef_coordinator.config import Settings
from cef_worker.agent import Agent
from cef_worker.config import WorkerConfig
from cef_worker.workloads.thumbnail import ThumbnailWorkload
from PIL import Image


def until(check, timeout=25):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(0.1)
    raise AssertionError("condition did not become true before deadline")


@pytest.fixture()
def pool(tmp_path):
    token = secrets.token_urlsafe(24)
    settings = Settings(data_dir=tmp_path / "coordinator", token=token, lease_ttl_s=2,
                        heartbeat_interval_s=1, reaper_interval_s=0.1)
    app = create_app(settings)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    url = f"http://127.0.0.1:{sock.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    processes = []
    handles = []
    until(lambda: server.started)
    with httpx.Client(base_url=url, headers={"Authorization": f"Bearer {token}"}, trust_env=False, timeout=10) as http:
        def launch(name, tag=""):
            log = (tmp_path / f"{name}.log").open("w", encoding="utf-8")
            handles.append(log)
            env = {**os.environ, "CEF_SERVER": url, "CEF_TOKEN": token, "PYTHONIOENCODING": "utf-8",
                   "CEF_MODEL_DIR": str(tmp_path / "models")}
            proc = subprocess.Popen(
                [sys.executable, "-m", "cef_worker", "run", "--name", name, "--workloads", "thumbnail",
                 "--data", str(tmp_path / name), "--tags", tag, "--slots", "2"],
                env=env, stdout=log, stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            processes.append(proc)
            return proc
        try:
            yield http, launch, url, token
        finally:
            for p in processes:
                if p.poll() is None:
                    p.terminate()
                p.wait(timeout=10)
            for handle in handles:
                handle.close()
            server.should_exit = True
            thread.join(timeout=10)
            sock.close()


def image_bytes():
    out = io.BytesIO()
    Image.new("RGB", (320, 160), (30, 80, 160)).save(out, format="JPEG")
    return out.getvalue()


def submit(http, files, tag=None):
    response = http.post("/api/v1/jobs", data={"spec": json.dumps({
        "workload": "thumbnail", "params": {"size": 64, "fmt": "png"}, "required_tag": tag,
    })}, files=[("files", (name, content, "image/jpeg")) for name, content in files])
    response.raise_for_status()
    return response.json()["job_id"]


def terminal(http, job):
    def check():
        result = http.get(f"/api/v1/jobs/{job}").json()
        return result if result["state"] in {"succeeded", "partial", "failed", "canceled"} else None
    return until(check)


def test_two_cli_workers_upload_compute_download_and_failure(pool):
    http, launch, _, _ = pool
    launch("worker-a")
    launch("worker-b")
    until(lambda: len(http.get("/api/v1/workers").json()["workers"]) == 2)
    assert http.get("/api/v1/workloads").status_code == 200
    job = submit(http, [(f"image-{i}.jpg", image_bytes()) for i in range(16)])
    result = terminal(http, job)
    assert result["succeeded"] == 16
    assert len(result["per_worker"]) == 2
    response = http.get(f"/api/v1/jobs/{job}/result")
    response.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        output_files = [n for n in archive.namelist() if n.endswith(".png")]
        assert len(output_files) == 16
        hashes = set()
        for name in output_files:
            data = archive.read(name)
            hashes.add(hashlib.sha256(data).hexdigest())
            with Image.open(io.BytesIO(data)) as im:
                im.load()
                assert im.size == (64, 32)
        assert all(e["output_sha256"] in hashes for e in manifest["entries"])
    bad = submit(http, [("good.jpg", image_bytes()), ("broken.jpg", b"not an image")])
    result = terminal(http, bad)
    assert (result["state"], result["succeeded"], result["failed"]) == ("partial", 1, 1)
    assert http.get("/api/v1/stats").status_code == 200


def test_lost_lease_recovery_and_late_result(pool):
    http, launch, _, _ = pool
    worker = http.post("/api/v1/workers/register", json={"name": "lost", "tags": ["recover"],
                      "workloads": [{"kind": "thumbnail"}]}).json()["worker_id"]
    job = submit(http, [("recover.jpg", image_bytes())], "recover")
    lease, = http.post(f"/api/v1/workers/{worker}/poll", json={"capacity": 1, "workloads": ["thumbnail"]}).json()["leases"]
    # Simulate an absent worker: it holds the lease but sends neither heartbeat nor result.
    launch("replacement", "recover")
    result = terminal(http, job)
    assert result["succeeded"] == 1 and result["tasks"][0]["attempt"] == 2
    stale = http.post(f"/api/v1/workers/{worker}/tasks/{lease['task_id']}/complete",
                      data={"meta": json.dumps({"attempt": 1, "duration_s": 1})})
    assert stale.status_code == 409
    assert http.get(f"/api/v1/jobs/{job}").json()["succeeded"] == 1


def test_cli_spec_submit_watch_and_download(pool, tmp_path):
    http, launch, url, token = pool
    launch("cli-worker")
    until(lambda: http.get("/api/v1/workers").json()["workers"])
    image = tmp_path / "sample.jpg"
    image.write_bytes(image_bytes())
    spec = tmp_path / "job.json"
    spec.write_text(json.dumps({"workload": "thumbnail", "params": {"size": 64},
                                "files": [str(image)]}), encoding="utf-8")

    def cli(*args):
        proc = subprocess.run(
            [sys.executable, "-m", "cef_client", *args],
            env={**os.environ, "CEF_SERVER": url, "CEF_TOKEN": token, "PYTHONIOENCODING": "utf-8"},
            capture_output=True, text=True, encoding="utf-8", timeout=20,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        assert proc.returncode == 0, proc.stderr
        return proc.stdout

    job = json.loads(cli("--json", "job", "submit", "--spec", str(spec)))["job_id"]
    assert "succeeded" in cli("job", "watch", job, "--interval", "0.1")
    result = tmp_path / "cli-result.zip"
    cli("job", "result", job, "-o", str(result))
    with zipfile.ZipFile(result) as archive:
        assert json.loads(archive.read("manifest.json"))["succeeded"] == 1


@pytest.mark.parametrize("stop_during_task", [True, False])
def test_stop_keeps_heartbeat_until_slow_real_task_finishes(pool, tmp_path, stop_during_task):
    http, _, url, token = pool
    started = threading.Event()

    class SlowThumbnail(ThumbnailWorkload):
        def run(self, ctx):
            started.set()
            time.sleep(4)  # Longer than lease TTL: exercise shutdown renewal, then real image compute.
            return super().run(ctx)

    agent = Agent(WorkerConfig(server=url, token=token, name="slow", workloads=["thumbnail"],
                               data_dir=tmp_path / "slow", model_dir=tmp_path / "models"))
    agent.adapters = {"thumbnail": SlowThumbnail()}
    thread = threading.Thread(target=agent.run, daemon=True)
    thread.start()
    try:
        until(lambda: agent.worker_id)
        job = submit(http, [("slow.jpg", image_bytes())])
        assert started.wait(10)
        assert agent.cfg.heartbeat_interval_s == 1
        if not stop_during_task:
            assert terminal(http, job)["state"] == "succeeded"
        agent._stop.set()
        thread.join(timeout=12)
        assert not thread.is_alive()
        result = http.get(f"/api/v1/jobs/{job}").json()
        assert result["state"] == "succeeded", result
        assert result["tasks"][0]["attempt"] == 1
    finally:
        agent._stop.set()
        thread.join(timeout=10)
