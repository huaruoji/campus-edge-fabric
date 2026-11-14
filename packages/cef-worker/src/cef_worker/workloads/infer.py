"""Local ONNX object detection (YOLOv8/YOLO11 style export).

Why this is the flagship adapter: a single `pip install onnxruntime` turns any
laptop — NVIDIA or CPU-only — into a working node, the output is a JSON detection
list that can be checked against a reference run, and "images/second" is a clean
unit for the P_i / P_pool comparison the rules ask for.

The model is *local* (never fetched from the network by the agent): drop
`yolo11n.onnx` into `~/.cef/models/` (or pass --model-dir).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .base import TaskContext, TaskResult, Workload, WorkloadError

_SESSION_CACHE: dict[tuple[str, str], Any] = {}
#: hashing a 12 MB model on every heartbeat would be silly; key on (path, mtime, size)
_MODEL_HASH_CACHE: dict[tuple[str, float, int], dict[str, Any]] = {}


def _model_ref(path: Path) -> dict[str, Any]:
    import hashlib

    try:
        stat = path.stat()
    except OSError:
        return {"name": path.stem, "sha256": "", "size": 0}
    key = (str(path), stat.st_mtime, stat.st_size)
    cached = _MODEL_HASH_CACHE.get(key)
    if cached:
        return cached
    digest = hashlib.sha256()
    try:
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                digest.update(chunk)
    except OSError:
        return {"name": path.stem, "sha256": "", "size": stat.st_size}
    ref = {"name": path.stem, "sha256": digest.hexdigest(), "size": stat.st_size}
    _MODEL_HASH_CACHE[key] = ref
    return ref


class InferWorkload(Workload):
    kind = "infer"
    unit = "images"

    def __init__(self, model_dir: Path | None = None) -> None:
        self.model_dir = Path(model_dir) if model_dir else Path.home() / ".cef" / "models"

    # ------------------------------------------------------------------ probe
    def probe(self) -> dict[str, Any]:
        try:
            import numpy  # noqa: F401
            import onnxruntime as ort
        except Exception as exc:  # noqa: BLE001
            return {
                "kind": self.kind,
                "available": False,
                "version": None,
                "reason": f"onnxruntime/numpy missing ({exc}); pip install 'cef-worker[infer]'",
            }
        models = [_model_ref(p) for p in sorted(self.model_dir.glob("*.onnx"))]
        providers = ort.get_available_providers()
        engine = "cuda" if "CUDAExecutionProvider" in providers else "cpu"
        if not models:
            return {
                "kind": self.kind,
                "available": False,
                "version": ort.__version__,
                "reason": f"no .onnx model in {self.model_dir}",
                "engine": engine,
                "models": [],
            }
        return {
            "kind": self.kind,
            "available": True,
            "version": f"onnxruntime-{ort.__version__}",
            "reason": None,
            "engine": engine,
            "models": models,
        }

    def _session(self, model: str) -> Any:
        import onnxruntime as ort

        path = Path(model)
        if not path.exists():
            path = self.model_dir / f"{model}.onnx"
        if not path.exists():
            raise WorkloadError(f"model not found: {model} (looked in {self.model_dir})", retryable=False)
        key = (str(path), ort.get_available_providers().__str__())
        if key not in _SESSION_CACHE:
            providers = [p for p in ("CUDAExecutionProvider", "CPUExecutionProvider") if p in ort.get_available_providers()]
            _SESSION_CACHE[key] = ort.InferenceSession(str(path), providers=providers or None)
        return _SESSION_CACHE[key]

    # -------------------------------------------------------------------- run
    def run(self, ctx: TaskContext) -> TaskResult:
        import numpy as np
        from PIL import Image, ImageOps

        if not ctx.inputs:
            raise WorkloadError("no image for infer task", retryable=False)
        model = str(ctx.params.get("model", "yolo11n"))
        imgsz = int(ctx.params.get("imgsz", 640))
        conf_th = float(ctx.params.get("conf", 0.25))
        max_det = int(ctx.params.get("max_det", 100))

        t0 = time.perf_counter()
        sess = self._session(model)
        src = ctx.inputs[0]
        try:
            with Image.open(src) as im:
                rgb = ImageOps.exif_transpose(im).convert("RGB")
                w, h = rgb.size
                canvas, scale, pad = _letterbox(rgb, imgsz)
                x = np.asarray(canvas, dtype=np.float32) / 255.0
                x = np.transpose(x, (2, 0, 1))[None, ...]
        except Exception as exc:  # noqa: BLE001
            raise WorkloadError(f"cannot read image: {exc}", retryable=False) from exc

        input_name = sess.get_inputs()[0].name
        raw = sess.run(None, {input_name: x})[0]
        boxes, scores, classes = _postprocess(raw, conf_th, max_det)
        dets = _scale_boxes(boxes, scores, classes, scale, pad, w, h)
        infer_s = time.perf_counter() - t0

        out = ctx.outdir / f"{Path(ctx.input_names[0] if ctx.input_names else src.name).stem}.json"
        payload = {
            "model": model,
            "image": ctx.input_names[0] if ctx.input_names else src.name,
            "size": [w, h],
            "detections": dets,
            "provider": sess.get_providers()[0],
        }
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        return TaskResult(
            files=[out],
            duration_s=infer_s,
            meta={
                "kind": self.kind,
                "model": model,
                "detections": len(dets),
                "provider": sess.get_providers()[0],
                "infer_s": round(infer_s, 4),
                "input_bytes": src.stat().st_size,
            },
        )


def _letterbox(im: Any, size: int) -> tuple[Any, float, tuple[int, int]]:
    from PIL import Image

    w, h = im.size
    scale = min(size / w, size / h)
    nw, nh = int(round(w * scale)), int(round(h * scale))
    resized = im.resize((nw, nh), Image.BILINEAR)
    canvas = Image.new("RGB", (size, size), (114, 114, 114))
    pad = ((size - nw) // 2, (size - nh) // 2)
    canvas.paste(resized, pad)
    return canvas, scale, pad


def _postprocess(raw: Any, conf_th: float, max_det: int) -> tuple[Any, Any, Any]:
    import numpy as np

    out = raw
    if out.ndim == 3:
        out = out[0]
    if out.shape[0] < out.shape[1]:  # (4+nc, N) -> (N, 4+nc)
        out = out.T
    boxes_cxcywh = out[:, :4]
    cls_scores = out[:, 4:]
    if cls_scores.size == 0:
        return np.zeros((0, 4), np.float32), np.zeros((0,), np.float32), np.zeros((0,), np.int64)
    classes = cls_scores.argmax(axis=1)
    scores = cls_scores.max(axis=1)
    keep = scores >= conf_th
    boxes_cxcywh, scores, classes = boxes_cxcywh[keep], scores[keep], classes[keep]
    if len(scores) == 0:
        return np.zeros((0, 4), np.float32), np.zeros((0,), np.float32), np.zeros((0,), np.int64)
    cx, cy, bw, bh = boxes_cxcywh.T
    boxes = np.stack([cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2], axis=1)
    idx = _nms(boxes, scores, 0.45)[:max_det]
    return boxes[idx], scores[idx], classes[idx]


def _nms(boxes: Any, scores: Any, iou_th: float) -> list[int]:
    import numpy as np

    order = scores.argsort()[::-1]
    keep: list[int] = []
    while order.size > 0:
        i = int(order[0])
        keep.append(i)
        if order.size == 1:
            break
        xx1 = np.maximum(boxes[i, 0], boxes[order[1:], 0])
        yy1 = np.maximum(boxes[i, 1], boxes[order[1:], 1])
        xx2 = np.minimum(boxes[i, 2], boxes[order[1:], 2])
        yy2 = np.minimum(boxes[i, 3], boxes[order[1:], 3])
        w = np.maximum(0.0, xx2 - xx1)
        h = np.maximum(0.0, yy2 - yy1)
        inter = w * h
        area_i = (boxes[i, 2] - boxes[i, 0]) * (boxes[i, 3] - boxes[i, 1])
        area_o = (boxes[order[1:], 2] - boxes[order[1:], 0]) * (boxes[order[1:], 3] - boxes[order[1:], 1])
        iou = inter / np.maximum(1e-9, area_i + area_o - inter)
        order = order[1:][iou <= iou_th]
    return keep


def _scale_boxes(boxes: Any, scores: Any, classes: Any, scale: float, pad: tuple[int, int], w: int, h: int) -> list[dict[str, Any]]:
    out = []
    for (x1, y1, x2, y2), s, c in zip(boxes, scores, classes, strict=False):
        left = max(0.0, (float(x1) - pad[0]) / scale)
        top = max(0.0, (float(y1) - pad[1]) / scale)
        right = min(float(w), (float(x2) - pad[0]) / scale)
        bottom = min(float(h), (float(y2) - pad[1]) / scale)
        out.append(
            {
                "class_id": int(c),
                "conf": round(float(s), 4),
                "xyxy": [round(left, 1), round(top, 1), round(right, 1), round(bottom, 1)],
            }
        )
    return out
