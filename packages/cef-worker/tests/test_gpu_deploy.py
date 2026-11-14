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
