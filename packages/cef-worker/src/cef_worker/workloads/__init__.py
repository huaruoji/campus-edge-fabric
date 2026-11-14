"""Workload adapters available to this worker."""

from __future__ import annotations

from pathlib import Path

from .base import TaskContext, TaskResult, Workload, WorkloadError
from .blender import BlenderWorkload
from .infer import InferWorkload
from .thumbnail import ThumbnailWorkload


def load_adapters(
    enabled: list[str] | None = None,
    model_dir: Path | None = None,
    blender_bin: str = "blender",
) -> dict[str, Workload]:
    """Instantiate adapters; `enabled` (if given) is an allowlist.

    This allowlist is the security boundary: a device never runs a workload the
    owner did not opt into.
    """
    adapters: list[Workload] = [
        ThumbnailWorkload(),
        InferWorkload(model_dir=model_dir),
        BlenderWorkload(binary=blender_bin),
    ]
    chosen = {a.kind: a for a in adapters if not enabled or a.kind in enabled}
    return chosen


__all__ = [
    "BlenderWorkload",
    "InferWorkload",
    "TaskContext",
    "TaskResult",
    "ThumbnailWorkload",
    "Workload",
    "WorkloadError",
    "load_adapters",
]
