"""Read retries and evidence-preserving recovery; never retry a job submission."""
import time
from pathlib import Path

import httpx
from cef_protocol import CefClient
from common import read_json, verify_archive, write_json


class ReadRetryClient(CefClient):
    def __init__(self, *args, retry_log=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.retry_log = retry_log
        self.retries = []

    def request(self, method, path, **kwargs):
        for attempt in range(4):
            try:
                return super().request(method, path, **kwargs)
            except httpx.TransportError as error:
                if method.upper() != "GET" or attempt == 3:
                    raise
                self.retries.append({"path": path, "error_type": type(error).__name__, "attempt": attempt + 1, "at": time.time()})
                if self.retry_log:
                    write_json(self.retry_log, self.retries)
                print("Read interrupted; retrying the same GET:", path, type(error).__name__, flush=True)
                time.sleep(2 ** attempt)

    def download_result(self, job_id, dest):
        response = self.request("GET", f"/api/v1/jobs/{job_id}/result")
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(response.content)
        return dest


def remaining_plan(plan, rows):
    prefix = [{k: r[k] for k in ("repeat", "size", "condition")} for r in rows]
    if prefix != plan[:len(rows)] or len(rows) >= len(plan):
        raise ValueError("Only an incomplete, exact measured plan prefix can resume")
    return plan[len(rows):]


def archive_round(run, folder, segment):
    """Rename within the same run, preserving interrupted evidence without deletion."""
    root, source, destination = run.resolve(), folder.resolve(), (segment / "excluded" / folder.name).resolve()
    if source.parent != root or not destination.is_relative_to(root) or destination.exists():
        raise ValueError("Unsafe or colliding evidence archive path")
    destination.parent.mkdir(parents=True, exist_ok=True)
    source.rename(destination)


def recover_inflight(client, run, segment, selected_ids, providers, groups, reference):
    recovered = []
    for created_path in run.glob("*/created.json"):
        folder = created_path.parent
        if (folder / "round.json").exists():
            continue
        job_id = read_json(created_path)["job_id"]
        limit = time.monotonic() + 90
        while True:
            job = client.get_job(job_id)
            write_json(segment / (job_id + "-status.json"), job.model_dump(mode="json"))
            if job.state not in ("queued", "running"):
                break
            if time.monotonic() >= limit:
                raise ValueError(f"Interrupted job {job_id} is still active; no new work submitted")
            time.sleep(5)
        if job.state != "succeeded":
            raise ValueError(f"Interrupted job {job_id} ended as {job.state}; investigate before continuation")
        if "-warm-" in folder.name:
            files = groups["warmup"]
        else:
            size = int(folder.name.split("-")[1][1:])
            files = groups["benchmark"][:size]
        allowed = set(selected_ids) if folder.name.endswith("pool") else {selected_ids[int(folder.name[-1]) - 1]}
        archive = client.download_result(job_id, folder / "recovered-result.zip")
        checked = verify_archive(archive, files, job_id, reference, allowed, providers)
        write_json(folder / "recovered-verification.json", checked)
        recovered.append({"job_id": job_id, "round": folder.name, "state": str(job.state),
                          "verified_images": checked["verified_images"], "total": len(files),
                          "included_in_performance": False, "reason": "client timing interrupted; original evidence retained"})
    write_json(segment / "recovered-jobs.json", recovered)
    return recovered
