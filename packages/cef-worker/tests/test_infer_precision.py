"""All GPU launch paths must share the same numerical precision policy."""
from types import SimpleNamespace

import pytest
from cef_worker.workloads import infer


def test_generic_cuda_session_disables_tf32(tmp_path, monkeypatch):
    ort = pytest.importorskip("onnxruntime")

    model = tmp_path / "yolo11n.onnx"
    model.write_bytes(b"test model")
    seen = []
    session = SimpleNamespace(get_providers=lambda: ["CUDAExecutionProvider", "CPUExecutionProvider"])
    monkeypatch.setattr(ort, "get_available_providers", lambda: ["CUDAExecutionProvider", "CPUExecutionProvider"])
    monkeypatch.setattr(infer, "_preload_cuda", lambda: None)
    monkeypatch.setattr(ort, "InferenceSession", lambda *a, **kw: seen.append(kw["providers"]) or session)
    adapter = infer.InferWorkload(tmp_path)
    assert adapter._session("yolo11n", "cuda") is session
    assert seen == [[("CUDAExecutionProvider", {"use_tf32": 0})]]


def test_cpu_reference_does_not_enable_cuda(tmp_path, monkeypatch):
    ort = pytest.importorskip("onnxruntime")

    (tmp_path / "yolo11n.onnx").write_bytes(b"test model")
    seen = []
    session = SimpleNamespace(get_providers=lambda: ["CPUExecutionProvider"])
    monkeypatch.setattr(ort, "get_available_providers", lambda: ["CUDAExecutionProvider", "CPUExecutionProvider"])
    monkeypatch.setattr(infer, "_preload_cuda", lambda: None)
    monkeypatch.setattr(ort, "InferenceSession", lambda *a, **kw: seen.append(kw["providers"]) or session)
    assert infer.InferWorkload(tmp_path)._session("yolo11n", "cpu") is session
    assert seen == [["CPUExecutionProvider"]]
