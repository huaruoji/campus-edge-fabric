"""Human-readable one-liners for jobs and tasks.

The dashboard should never make a judge read a JSON blob to find out what ran.
This module is the single place that turns (workload, params, worker meta) into
Chinese text, so the API, the CLI and the tests agree.
"""

from __future__ import annotations

from typing import Any

from .workloads import WorkloadKind


def _kb(size: int | None) -> str:
    if not size:
        return "—"
    if size < 1024:
        return f"{size}B"
    if size < 1024 * 1024:
        return f"{size / 1024:.0f}KB"
    return f"{size / (1024 * 1024):.1f}MB"


def describe_job(workload: str, params: dict[str, Any] | None) -> str:
    p = params or {}
    if workload == WorkloadKind.THUMBNAIL:
        return f"缩略图，长边 {p.get('size', 256)}px，{str(p.get('fmt', 'webp')).upper()}，质量 {p.get('quality', 80)}"
    if workload == WorkloadKind.INFER:
        return (
            f"目标检测，模型 {p.get('model', 'yolo11n')}，输入 {p.get('imgsz', 640)}px，"
            f"置信度 {p.get('conf', 0.25)}"
        )
    if workload == WorkloadKind.BLENDER:
        return (
            f"渲染，第 {p.get('frames', '?')} 帧，采样 {p.get('samples', 32)}，"
            f"分辨率 {p.get('resolution', 50)}%，{p.get('engine', 'CYCLES')}"
        )
    return f"{workload}，参数 {p}" if p else workload


def describe_task(workload: str, params: dict[str, Any] | None, meta: dict[str, Any] | None) -> str | None:
    p, m = params or {}, meta or {}
    if workload == WorkloadKind.THUMBNAIL:
        src, out = m.get("src_size"), m.get("out_size")
        if isinstance(src, list) and len(src) == 2 and isinstance(out, list) and len(out) == 2:
            return (
                f"{src[0]}×{src[1]} → {out[0]}×{out[1]}，"
                f"{str(m.get('format', 'webp')).upper()} {_kb(m.get('out_bytes'))}"
            )
        return None
    if workload == WorkloadKind.INFER:
        if "detections" in m:
            return f"{m['detections']} 个目标，{m.get('provider', 'CPU')}"
        return None
    if workload == WorkloadKind.BLENDER:
        frame = m.get("frame", p.get("frame"))
        return f"第 {frame} 帧" if frame is not None else None
    return None