"""CPU-only batch image conversion.

Genuinely useful on its own (a design/photo team shrinking a shoot for a deck or
a web gallery), dependency-light (Pillow), deterministic and cheap to verify:
same input + same params -> byte-identical output. It is also the fallback that
guarantees *every* device can contribute on demo day even without a GPU or
Blender, so the pool never looks idle because of a missing dependency.
"""

from __future__ import annotations

import hashlib
from typing import Any

from .base import TaskContext, TaskResult, Timer, Workload, WorkloadError


class ThumbnailWorkload(Workload):
    kind = "thumbnail"
    unit = "images"

    def probe(self) -> dict[str, Any]:
        try:
            import PIL
            from PIL import Image  # noqa: F401
        except Exception as exc:  # noqa: BLE001
            return {"kind": self.kind, "available": False, "version": None, "reason": f"pillow missing: {exc}"}
        return {
            "kind": self.kind,
            "available": True,
            "version": f"pillow-{PIL.__version__}",
            "reason": None,
            "engine": "cpu",
        }

    def run(self, ctx: TaskContext) -> TaskResult:
        try:
            from PIL import Image, ImageOps
        except Exception as exc:  # noqa: BLE001
            raise WorkloadError(f"pillow missing: {exc}", retryable=False) from exc

        if not ctx.inputs:
            raise WorkloadError("no input file for thumbnail task", retryable=False)
        src = ctx.inputs[0]
        size = int(ctx.params.get("size", 256))
        fmt = str(ctx.params.get("fmt", "webp"))
        quality = int(ctx.params.get("quality", 80))

        with Timer() as t:
            try:
                with Image.open(src) as im:
                    im = ImageOps.exif_transpose(im)
                    orig = im.size
                    if im.mode not in ("RGB", "L"):
                        im = im.convert("RGB")
                    im.thumbnail((size, size), Image.LANCZOS)
                    out = ctx.outdir / f"{src.stem}.{fmt}"
                    save_kwargs = {"quality": quality} if fmt in ("webp", "jpeg") else {}
                    if fmt == "jpeg" and im.mode != "RGB":
                        im = im.convert("RGB")
                    im.save(out, fmt.upper(), **save_kwargs)
                    new = im.size
            except Exception as exc:  # noqa: BLE001
                raise WorkloadError(f"image processing failed: {exc}", retryable=False) from exc

        digest = hashlib.sha256(out.read_bytes()).hexdigest()
        return TaskResult(
            files=[out],
            duration_s=t.elapsed,
            meta={
                "kind": self.kind,
                "source": ctx.input_names[0] if ctx.input_names else src.name,
                "src_bytes": src.stat().st_size,
                "out_bytes": out.stat().st_size,
                "src_size": list(orig),
                "out_size": list(new),
                "format": fmt,
                "quality": quality,
                "output_sha256": digest,
            },
        )
