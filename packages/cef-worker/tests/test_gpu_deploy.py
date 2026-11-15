"""Failure boundaries of deployment helpers; no GPU or network required."""
import importlib.util
import io
from pathlib import Path
from types import SimpleNamespace

import pytest

ENTRY = Path(__file__).resolve().parents[3] / "deploy/gpu_worker.py"
spec = importlib.util.spec_from_file_location("gpu_deploy", ENTRY)
gpu = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gpu)


def test_cpu_fallback_is_rejected():
    session = SimpleNamespace(disable_fallback=lambda: None, get_providers=lambda: ["CPUExecutionProvider"])
    with pytest.raises(RuntimeError, match="refusing CPU-only"):
        gpu.require_cuda(session)


def test_bad_download_preserves_existing_model(tmp_path, monkeypatch):
    model = tmp_path / "model.onnx"
    model.write_bytes(b"existing model")
    monkeypatch.setattr(gpu.urllib.request, "urlopen", lambda *a, **kw: io.BytesIO(b"bad download"))
    with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
        gpu.download_checked("https://example.test/model", model, "0" * 64)
    assert model.read_bytes() == b"existing model"
    assert not model.with_suffix(".onnx.download").exists()


def test_duplicate_worker_lock_and_release(tmp_path):
    with gpu.worker_lock(tmp_path), pytest.raises(RuntimeError, match="already owns"), gpu.worker_lock(tmp_path):
        pytest.fail("Duplicate worker must not enter")
    with gpu.worker_lock(tmp_path):
        pass


def test_reused_pid_is_not_live_worker(tmp_path):
    import os

    gpu.write_json(tmp_path / "gpu-runtime.json", {"pid": os.getpid(), "process_created": 0})
    assert gpu.runtime_status(tmp_path)["alive"] is False


def test_explicit_cpu_request_uses_cpu_session(monkeypatch):
    adapter = object.__new__(gpu.CudaInference)
    adapter.session = object()
    cpu = object()
    requests = []
    monkeypatch.setattr(gpu.InferWorkload, "_session", lambda self, model, device: (requests.append((model, device)), cpu)[1])
    assert adapter._session("yolo11n", "cpu") is cpu
    assert requests == [("yolo11n", "cpu")]
    assert adapter._session("yolo11n", "cuda") is adapter.session
    assert adapter._session("yolo11n") is adapter.session


GPU_UUID = "GPU-771e8ac6-f770-428d-55e4-b3ecfb86cf09"


def test_uuid_binds_cuda_and_telemetry(monkeypatch):
    calls = []
    monkeypatch.setattr(gpu, "find_nvidia_smi", lambda: "nvidia-smi")
    monkeypatch.setattr(gpu.subprocess, "run", lambda cmd, **kw: (calls.append(cmd), SimpleNamespace(stdout=GPU_UUID + "\n"))[1])
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "6")
    monkeypatch.setenv("CEF_GPU_UUID", "")
    assert gpu.select_gpu(GPU_UUID, 0) == GPU_UUID
    assert gpu.os.environ["CUDA_VISIBLE_DEVICES"] == gpu.os.environ["CEF_GPU_UUID"] == GPU_UUID
    assert "--id=" + GPU_UUID in calls[0]


def test_multigpu_profile_requires_explicit_uuid(monkeypatch):
    monkeypatch.setattr(gpu, "find_nvidia_smi", lambda: "nvidia-smi")
    monkeypatch.setattr(gpu.subprocess, "run", lambda *a, **kw: SimpleNamespace(stdout=GPU_UUID + "\nGPU-other\n"))
    with pytest.raises(RuntimeError, match="exactly one"):
        gpu.select_gpu(None, 0)
    with pytest.raises(ValueError, match="isolated CUDA"):
        gpu.select_gpu(GPU_UUID, 6)
