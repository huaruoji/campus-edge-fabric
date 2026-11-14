"""Runtime settings. Everything has a sane default so `cef-coord serve` works
with zero arguments on a fresh machine, and everything can be overridden by a
flag or a CEF_* environment variable (flags win).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("CEF_DATA", "./cef-data")))
    token: str = field(default_factory=lambda: os.environ.get("CEF_TOKEN", ""))
    host: str = "0.0.0.0"
    port: int = 8080
    #: a worker is considered offline after this long without a heartbeat
    offline_after_s: int = field(default_factory=lambda: _env_int("CEF_OFFLINE_AFTER", 45))
    #: lease TTL handed to workers; renewed by every heartbeat
    lease_ttl_s: int = field(default_factory=lambda: _env_int("CEF_LEASE_TTL", 90))
    heartbeat_interval_s: int = 5
    #: target seconds of work per lease; adaptive batching aims at this
    target_lease_s: int = field(default_factory=lambda: _env_int("CEF_TARGET_LEASE", 30))
    max_attempts: int = field(default_factory=lambda: _env_int("CEF_MAX_ATTEMPTS", 3))
    poll_limit: int = 64
    #: keep worker utilization samples for this long (dashboard window)
    stats_retention_s: int = field(default_factory=lambda: _env_int("CEF_STATS_RETENTION", 6 * 3600))
    reaper_interval_s: float = field(default_factory=lambda: _env_float("CEF_REAPER_INTERVAL", 3.0))

    @property
    def db_path(self) -> Path:
        return self.data_dir / "cef.db"

    @property
    def blob_dir(self) -> Path:
        return self.data_dir / "blobs"

    @property
    def web_dir(self) -> Path:
        return Path(__file__).parent / "web"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.blob_dir.mkdir(parents=True, exist_ok=True)
