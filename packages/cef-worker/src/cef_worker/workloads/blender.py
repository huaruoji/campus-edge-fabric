"""Blender frame rendering adapter.

One task = one frame. The coordinator expands `params.frames` ("1-120") into 120
tasks, so slow frames do not block fast ones and the pool's progress is visible
frame by frame — which is exactly the "distributed animation render farm" story.

Safety: the agent only ever runs a fixed argv (`blender -b ... -f <int>`) with a
validated integer frame number. No user string reaches a shell.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from .base import TaskContext, TaskResult, Timer, Workload, WorkloadError

_CANDIDATES = [
    "blender",
    r"C:\Program Files\Blender Foundation\Blender 4.2\blender.exe",
    r"C:\Program Files\Blender Foundation\Blender 4.1\blender.exe",
    "/Applications/Blender.app/Contents/MacOS/Blender",
    "/usr/bin/blender",
    "/usr/local/bin/blender",
]


class BlenderWorkload(Workload):
    kind = "blender"
    unit = "frames"

    def __init__(self, binary: str = "blender") -> None:
        self.binary = binary

    def _resolve(self) -> str | None:
        if Path(self.binary).exists():
            return self.binary
        found = shutil.which(self.binary)
        if found:
            return found
        for cand in _CANDIDATES:
            if Path(cand).exists():
                return cand
        return None

    def probe(self) -> dict[str, Any]:
        exe = self._resolve()
        if not exe:
            return {
                "kind": self.kind,
                "available": False,
                "version": None,
                "reason": "blender binary not found (set CEF_BLENDER=/path/to/blender)",
            }
        try:
            out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=20, check=False)
            version = (out.stdout or "").strip().splitlines()[0] if out.stdout else "unknown"
        except Exception as exc:  # noqa: BLE001
            return {"kind": self.kind, "available": False, "version": None, "reason": f"{exc}"}
        return {"kind": self.kind, "available": True, "version": version, "reason": None, "engine": "CYCLES"}

    def run(self, ctx: TaskContext) -> TaskResult:
        exe = self._resolve()
        if not exe:
            raise WorkloadError("blender binary not found", retryable=False)
        if not ctx.inputs:
            raise WorkloadError("no .blend uploaded for this job", retryable=False)
        frame = int(ctx.params.get("frame", 1))
        samples = int(ctx.params.get("samples", 32))
        resolution = int(ctx.params.get("resolution", 50))
        engine = str(ctx.params.get("engine", "CYCLES"))
        scene = ctx.inputs[0]
        prefix = ctx.outdir / "frame_"
        expr = (
            "import bpy;"
            f"bpy.context.scene.render.resolution_percentage={resolution};"
            f"bpy.context.scene.render.engine='{engine}';"
            f"bpy.context.scene.cycles.samples={samples}"
            if engine == "CYCLES"
            else f"import bpy;bpy.context.scene.render.resolution_percentage={resolution};"
            f"bpy.context.scene.render.engine='{engine}'"
        )
        argv = [
            exe,
            "-b",
            str(scene),
            "--python-expr",
            expr,
            "-o",
            str(prefix) + "####",
            "-F",
            "PNG",
            "-f",
            str(frame),
        ]
        with Timer() as t:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=3600, check=False)
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "")[-800:]
            raise WorkloadError(f"blender exited {proc.returncode}: {tail}")
        produced = sorted(ctx.outdir.glob(f"frame_{frame:04d}.png"))
        if not produced:
            raise WorkloadError("blender finished but produced no frame", retryable=False)
        return TaskResult(
            files=produced,
            duration_s=t.elapsed,
            meta={
                "kind": self.kind,
                "frame": frame,
                "engine": engine,
                "samples": samples,
                "resolution_pct": resolution,
                "blender": exe,
                "platform": sys.platform,
            },
        )
