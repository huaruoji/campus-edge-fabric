"""Actual live GPU batch, result validation and worker-session kernel evidence."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
import zipfile
from collections import Counter
from datetime import datetime
from pathlib import Path

import numpy as np
import onnxruntime as ort
from cef_protocol.client import CefClient
from cef_worker.workloads.infer import _letterbox, _postprocess, _scale_boxes
from PIL import Image, ImageDraw, ImageEnhance, ImageOps

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--data", type=Path, default=Path(__file__).resolve().parents[1] / "cef-gpu-data")
parser.add_argument("--server", default=os.environ.get("CEF_SERVER"))
parser.add_argument("--tag", required=True, help="Unique tag of the intended local GPU Worker")
parser.add_argument("--output", type=Path)
parser.add_argument("--count", type=int, choices=range(1, 25), default=24)
parser.add_argument("--allow-missing-process-sample", action="store_true",
                    help="For WSL NVML limitations; still requires real CUDA kernel trace and result checks")
args = parser.parse_args()
if not args.server or not os.environ.get("CEF_TOKEN"):
    parser.error("Set CEF_SERVER/--server and CEF_TOKEN")
DATA = args.data.resolve()
OUT = (args.output or DATA / "validation" / datetime.now().strftime("%Y%m%d-%H%M%S")).resolve()
if (OUT / "job-id.txt").exists():
    raise RuntimeError("Output already contains a submitted job. Use a new directory; do not duplicate it.")
if (DATA / "profile-path.txt").exists():
    raise RuntimeError("Profile already ended. Restart an idle Worker with profiling before a new validation.")
INPUTS = OUT / "inputs"
OUT.mkdir(parents=True, exist_ok=True)
with Image.open(DATA / "bus.jpg") as original:
    original = original.convert("RGB")
    original.thumbnail((1024, 1024))
    original.save(OUT / "bus-input.jpg", quality=85)
SMI = shutil.which("nvidia-smi")
if not SMI:
    for candidate in ["C:/Windows/System32/nvidia-smi.exe", "/usr/lib/wsl/lib/nvidia-smi"]:
        if Path(candidate).is_file():
            SMI = candidate
            break
if not SMI:
    raise RuntimeError("nvidia-smi unavailable; cannot record GPU process evidence")
INPUTS.mkdir(exist_ok=True)
OUT.mkdir(parents=True, exist_ok=True)
runtime = json.loads((DATA / "gpu-runtime.json").read_text(encoding="utf-8"))
assert runtime.get("profile_dir") and runtime.get("ready"), "Start Worker with profiling and wait for registration"
local_id = (DATA / "worker.id").read_text().strip()
samples = []
stop = threading.Event()


def monitor():
    while not stop.is_set():
        row = {"time": time.time()}
        for label, query in [
            ("gpu", "--query-gpu=index,name,uuid,utilization.gpu,memory.used,memory.total"),
            ("processes", "--query-compute-apps=pid,process_name,gpu_uuid,used_gpu_memory"),
        ]:
            p = subprocess.run([SMI, query, "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=8)
            row[label] = p.stdout.strip()
        samples.append(row)
        stop.wait(0.5)


thread = threading.Thread(target=monitor, daemon=True)
thread.start()
client = CefClient(args.server, os.environ["CEF_TOKEN"], timeout=60)
try:
    workers = client.workers().model_dump(mode="json")["workers"]
    local = next(w for w in workers if w["worker_id"] == local_id)
    assert local["state"] == "online"
    assert args.tag in local["tags"], "Tag must belong to this local Worker"
    assert sum(args.tag in w["tags"] for w in workers) == 1, "Choose a tag unique to this Worker"
    assert any(w["kind"] == "infer" and w["available"] for w in local["workloads"])
    paths = []
    with Image.open(OUT / "bus-input.jpg") as im:
        for i in range(args.count):
            variant = ImageEnhance.Brightness(im).enhance(0.85 + (i % 12) * 0.025)
            if i >= 12:
                variant = ImageOps.mirror(variant)
            path = INPUTS / f"bus-variant-{i:02d}.jpg"
            variant.save(path, quality=85)
            assert path.stat().st_size <= 500_000
            paths.append(path)
    created = client.submit_job(
        paths,
        "infer",
        {"model": "yolo11n", "imgsz": 640, "conf": 0.25, "max_det": 100},
        name=f"cuda-validation-{args.tag}-{args.count}",
        required_tag=args.tag,
    )
    (OUT / "job-id.txt").write_text(created.job_id, encoding="utf-8")
    print("JOB_ID", created.job_id, flush=True)
    prev = None
    for job in client.wait(created.job_id, poll_s=2, timeout_s=300):
        current = (job.succeeded, job.failed, str(job.state))
        if current != prev:
            print("Progress", current, flush=True)
            prev = current
    detail = client.get_job(created.job_id).model_dump(mode="json")
    (OUT / "job-detail.json").write_text(json.dumps(detail, ensure_ascii=False, indent=2), encoding="utf-8")
    assert detail["succeeded"] == args.count and detail["failed"] == 0, detail["per_worker"]
    archive_path = client.download_result(created.job_id, OUT / "result.zip")
    (DATA / "finish-profile.request").touch()
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    cpu = ort.InferenceSession(str(DATA / "models/yolo11n.onnx"), sess_options=options, providers=["CPUExecutionProvider"])
    checks = []
    with zipfile.ZipFile(archive_path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert len(manifest["entries"]) == args.count
        for entry in manifest["entries"]:
            assert entry["worker_id"] == local_id
            raw = archive.read(f"outputs/{entry['idx']:06d}_{entry['output_files'][0]}")
            assert hashlib.sha256(raw).hexdigest() == entry["output_sha256"]
            result = json.loads(raw)
            assert result["provider"] == "CUDAExecutionProvider"
            with Image.open(paths[entry["idx"]]) as im:
                canvas, scale, pad = _letterbox(im.convert("RGB"), 640)
                x = np.ascontiguousarray(np.asarray(canvas, dtype=np.float32).transpose(2, 0, 1)[None] / 255)
                pred = cpu.run(None, {cpu.get_inputs()[0].name: x})[0]
                reference = _scale_boxes(*_postprocess(pred, 0.25, 100), scale, pad, *im.size)
                assert result["size"] == list(im.size)
            actual = result["detections"]
            assert {0, 5}.issubset({d["class_id"] for d in actual}), actual
            assert len(actual) == len(reference)
            remaining = reference.copy()
            for a in actual:
                matches = [
                    b
                    for b in remaining
                    if a["class_id"] == b["class_id"]
                    and abs(a["conf"] - b["conf"]) < 0.002
                    and max(abs(c - d) for c, d in zip(a["xyxy"], b["xyxy"], strict=True)) <= 0.3
                ]
                assert matches, (entry["idx"], a, reference)
                remaining.remove(matches[0])
            checks.append(
                {
                    "idx": entry["idx"],
                    "worker_id": entry["worker_id"],
                    "provider": result["provider"],
                    "detections": len(actual),
                    "sha256_ok": True,
                    "cpu_reference_match": True,
                }
            )
            if entry["idx"] == 0:
                with Image.open(paths[0]) as im:
                    draw = ImageDraw.Draw(im)
                    for d in actual:
                        draw.rectangle(d["xyxy"], outline="lime", width=3)
                        draw.text(d["xyxy"][:2], f"class {d['class_id']} {d['conf']:.3f}", fill="red")
                    im.save(OUT / "detection-preview.jpg", quality=90)
    deadline = time.monotonic() + 20
    while not (DATA / "profile-path.txt").exists() and time.monotonic() < deadline:
        time.sleep(0.5)
    profile = Path((DATA / "profile-path.txt").read_text(encoding="utf-8"))
    events = json.loads(profile.read_text(encoding="utf-8"))
    kernels = [e for e in events if e.get("cat") == "Node" and e.get("args", {}).get("provider")]
    providers = Counter(e["args"]["provider"] for e in kernels)
    cuda_ops = Counter(e.get("args", {}).get("op_name") for e in kernels if e["args"]["provider"] == "CUDAExecutionProvider")
    model_runs = sum(e.get("name") == "model_run" for e in events)
    assert providers["CUDAExecutionProvider"] > 0 and model_runs >= args.count
    assert any("Conv" in str(op) for op in cuda_ops), cuda_ops
    stop.set()
    thread.join(timeout=12)
    process_seen = any(str(runtime["pid"]) in [line.split(",")[0].strip() for line in s["processes"].splitlines()] for s in samples)
    assert process_seen or args.allow_missing_process_sample, "Worker PID absent from NVIDIA samples; inspect GPU monitoring support"
    (OUT / "gpu-samples.json").write_text(json.dumps(samples, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {
        "job_id": created.job_id,
        "verified_images": len(checks),
        "worker_id": local_id,
        "worker_pid": runtime["pid"],
        "per_worker": detail["per_worker"],
        "runtime": runtime,
        "kernel_provider_counts": dict(providers),
        "cuda_op_counts": dict(cuda_ops),
        "profile_model_runs": model_runs,
        "gpu_compute_pid_observed": process_seen,
        "profile": str(profile),
        "checks": checks,
        "workers_after": client.workers().model_dump(mode="json")["workers"],
        "model_sha256": hashlib.sha256((DATA / "models/yolo11n.onnx").read_bytes()).hexdigest(),
        "input_bytes": sum(p.stat().st_size for p in paths),
        "scope": f"{args.count} public bus-image variants; functional GPU validation, not throughput benchmark",
    }
    (OUT / "verification.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        "VERIFIED",
        json.dumps(
            {
                k: summary[k]
                for k in ["job_id", "verified_images", "worker_pid", "kernel_provider_counts", "profile_model_runs", "gpu_compute_pid_observed"]
            }
        ),
        flush=True,
    )
finally:
    stop.set()
    thread.join(timeout=12)
    (OUT / "gpu-samples.json").write_text(json.dumps(samples, ensure_ascii=False, indent=2), encoding="utf-8")
    client.close()
