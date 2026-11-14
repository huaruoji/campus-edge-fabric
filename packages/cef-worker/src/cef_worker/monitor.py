"""Local resource sampling: psutil for CPU/RAM, nvidia-smi (no extra dep) for GPU.

GPU utilisation is only used to decide *admission* (do not pile tasks on a device
its owner is using) and for the dashboard. It is never used as a performance
metric — the metric is completed tasks per second.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass

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


class ResourceMonitor:
    def __init__(self) -> None:
        self._nvidia = shutil.which("nvidia-smi")
        self._primed = False

    def probe_gpu(self) -> tuple[str | None, float | None]:
        if not self._nvidia:
            return None, None
        try:
            out = subprocess.run(
                [
                    self._nvidia,
                    "--query-gpu=name,memory.total,utilization.gpu",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=4,
                check=False,
            )
            first = (out.stdout or "").strip().splitlines()[0]
            name, mem, util = [p.strip() for p in first.split(",")]
            return f"{name} ({int(float(mem))} MiB)", float(util)
        except Exception:  # noqa: BLE001 - GPU probing must never break the agent
            return None, None

    def sample(self) -> Sample:
        s = Sample()
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
                out = subprocess.run(
                    [
                        self._nvidia,
                        "--query-gpu=utilization.gpu,memory.used,memory.total,name",
                        "--format=csv,noheader,nounits",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=4,
                    check=False,
                )
                line = (out.stdout or "").strip().splitlines()[0]
                util, used, total, gname = [p.strip() for p in line.split(",", 3)]
                s.gpu_pct = float(util)
                s.gpu_mem_pct = round(100.0 * float(used) / max(1.0, float(total)), 1)
                s.gpu_name = gname
            except Exception:  # noqa: BLE001
                pass
        return s
