"""Local resource sampling: psutil for CPU/RAM, nvidia-smi (no extra dep) for GPU.

GPU utilisation is only used to decide *admission* (do not pile tasks on a device
its owner is using) and for the dashboard. It is never used as a performance
metric — the metric is completed tasks per second.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

try:  # pragma: no cover - psutil is a hard dependency, keep import defensive
    import psutil
except ImportError:  # pragma: no cover
    psutil = None  # type: ignore[assignment]


@dataclass
class Sample:
    cpu_pct: float | None = None
    mem_pct: float | None = None
    gpu_pct: float | None = None
    gpu_mem_pct: float | None = None
    gpu_name: str | None = None
    disk_free_gb: float | None = None
    disk_total_gb: float | None = None


#: WSL2 exposes the Windows driver through a shim that is NOT on PATH
NVIDIA_CANDIDATES = (
    "/usr/lib/wsl/lib/nvidia-smi",
    "/usr/bin/nvidia-smi",
    "/usr/local/bin/nvidia-smi",
    "/opt/nvidia/bin/nvidia-smi",
)


def find_nvidia_smi() -> str | None:
    found = shutil.which("nvidia-smi")
    if found:
        return found
    for candidate in NVIDIA_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return None


class ResourceMonitor:
    def __init__(self, data_dir: Path | None = None) -> None:
        self._nvidia = find_nvidia_smi()
        self._primed = False
        # disk is measured on the volume that actually holds task scratch space
        self._data_dir = data_dir or Path.home() / ".cef" / "worker"

    def _gpu_query(self, fields: str) -> str | None:
        if not self._nvidia:
            return None
        # CUDA_VISIBLE_DEVICES changes CUDA ordinals, not nvidia-smi indices.
        # An unselected multi-GPU host must not silently report GPU 0.
        uuid = os.environ.get("CEF_GPU_UUID", "").strip()
        if not uuid:
            visible = os.environ.get("CUDA_VISIBLE_DEVICES", "").strip()
            uuid = visible if visible.startswith("GPU-") and "," not in visible else ""
        command = [self._nvidia, f"--query-gpu={fields}", "--format=csv,noheader,nounits"]
        if uuid:
            command.append(f"--id={uuid}")
        out = subprocess.run(command, capture_output=True, text=True, timeout=4, check=False)
        lines = [line.strip() for line in (out.stdout or "").splitlines() if line.strip()]
        return lines[0] if out.returncode == 0 and len(lines) == 1 else None

    def probe_gpu(self) -> tuple[str | None, float | None]:
        if not self._nvidia:
            return None, None
        try:
            first = self._gpu_query("name,memory.total,utilization.gpu")
            if first is None:
                return None, None
            name, mem, util = [p.strip() for p in first.split(",")]
            return f"{name} ({int(float(mem))} MiB)", float(util)
        except Exception:  # noqa: BLE001 - GPU probing must never break the agent
            return None, None

    def sample(self) -> Sample:
        s = Sample()
        try:
            usage = shutil.disk_usage(self._data_dir)
            s.disk_free_gb = round(usage.free / 1e9, 2)
            s.disk_total_gb = round(usage.total / 1e9, 2)
        except OSError:
            pass
        if psutil is not None:
            try:
                # first call after import returns 0.0, prime it once
                if not self._primed:
                    psutil.cpu_percent(interval=None)
                    self._primed = True
                s.cpu_pct = float(psutil.cpu_percent(interval=None))
                s.mem_pct = float(psutil.virtual_memory().percent)
            except Exception:  # noqa: BLE001
                pass
        if self._nvidia:
            try:
                line = self._gpu_query("utilization.gpu,memory.used,memory.total,name")
                if line is None:
                    return s
                util, used, total, gname = [p.strip() for p in line.split(",", 3)]
                s.gpu_pct = float(util)
                s.gpu_mem_pct = round(100.0 * float(used) / max(1.0, float(total)), 1)
                s.gpu_name = gname
            except Exception:  # noqa: BLE001
                pass
        return s
