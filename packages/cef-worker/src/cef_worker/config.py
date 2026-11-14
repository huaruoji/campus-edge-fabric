"""Worker configuration (flags > env > defaults)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


@dataclass
class WorkerConfig:
    server: str = field(default_factory=lambda: _env("CEF_SERVER", "http://127.0.0.1:8080") or "")
    token: str = field(default_factory=lambda: _env("CEF_TOKEN", "") or "")
    name: str = field(default_factory=lambda: _env("CEF_WORKER_NAME", "") or "")
    tags: list[str] = field(default_factory=list)
    slots: int = field(default_factory=lambda: int(_env("CEF_SLOTS", "1") or 1))
    max_batch: int = field(default_factory=lambda: int(_env("CEF_MAX_BATCH", "4") or 4))
    data_dir: Path = field(default_factory=lambda: Path(_env("CEF_WORKER_DATA", str(Path.home() / ".cef" / "worker")) or "."))
    workloads: list[str] = field(default_factory=list)
    model_dir: Path = field(default_factory=lambda: Path(_env("CEF_MODEL_DIR", str(Path.home() / ".cef" / "models")) or "."))
    blender_bin: str = field(default_factory=lambda: _env("CEF_BLENDER", "blender") or "blender")
    #: refuse to take new leases when CPU/GPU load is above this (idle-aware admission)
    max_cpu_pct: float = field(default_factory=lambda: float(_env("CEF_MAX_CPU", "85") or 85))
    max_gpu_pct: float = field(default_factory=lambda: float(_env("CEF_MAX_GPU", "85") or 85))
    poll_interval_s: float = field(default_factory=lambda: float(_env("CEF_POLL_INTERVAL", "1.0") or 1.0))
    #: stop taking new work when the scratch volume gets this low (GB).
    #: the device belongs to a human - filling their disk is the worst failure mode.
    min_free_gb: float = field(default_factory=lambda: float(_env("CEF_MIN_FREE_GB", "1.0") or 1.0))
    #: recipes the coordinator may trigger on this device. Empty = refuse everything.
    #: This is the owner's consent switch; without it a provision request is rejected.
    allow_provision: list[str] = field(default_factory=lambda: [p.strip() for p in (_env("CEF_ALLOW_PROVISION", "") or "").split(",") if p.strip()])
    heartbeat_interval_s: float = 5.0

    def ensure_dirs(self) -> None:
        (self.data_dir / "work").mkdir(parents=True, exist_ok=True)
        self.model_dir.mkdir(parents=True, exist_ok=True)
