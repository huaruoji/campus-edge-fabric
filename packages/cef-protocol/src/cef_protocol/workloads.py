"""Workload registry: parameter schemas shared by coordinator (validation) and
worker agents (execution).

A *workload* is a task type the pool knows how to run. The coordinator never
imports worker code: it validates params against the model registered here, and
the worker looks up its local adapter by the same `kind` string.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class WorkloadKind(StrEnum):
    THUMBNAIL = "thumbnail"
    INFER = "infer"
    BLENDER = "blender"


class ThumbnailParams(BaseModel):
    """Batch image conversion / downscale. CPU only, deterministic output."""

    size: int = Field(256, ge=32, le=2048, description="longest edge in pixels")
    fmt: str = Field("webp", pattern="^(webp|jpeg|png)$")
    quality: int = Field(80, ge=1, le=100)

    model_config = ConfigDict(extra="forbid")


class InferParams(BaseModel):
    """Object detection over images with a local ONNX model (CPU or CUDA EP)."""

    model: str = Field("yolo11n", description="model name resolved by the worker")
    imgsz: int = Field(640, ge=320, le=1280)
    conf: float = Field(0.25, ge=0.0, le=1.0)
    max_det: int = Field(100, ge=1, le=1000)

    model_config = ConfigDict(extra="forbid")


class BlenderParams(BaseModel):
    """One Blender frame per task. The coordinator expands `frames` into tasks."""

    frames: str = Field("1-10", pattern=r"^[0-9, -]+$", description="e.g. 1-120 or 1,5,9-12")
    samples: int = Field(32, ge=1, le=4096)
    resolution: int = Field(50, ge=5, le=100, description="percentage of scene resolution")
    engine: str = Field("CYCLES", pattern="^(CYCLES|BLENDER_EEVEE|BLENDER_EEVEE_NEXT|BLENDER_WORKBENCH)$")

    model_config = ConfigDict(extra="forbid")


WORKLOADS: dict[str, type[BaseModel]] = {
    WorkloadKind.THUMBNAIL: ThumbnailParams,
    WorkloadKind.INFER: InferParams,
    WorkloadKind.BLENDER: BlenderParams,
}

#: unit of work shown to users, per workload (what "有效吞吐" is measured in)
WORKLOAD_UNITS: dict[str, str] = {
    WorkloadKind.THUMBNAIL: "images",
    WorkloadKind.INFER: "images",
    WorkloadKind.BLENDER: "frames",
}


#: The closed vocabulary of things a device may be asked to prepare. There is
#: deliberately no "run this command" entry: the network picks a name, the device
#: decides what that name does.
PROVISION_RECIPES = ("infer", "blender")


class InvalidParams(ValueError):
    pass


def param_model(kind: str) -> type[BaseModel]:
    try:
        return WORKLOADS[kind]
    except KeyError as exc:  # pragma: no cover - guarded by API layer
        raise InvalidParams(f"unknown workload: {kind}") from exc


def validate_params(kind: str, raw: dict[str, Any] | None) -> BaseModel:
    model = param_model(kind)
    try:
        return model.model_validate(raw or {})
    except ValidationError as exc:
        raise InvalidParams(str(exc)) from exc


def param_schema(kind: str) -> dict[str, Any]:
    return param_model(kind).model_json_schema()


def parse_frames(spec: str) -> list[int]:
    """Expand a Blender frame spec into concrete frame numbers (sorted, unique)."""
    out: set[int] = set()
    for chunk in spec.replace(" ", "").split(","):
        if not chunk:
            continue
        if "-" in chunk:
            lo, hi = chunk.split("-", 1)
            lo_i, hi_i = int(lo), int(hi)
            if hi_i < lo_i:
                raise InvalidParams(f"invalid frame range: {chunk}")
            if hi_i - lo_i > 20000:
                raise InvalidParams("frame range too large")
            out.update(range(lo_i, hi_i + 1))
        else:
            out.add(int(chunk))
    if not out:
        raise InvalidParams("empty frame spec")
    return sorted(out)
