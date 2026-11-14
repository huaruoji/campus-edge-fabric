"""Submit the fixed 16-image D smoke batch through the existing cef CLI.

Default: validate local inputs only. --run contacts the pool and submits once.
Uses PNG for lossless comparison with local resized source images.
"""
from __future__ import annotations

import argparse
import collections
import getpass
import hashlib
import io
import json
import os
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def verify_result(archive, source_files, job_id):
    from PIL import Image, ImageChops, ImageOps

    checks = []
    with zipfile.ZipFile(archive) as z:
        names = z.namelist()
        if len(names) != len(set(names)) or z.testzip() is not None:
            raise ValueError("ZIP contains duplicate members or a CRC failure")
        manifest = json.loads(z.read("manifest.json"))
        if manifest["job_id"] != job_id or manifest["workload"] != "thumbnail":
            raise ValueError("Unexpected job or workload in manifest")
        params = manifest["params"]
        if params["size"] != 256 or params["fmt"] != "png":
            raise ValueError("Unexpected thumbnail parameters")
        total = len(source_files)
        if (manifest["state"], manifest["total"], manifest["succeeded"], manifest["failed"]) != ("succeeded", total, total, 0):
            raise ValueError("Job did not successfully finish every expected task")
        entries = manifest["entries"]
        if len(entries) != total or len({e["task_id"] for e in entries}) != total:
            raise ValueError("Unexpected or duplicate task records")
        if {e["idx"] for e in entries} != set(range(total)):
            raise ValueError("Task indices are missing or repeated")
        expected_outputs = set()
        for entry in sorted(entries, key=lambda e: e["idx"]):
            source = source_files[entry["idx"]]
            if entry["name"] != source.name or entry.get("error") or not entry.get("worker_id"):
                raise ValueError("Source name, task error or Worker ID mismatch")
            if len(entry["output_files"]) != 1:
                raise ValueError("Expected exactly one thumbnail per task")
            member = f"outputs/{entry['idx']:06d}_{entry['output_files'][0]}"
            expected_outputs.add(member)
            data = z.read(member)
            hash_ok = hashlib.sha256(data).hexdigest() == entry["output_sha256"]
            with Image.open(source) as original:
                expected = ImageOps.exif_transpose(original)
                if expected.mode not in ("RGB", "L"):
                    expected = expected.convert("RGB")
                expected.thumbnail((256, 256), Image.Resampling.LANCZOS)
                expected_rgb = expected.convert("RGB")
            with Image.open(io.BytesIO(data)) as actual:
                actual.load()
                size_ok = actual.size == expected_rgb.size
                pixel_ok = size_ok and ImageChops.difference(actual.convert("RGB"), expected_rgb).getbbox() is None
                check = {
                    "task_id": entry["task_id"], "input": source.name,
                    "worker_id": entry["worker_id"], "worker_name": entry["worker_name"],
                    "attempt": entry["attempt"], "format": actual.format, "size": list(actual.size),
                    "hash_matches_manifest": hash_ok, "size_matches_reference": size_ok,
                    "pixels_match_local_reference": pixel_ok,
                    "passed": hash_ok and actual.format == "PNG" and size_ok and pixel_ok,
                }
                checks.append(check)
        if {n for n in names if n.startswith("outputs/") and not n.endswith("/")} != expected_outputs:
            raise ValueError("ZIP output inventory differs from manifest")
    workers = collections.Counter(c["worker_id"] for c in checks if c["passed"])
    worker_names = {c["worker_id"]: c["worker_name"] for c in checks}
    return {
        "job_id": job_id, "images_passed": sum(c["passed"] for c in checks), "images_total": total,
        "all_image_checks_passed": all(c["passed"] for c in checks),
        "two_worker_ids_contributed": len(workers) >= 2,
        "per_worker": [{"worker_id": wid, "name": worker_names[wid], "verified_outputs": count} for wid, count in workers.items()],
        "checks": checks,
        "limits": [
            "Different Worker IDs still require B to confirm separate physical computers.",
            "Exact PNG reference comparison assumes compatible Pillow resizing behavior; investigate version differences on mismatch.",
            "This is a correctness smoke test, not a controlled throughput benchmark.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="contact the pool and submit one batch")
    parser.add_argument("--server", default=os.environ.get("CEF_SERVER") or "https://cef.huaruoji.com")
    parser.add_argument("--dataset-root", type=Path, default=REPO / "data/test-datasets")
    parser.add_argument("--output-root", type=Path, default=REPO / "data/d-acceptance")
    args = parser.parse_args()
    metadata = REPO / "benchmarks/datasets"
    split = json.loads((metadata / "splits.json").read_text(encoding="utf-8"))["groups"]["thumbnail"]["smoke"]
    manifest = {r["path"]: r for r in json.loads((metadata / "image-manifest.json").read_text(encoding="utf-8"))}
    files = [(args.dataset_root / name).resolve() for name in split["source_files"]]
    from PIL import Image

    for relative, path in zip(split["source_files"], files, strict=True):
        data = path.read_bytes()
        if len(data) > 500_000 or hashlib.sha256(data).hexdigest() != manifest[relative]["sha256"]:
            raise ValueError(f"Input exceeds budget or differs from fixed dataset: {path.name}")
        with Image.open(path) as image:
            image.verify()
    if len(files) != 16 or len({p.name for p in files}) != 16:
        raise ValueError("Expected 16 distinct fixed smoke images")
    print(f"Local checks passed: {len(files)} original BSDS300 images, each <= 500 KB.", flush=True)
    if not args.run:
        print("No network request made. Add --run to submit this batch.")
        return 0

    token = os.environ.get("CEF_TOKEN") or getpass.getpass("CEF Token (hidden; not saved): ")
    if not token.strip():
        raise ValueError("CEF_TOKEN is required")
    cli_executable = REPO / ".venv/Scripts/cef.exe" if os.name == "nt" else REPO / ".venv/bin/cef"
    if not cli_executable.is_file():
        raise FileNotFoundError("Install the project CLI first: uv sync --all-packages --extra thumbnail")
    env = {**os.environ, "CEF_SERVER": args.server, "CEF_TOKEN": token.strip(), "PYTHONUTF8": "1"}
    out = args.output_root.resolve() / ("cli-smoke-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    out.mkdir(parents=True)
    print(f"Evidence directory: {out}", flush=True)

    def cli(name, *arguments, timeout=60):
        try:
            result = subprocess.run([str(cli_executable), *arguments], cwd=REPO, env=env,
                                    capture_output=True, text=True, encoding="utf-8", timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            (out / (name + ".error.txt")).write_text("CLI timed out. Check the job list before resubmitting.\n", encoding="utf-8")
            raise TimeoutError("CLI timed out; the server may have accepted the request. Check jobs before retrying.") from exc
        (out / (name + ".stdout.txt")).write_text(result.stdout, encoding="utf-8")
        (out / (name + ".stderr.txt")).write_text(result.stderr, encoding="utf-8")
        if result.returncode:
            raise RuntimeError(f"cef {name} failed (exit {result.returncode}); see evidence logs")
        return result.stdout

    workers = json.loads(cli("workers-before", "--json", "workers"))
    available = [w for w in workers if w["state"] == "online" and any(
        cap["kind"] == "thumbnail" and cap["available"] for cap in w["workloads"])]
    if len(available) < 2:
        raise RuntimeError("Fewer than two online thumbnail Workers; no task submitted")
    print("Online thumbnail Workers: " + ", ".join(w["name"] for w in available), flush=True)
    spec = {"name": "D-thumbnail-smoke-" + out.name.removeprefix("cli-smoke-"),
            "workload": "thumbnail", "params": {"size": 256, "fmt": "png"},
            "files": [p.as_posix() for p in files], "required_tag": None}
    write_json(out / "job-spec.json", spec)
    write_json(out / "input-hashes.json", [{"name": p.name, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in files])
    created = json.loads(cli("submit", "--json", "job", "submit", "--spec", str(out / "job-spec.json"), timeout=120))
    write_json(out / "created.json", created)
    job_id = created["job_id"]
    print(f"Submitted once: {job_id}. Waiting up to 300 seconds.", flush=True)
    cli("watch", "job", "watch", job_id, timeout=300)
    cli("show", "job", "show", job_id)
    archive = out / "result.zip"
    cli("download", "job", "result", job_id, "-o", str(archive), timeout=120)
    report = verify_result(archive, files, job_id)
    write_json(out / "verification.json", report)
    print(json.dumps({k: v for k, v in report.items() if k != "checks"}, ensure_ascii=False, indent=2))
    return 0 if report["all_image_checks_passed"] and report["two_worker_ids_contributed"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1) from error
