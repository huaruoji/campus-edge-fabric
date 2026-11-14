"""Read-only online preflight; asks for a hidden Token and never submits a job."""
import argparse
import getpass
import json
import os
from datetime import datetime
from pathlib import Path

from cef_protocol import CefClient

repo = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--job", help="also inspect an existing job; never submits or cancels work")
args = parser.parse_args()
token = os.environ.get("CEF_TOKEN") or getpass.getpass("CEF Token (hidden; not saved): ")
if not token.strip():
    raise SystemExit("A Token is required")
with CefClient(os.environ.get("CEF_SERVER") or "https://cef.huaruoji.com", token.strip(), timeout=30) as client:
    # Keep newly deployed diagnostic fields even when the local SDK is older.
    workers = client.request("GET", "/api/v1/workers").json()["workers"]
    data = {
        "checked_at_local": datetime.now().astimezone().isoformat(),
        "health": client.health().model_dump(mode="json"),
        "workers": workers,
        "queued_jobs": client.list_jobs(limit=20, state="queued").model_dump(mode="json"),
        "running_jobs": client.list_jobs(limit=20, state="running").model_dump(mode="json"),
        "workloads": client.workloads().model_dump(mode="json"),
        "scope": "Read-only capability snapshot; no job submitted, no remote model hash verified.",
    }
    if args.job:
        data["inspected_job"] = client.request("GET", f"/api/v1/jobs/{args.job}").json()
dest = repo / "data/d-acceptance/infer-preflight"
dest.mkdir(parents=True, exist_ok=True)
path = dest / (datetime.now().strftime("%Y%m%d-%H%M%S") + ".json")
path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
for worker in workers:
    infer = next((w for w in worker["workloads"] if w["kind"] == "infer"), {})
    print(json.dumps({"name": worker["name"], "worker_id": worker["worker_id"], "state": worker["state"],
                      "tags": worker["tags"], "slots": worker["slots"], "running": worker["running"],
                      "last_seen_s_ago": worker["last_seen_s_ago"], "cpu_pct": worker["cpu_pct"], "mem_pct": worker["mem_pct"],
                      "gpu_pct": worker["gpu_pct"], "disk_free_gb": worker.get("disk_free_gb"), "infer": infer}, ensure_ascii=False))
if args.job:
    job = data["inspected_job"]
    print(json.dumps({k: job.get(k) for k in ("job_id", "state", "required_tag", "required_model_sha", "blocked_reason",
                                            "total", "succeeded", "failed", "queued")},
                     ensure_ascii=False))
print("Queued jobs:", len(data["queued_jobs"]["jobs"]), "Running jobs:", len(data["running_jobs"]["jobs"]))
print("Saved:", path)
