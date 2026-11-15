"""Read-only B-to-D device handoff. Never exports credentials or process argv."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import time
from pathlib import Path

import psutil
from cef_worker.monitor import ResourceMonitor, find_nvidia_smi

ROOT = Path(__file__).resolve().parents[1]


def command(args):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=10, check=False)
        return result.stdout.strip() if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def collect(data, owner=None, network=None, authorized=False):
    runtime_path = data / "gpu-runtime.json"
    raw = json.loads(runtime_path.read_text(encoding="utf8")) if runtime_path.exists() else {}
    runtime_keys = ("pid", "process_created", "ready", "name", "data", "model", "worker_id", "gpu_uuid",
                    "cuda_visible_devices", "ort_version", "providers", "provider_options")
    runtime = {key: raw[key] for key in runtime_keys if key in raw}
    worker_id = (data / "worker.id").read_text().strip() if (data / "worker.id").exists() else None
    selected = runtime.get("gpu_uuid") or os.environ.get("CEF_GPU_UUID")
    smi = find_nvidia_smi()
    gpu_args = [smi, "--query-gpu=index,uuid,name,driver_version,memory.total", "--format=csv,noheader"] if smi else None
    hashes = {}
    for name in ("deploy/gpu_worker.py", "packages/cef-worker/src/cef_worker/monitor.py", "packages/cef-worker/src/cef_worker/workloads/infer.py"):
        path = ROOT / name
        hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
    packages = {}
    for name in ("onnxruntime-gpu", "onnxruntime", "cef-worker", "cef-protocol"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            pass
    # Set the selected UUID only within this inventory process, matching the live Worker.
    if selected:
        os.environ["CEF_GPU_UUID"] = selected
    sample = ResourceMonitor(data).sample()
    return {
        "captured_at": time.time(), "owner_statement": owner, "authorized_by_owner_statement": authorized,
        "network_description": network, "hostname": platform.node(), "os": platform.platform(),
        "python": platform.python_version(), "cpu_logical": psutil.cpu_count(), "cpu_physical": psutil.cpu_count(logical=False),
        "cpu_description": command(["lscpu", "-J"]) if platform.system() == "Linux" else platform.processor(),
        "ram_bytes": psutil.virtual_memory().total, "worker_id": worker_id, "runtime": runtime,
        "visible_gpu_inventory": command(gpu_args) if gpu_args else None,
        "selected_gpu_uuid": selected,
        "selected_gpu_inventory": command([*gpu_args, "--id=" + selected]) if gpu_args and selected else None,
        "target_sample": vars(sample), "packages": packages,
        "repository_commit": command(["git", "-C", str(ROOT), "rev-parse", "HEAD"]),
        "source_sha256": hashes,
        "scope": "Inventory only; does not prove stop/restart, power loss, network recovery or inference correctness.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True, help="Existing Worker data directory; never copy another worker.id")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--owner")
    parser.add_argument("--network")
    parser.add_argument("--authorized", action="store_true", help="Only set after owner authorization was actually obtained")
    args = parser.parse_args()
    result = collect(args.data.resolve(), args.owner, args.network, args.authorized)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8")
    print(f"Inventory written: {args.output}")


if __name__ == "__main__":
    main()
