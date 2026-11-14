"""Generate CPU reference outputs only, not a local performance baseline."""
import argparse
import platform
import sys
from pathlib import Path

import numpy as np
import onnxruntime as ort
import PIL
from cef_worker.workloads.infer import _letterbox, _postprocess, _scale_boxes
from common import MODEL_SHA, PARAMS, REPO, TOLERANCE, digest, load_inputs, validate_payload, write_json
from PIL import Image, ImageOps


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-root", type=Path, default=REPO.parent / "test-data")
    parser.add_argument("--model", type=Path, default=REPO / "data/d-acceptance/models/yolo11n.onnx")
    parser.add_argument("--output", type=Path, default=REPO / "data/d-acceptance/infer-reference/reference.json")
    args = parser.parse_args()
    groups = load_inputs(args.dataset_root)
    if digest(args.model) != MODEL_SHA:
        raise ValueError("Model SHA256 does not match the fixed GPU deployment")
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    session = ort.InferenceSession(str(args.model), sess_options=options, providers=["CPUExecutionProvider"])
    result = {"scope": "CPU reference outputs for numerical agreement; not a timing baseline or held-out accuracy evaluation",
              "model_sha256": MODEL_SHA, "params": PARAMS, "tolerance": TOLERANCE,
              "environment": {"python": sys.version, "os": platform.platform(), "ort": ort.__version__,
                              "numpy": np.__version__, "pillow": PIL.__version__, "provider": session.get_providers()},
              "images": {}}
    files = [p for group in groups.values() for p in group]
    for index, path in enumerate(files, 1):
        with Image.open(path) as source:
            rgb = ImageOps.exif_transpose(source).convert("RGB")
            width, height = rgb.size
            canvas, scale, pad = _letterbox(rgb, PARAMS["imgsz"])
            x = np.asarray(canvas, dtype=np.float32) / 255.0
            x = np.transpose(x, (2, 0, 1))[None, ...]
            raw = session.run(None, {session.get_inputs()[0].name: x})[0]
            if not np.isfinite(raw).all():
                raise ValueError("Non-finite CPU model output")
            dets = _scale_boxes(*_postprocess(raw, PARAMS["conf"], PARAMS["max_det"]), scale, pad, width, height)
        output = {"model": PARAMS["model"], "image": path.name, "size": [width, height],
                  "provider": "CPUExecutionProvider", "detections": dets}
        validate_payload(output, path.name, [width, height], False)
        result["images"][path.name] = {"input_sha256": digest(path), "output": output}
        if index % 10 == 0 or index == len(files):
            print(f"Reference: {index}/{len(files)}", flush=True)
    write_json(args.output, result)
    print("Reference saved:", args.output)


if __name__ == "__main__":
    main()
