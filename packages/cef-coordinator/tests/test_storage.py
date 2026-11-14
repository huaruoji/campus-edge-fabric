"""Disk guards: upload ceilings, blob quota, and GC that never eats live work."""

from __future__ import annotations

import json

from cef_coordinator import db, scheduler
from cef_coordinator.app import create_app
from cef_coordinator.config import Settings
from fastapi.testclient import TestClient


def make_client(tmp_path, **overrides):
    settings = Settings(data_dir=tmp_path, token="t", **overrides)
    app = create_app(settings)
    http = TestClient(app)
    http.headers["Authorization"] = "Bearer t"
    return http, settings


def upload(http, name="a.jpg", size=1024):
    return http.post(
        "/api/v1/jobs",
        data={"spec": json.dumps({"workload": "thumbnail"})},
        files={"files": (name, b"x" * size, "image/jpeg")},
    )


def test_oversized_file_is_rejected(tmp_path):
    http, _ = make_client(tmp_path, max_upload_mb=0.001)  # 1 KB
    resp = upload(http, size=4096)
    assert resp.status_code == 413
    assert resp.json()["error"]["code"] == "file_too_large"


def test_quota_exhaustion_is_reported_as_storage_full(tmp_path):
    http, settings = make_client(tmp_path, max_upload_mb=10, blob_quota_gb=0.000002)  # 2 KB
    assert upload(http, size=1500).status_code in (201, 507)
    resp = upload(http, name="b.jpg", size=1500)
    assert resp.status_code == 507
    assert resp.json()["error"]["code"] == "storage_full"


def test_storage_endpoint_reports_usage(tmp_path):
    http, _ = make_client(tmp_path)
    upload(http, size=2048)
    body = http.get("/api/v1/storage").json()
    assert body["blob_count"] == 1
    assert body["blob_bytes"] == 2048
    assert body["quota_gb"] > 0


def test_gc_keeps_blobs_referenced_by_tasks(tmp_path):
    http, settings = make_client(tmp_path)
    job_id = upload(http, size=4096).json()["job_id"]
    conn = db.reading(settings.db_path)
    sha = db.one(conn, "SELECT sha256 FROM blobs")["sha256"]
    conn.close()
    path = settings.blob_dir / sha[:2] / sha[2:4] / sha
    assert path.exists()

    deleted, _ = scheduler.gc_blobs(settings.db_path, settings, settings.blob_dir)
    assert deleted == 0 and path.exists(), "a blob referenced by a live task must never be collected"

    # once the task is gone the same blob becomes collectable
    with db.transaction(settings.db_path) as conn:
        conn.execute("DELETE FROM tasks WHERE job_id=?", (job_id,))
        conn.execute("UPDATE blobs SET created_at=?", (0.0,))
    deleted, freed = scheduler.gc_blobs(settings.db_path, settings, settings.blob_dir)
    assert deleted == 1 and freed == 4096
    assert not path.exists()


def test_expired_lease_then_gc_does_not_lose_queued_inputs(tmp_path):
    """GC must not delete inputs of tasks that are queued but not yet leased."""
    http, settings = make_client(tmp_path)
    upload(http, size=2048)
    with db.transaction(settings.db_path) as conn:
        conn.execute("UPDATE blobs SET created_at=?", (0.0,))
    deleted, _ = scheduler.gc_blobs(settings.db_path, settings, settings.blob_dir)
    assert deleted == 0
