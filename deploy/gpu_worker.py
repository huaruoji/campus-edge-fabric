"""Portable, CUDA-required deployment profile for the existing Worker adapter.

No protocol changes. Credentials are read from CEF_TOKEN, never configuration
files or argv. Run --help for installation/check/run/local control commands.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import signal
import socket
import subprocess
import sysconfig
import threading
import urllib.request
from contextlib import contextmanager
from pathlib import Path

from cef_worker.agent import Agent
from cef_worker.config import WorkerConfig
from cef_worker.monitor import find_nvidia_smi
from cef_worker.workloads.base import WorkloadError
from cef_worker.workloads.infer import InferWorkload

DEFAULT_DATA = Path(__file__).resolve().parents[1] / "cef-gpu-data"
MODEL_URL = "https://huggingface.co/unileon-robotics/YOLO11-ONNX/resolve/0e2a3f195ae62a25bc038ee9665b8e4ac5b98793/dynamic/yolo11n.onnx"
MODEL_SHA256 = "b1a053916c9d667d05a53bfb43764a0aabb11dee20795af75b8ce408760685d3"
IMAGE_URL = "https://raw.githubusercontent.com/ultralytics/ultralytics/main/ultralytics/assets/bus.jpg"
IMAGE_SHA256 = "c02019c4979c191eb739ddd944445ef408dad5679acab6fd520ef9d434bfbc63"


def write_json(path, value):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def download_checked(url, path, sha256):
    path = Path(path)
    if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == sha256:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".download")
    try:
        digest = hashlib.sha256()
        with urllib.request.urlopen(url, timeout=120) as response, tmp.open("wb") as dest:
            while chunk := response.read(1024 * 1024):
                digest.update(chunk)
                dest.write(chunk)
        if digest.hexdigest() != sha256:
            raise RuntimeError(f"SHA-256 mismatch for {path.name}; original file preserved")
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


@contextmanager
def worker_lock(data):
    """OS lock survives neither crash nor reboot; prevents duplicate profile runs."""
    data.mkdir(parents=True, exist_ok=True)
    with (data / "gpu-worker.lock").open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError(f"A GPU worker already owns {data}") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def require_cuda(session):
    session.disable_fallback()
    if session.get_providers()[0] != "CUDAExecutionProvider":
        raise RuntimeError("CUDA session failed; refusing CPU-only fallback")


def select_gpu(uuid, device):
    """Bind CUDA and telemetry to one physical UUID before importing ORT."""
    if device != 0:
        raise ValueError("Use --gpu-uuid for physical selection; isolated CUDA --device must be 0")
    if uuid and not re.fullmatch(r"GPU-[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", uuid):
        raise ValueError("--gpu-uuid must be a complete NVIDIA GPU UUID")
    smi = find_nvidia_smi()
    if not smi:
        raise RuntimeError("nvidia-smi unavailable; cannot verify target GPU")
    command = [smi, "--query-gpu=uuid", "--format=csv,noheader"]
    if uuid:
        command.append(f"--id={uuid}")
    result = subprocess.run(command, capture_output=True, text=True, timeout=10, check=True)
    ids = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if len(ids) != 1 or (uuid and ids[0].lower() != uuid.lower()):
        raise RuntimeError("Select exactly one physical GPU with --gpu-uuid or CEF_GPU_UUID")
    if not uuid and os.environ.get("CUDA_VISIBLE_DEVICES") not in (None, "0", ids[0]):
        raise RuntimeError("Ambiguous CUDA_VISIBLE_DEVICES; specify the physical --gpu-uuid explicitly")
    os.environ["CEF_GPU_UUID"] = ids[0]
    os.environ["CUDA_VISIBLE_DEVICES"] = ids[0]
    return ids[0]


class CudaInference(InferWorkload):
    def __init__(self, model_dir, device=0, profile_dir=None):
        import onnxruntime as ort

        super().__init__(model_dir)
        nvidia = Path(sysconfig.get_paths()["purelib"]) / "nvidia"
        dll_dirs = sorted({dll.parent for dll in nvidia.rglob("*.dll")})
        # Keep handles alive: cuDNN loads some engine DLLs lazily, on first Conv.
        self._dll_handles = [os.add_dll_directory(str(d)) for d in dll_dirs] if os.name == "nt" else []
        if dll_dirs:
            os.environ["PATH"] = os.pathsep.join(map(str, dll_dirs)) + os.pathsep + os.environ.get("PATH", "")
        ort.preload_dlls()
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        if profile_dir:
            profile_dir.mkdir(parents=True, exist_ok=True)
            options.enable_profiling = True
            options.profile_file_prefix = str(profile_dir / "worker-ort")
        self.session = ort.InferenceSession(
            str(self.model_dir / "yolo11n.onnx"),
            sess_options=options,
            providers=[
                ("CUDAExecutionProvider", {"device_id": device, "cudnn_conv_algo_search": "HEURISTIC", "use_tf32": 0}),
                "CPUExecutionProvider",
            ],
        )
        require_cuda(self.session)

    def _session(self, model, device="auto"):
        if model != "yolo11n":
            raise WorkloadError("GPU profile supports only the local yolo11n model", retryable=False)
        if device == "cpu":
            return super()._session(model, device="cpu")
        return self.session


def runtime_status(data):
    import psutil

    path = data / "gpu-runtime.json"
    if not path.exists():
        return {"alive": False}
    state = json.loads(path.read_text(encoding="utf-8"))
    try:
        # Creation time also protects against reuse of a stale PID.
        proc = psutil.Process(state["pid"])
        state["alive"] = abs(proc.create_time() - state["process_created"]) < 0.01
    except (psutil.Error, KeyError):
        state["alive"] = False
    return state


def check(model_dir, device):
    import numpy as np

    adapter = CudaInference(model_dir, device)
    x = np.zeros((1, 3, 640, 640), dtype=np.float32)
    result = adapter.session.run(None, {adapter.session.get_inputs()[0].name: x})[0]
    if result.shape != (1, 84, 8400) or not np.isfinite(result).all():
        raise RuntimeError(f"Unexpected model output: {result.shape}")
    cpu = adapter._session("yolo11n", "cpu")
    if cpu.get_providers()[0] != "CPUExecutionProvider":
        raise RuntimeError("Explicit CPU reference request incorrectly routed to CUDA")
    cpu_result = cpu.run(None, {cpu.get_inputs()[0].name: x})[0]
    if cpu_result.shape != result.shape or not np.isfinite(cpu_result).all():
        raise RuntimeError("CPU reference inference failed")
    print(
        json.dumps(
            {"providers": adapter.session.get_providers(), "cpu_providers": cpu.get_providers(),
             "gpu_uuid": os.environ.get("CEF_GPU_UUID"), "output_shape": list(result.shape),
             "device": device, "real_inference_passed": True}
        )
    )


def run(args, data, model_dir):
    import onnxruntime as ort
    import psutil

    if not os.environ.get("CEF_TOKEN"):
        raise RuntimeError("Set CEF_TOKEN in the process environment first")
    if not args.server:
        raise RuntimeError("Specify --server or CEF_SERVER")
    profile_dir = Path(args.profile_dir).resolve() if args.profile_dir else None
    with worker_lock(data):
        for name in ("stop.request", "finish-profile.request", "profile-path.txt"):
            (data / name).unlink(missing_ok=True)
        if os.name == "nt":
            os.environ["PATH"] = str(Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32") + os.pathsep + os.environ["PATH"]
        elif Path("/usr/lib/wsl/lib/nvidia-smi").exists():
            os.environ["PATH"] = "/usr/lib/wsl/lib:" + os.environ.get("PATH", "")
        adapter = CudaInference(model_dir, args.device, profile_dir)
        cfg = WorkerConfig(
            server=args.server,
            name=args.name,
            data_dir=data,
            model_dir=model_dir,
            workloads=["thumbnail", "infer"],
            slots=args.slots,
            max_batch=args.slots,
            tags=list(dict.fromkeys(["cuda", *filter(None, args.tags.split(","))])),
        )
        agent = Agent(cfg)
        agent.adapters["infer"] = adapter
        state = {
            "pid": os.getpid(),
            "process_created": psutil.Process().create_time(),
            "ready": False,
            "data": str(data),
            "server": args.server,
            "name": args.name,
            "gpu_uuid": os.environ.get("CEF_GPU_UUID"),
            "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES"),
            "tags": cfg.tags,
            "ort_version": ort.__version__,
            "providers": adapter.session.get_providers(),
            "provider_options": adapter.session.get_provider_options(),
            "model": str(model_dir / "yolo11n.onnx"),
            "profile_dir": str(profile_dir) if profile_dir else None,
        }
        write_json(data / "gpu-runtime.json", state)

        def control():
            profiled = not bool(profile_dir)
            while not agent._stop.wait(0.5):
                if agent._heartbeat_thread is not None and not state["ready"]:
                    state.update(ready=True, worker_id=agent.worker_id)
                    write_json(data / "gpu-runtime.json", state)
                if (data / "stop.request").exists():
                    agent._stop.set()
                if not profiled and (data / "finish-profile.request").exists():
                    with agent._active_lock:
                        if not agent._active:
                            path = adapter.session.end_profiling()
                            (data / "profile-path.txt").write_text(path, encoding="utf-8")
                            profiled = True
                            logging.info("CUDA profile saved: %s", path)

        threading.Thread(target=control, daemon=True).start()
        signal.signal(signal.SIGTERM, lambda *_: agent._stop.set())
        signal.signal(signal.SIGINT, lambda *_: agent._stop.set())
        try:
            agent.run()
        finally:
            state["ready"] = False
            write_json(data / "gpu-runtime.json", state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["setup-model", "check", "run", "status", "stop", "profile"])
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--model-dir", type=Path)
    parser.add_argument("--server", default=os.environ.get("CEF_SERVER"))
    parser.add_argument("--name", default=socket.gethostname())
    parser.add_argument("--tags", default=socket.gethostname())
    parser.add_argument("--slots", type=int, default=2)
    parser.add_argument("--device", type=int, default=0)
    parser.add_argument("--gpu-uuid", default=os.environ.get("CEF_GPU_UUID"), help="Physical GPU UUID; CUDA logical device remains 0")
    parser.add_argument("--profile-dir")
    args = parser.parse_args()
    if args.slots < 1 or args.device < 0:
        parser.error("slots must be positive and device must be nonnegative")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    data = args.data.resolve()
    model_dir = (args.model_dir or data / "models").resolve()
    if args.action in ("check", "run"):
        select_gpu(args.gpu_uuid, args.device)
    if args.action == "setup-model":
        download_checked(MODEL_URL, model_dir / "yolo11n.onnx", MODEL_SHA256)
        download_checked(IMAGE_URL, data / "bus.jpg", IMAGE_SHA256)
        print("Model and public test image downloaded; SHA-256 verified")
    elif args.action == "check":
        check(model_dir, args.device)
    elif args.action == "run":
        run(args, data, model_dir)
    elif args.action == "status":
        print(json.dumps(runtime_status(data), ensure_ascii=False, indent=2))
    else:
        state = runtime_status(data)
        if not state["alive"]:
            raise RuntimeError("No live GPU worker for this data directory")
        if args.action == "profile" and not state.get("profile_dir"):
            raise RuntimeError("Start with --profile-dir to collect CUDA kernel evidence")
        (data / ("stop.request" if args.action == "stop" else "finish-profile.request")).touch()
        print("Requested graceful stop" if args.action == "stop" else "Requested profile flush when idle")


if __name__ == "__main__":
    main()
