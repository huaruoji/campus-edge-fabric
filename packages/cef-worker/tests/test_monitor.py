from types import SimpleNamespace

import pytest
from cef_worker import monitor

UUID = "GPU-771e8ac6-f770-428d-55e4-b3ecfb86cf09"


@pytest.fixture()
def resource(monkeypatch, tmp_path):
    monkeypatch.delenv("CEF_GPU_UUID", raising=False)
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
    monkeypatch.setattr(monitor, "find_nvidia_smi", lambda: "nvidia-smi")
    return monitor.ResourceMonitor(tmp_path)


def test_unselected_multigpu_is_unknown_not_gpu_zero(resource, monkeypatch):
    monkeypatch.setattr(monitor.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout="0, 100, 1000, GPU0\n90, 200, 1000, GPU6\n"))
    assert resource.sample().gpu_pct is None
    assert resource.probe_gpu() == (None, None)


@pytest.mark.parametrize("env", ["CEF_GPU_UUID", "CUDA_VISIBLE_DEVICES"])
def test_target_uuid_used_for_probe_and_samples(resource, monkeypatch, env):
    monkeypatch.setenv(env, UUID)
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        text = "A6000, 49140, 27\n" if "--query-gpu=name,memory.total,utilization.gpu" in command else "27, 2457, 49140, A6000\n"
        return SimpleNamespace(returncode=0, stdout=text)

    monkeypatch.setattr(monitor.subprocess, "run", run)
    assert resource.probe_gpu() == ("A6000 (49140 MiB)", 27.0)
    sample = resource.sample()
    assert sample.gpu_pct == 27 and sample.gpu_mem_pct == 5 and sample.gpu_name == "A6000"
    assert all("--id=" + UUID in cmd for cmd in commands)


def test_failed_gpu_query_is_not_zero_utilization(resource, monkeypatch):
    monkeypatch.setenv("CEF_GPU_UUID", UUID)
    monkeypatch.setattr(monitor.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stdout="No devices were found"))
    assert resource.sample().gpu_pct is None
    assert resource.probe_gpu() == (None, None)
