"""Workload adapter contract.

An adapter is the *only* way user work reaches a device. It is a fixed Python
entry point that receives a validated parameter dict and a list of local input
files — never a shell string, never arbitrary code from the network.
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class TaskContext:
    task_id: str
    job_id: str
    idx: int
    attempt: int
    params: dict[str, Any]
    inputs: list[Path]
    input_names: list[str]
    workdir: Path
    outdir: Path
    log: logging.Logger = field(default_factory=lambda: logging.getLogger("cef.workload"))

    @property
    def scratch(self) -> Path:
        self.workdir.mkdir(parents=True, exist_ok=True)
        return self.workdir


@dataclass
class TaskResult:
    files: list[Path] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)
    duration_s: float = 0.0


class WorkloadError(RuntimeError):
    def __init__(self, message: str, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class Workload(ABC):
    kind: str = ""
    unit: str = "tasks"

    @abstractmethod
    def probe(self) -> dict[str, Any]:
        """Return {"kind","available","version","reason"}. Must never raise."""

    @abstractmethod
    def run(self, ctx: TaskContext) -> TaskResult:
        """Execute one task. Raise WorkloadError for expected failures."""

    def available(self) -> bool:
        try:
            return bool(self.probe().get("available"))
        except Exception:  # noqa: BLE001
            return False


class Timer:
    def __init__(self) -> None:
        self.t0 = time.perf_counter()

    def __enter__(self) -> Timer:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.elapsed = time.perf_counter() - self.t0
