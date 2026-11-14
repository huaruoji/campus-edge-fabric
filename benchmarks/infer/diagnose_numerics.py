"""Offline CPU reproduction of one saved job, optionally using another Pillow install.

Writes separate diagnostic evidence; never changes the reference or experiment verdict.
"""
import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path

from common import MODEL_SHA, PARAMS, REPO, detections_match, digest, load_inputs, read_json, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("round", type=Path)
    parser.add_argument("--pillow-path", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.pillow_path:
        sys.path.insert(0, str(args.pillow_path.resolve()))
    import PIL
    from PIL import Image, ImageOps, features
    if args.pillow_path:
        sys.path.pop(0)
    import numpy as np
    import onnxruntime as ort
    from cef_worker.workloads.infer import _letterbox, _postprocess, _scale_boxes

    model = REPO / "data/d-acceptance/models/yolo11n.onnx"
    if digest(model) != MODEL_SHA:
        raise ValueError("Wrong fixed model")
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    session = ort.InferenceSession(str(model), sess_options=options, providers=["CPUExecutionProvider"])
    groups = load_inputs(REPO.parent / "test-data")
    paths = {p.name: p for files in groups.values() for p in files}
    old_ref = read_json(REPO / "data/d-acceptance/infer-reference/reference.json")
    report = {"environment": {"pillow": PIL.__version__, "jpeg": features.version_codec("jpg"),
                              "libjpeg_turbo": features.version_feature("libjpeg_turbo"), "onnxruntime": ort.__version__},
              "round": args.round.name, "images": []}
    with zipfile.ZipFile(args.round / "result.zip") as archive:
        manifest = json.loads(archive.read("manifest.json"))
        for entry in manifest["entries"]:
            source = paths[entry["name"]]
            with Image.open(source) as im:
                rgb = ImageOps.exif_transpose(im).convert("RGB")
                canvas, scale, pad = _letterbox(rgb, PARAMS["imgsz"])
            pixels = np.asarray(canvas, dtype=np.float32) / 255.0
            pixels = np.transpose(pixels, (2, 0, 1))[None, ...]
            raw = session.run(None, {session.get_inputs()[0].name: pixels})[0]
            if not np.isfinite(raw).all():
                raise ValueError("Non-finite CPU result")
            dets = _scale_boxes(*_postprocess(raw, PARAMS["conf"], PARAMS["max_det"]), scale, pad, *rgb.size)
            member = f"outputs/{entry['idx']:06d}_{entry['output_files'][0]}"
            remote = json.loads(archive.read(member))
            baseline = old_ref["images"][source.name]["output"]["detections"]
            report["images"].append({"name": source.name, "input_sha256": digest(source),
                                     "rgb_sha256": hashlib.sha256(rgb.tobytes()).hexdigest(),
                                     "canvas_sha256": hashlib.sha256(canvas.tobytes()).hexdigest(),
                                     "cpu_detections": dets, "remote_provider": remote["provider"],
                                     "remote_matches_new_cpu": detections_match(remote["detections"], dets),
                                     "remote_matches_old_cpu": detections_match(remote["detections"], baseline),
                                     "new_cpu_matches_old_cpu": detections_match(dets, baseline)})
    report["counts"] = {k: sum(row[k] for row in report["images"]) for k in (
        "remote_matches_new_cpu", "remote_matches_old_cpu", "new_cpu_matches_old_cpu"
    )}
    report["total"] = len(report["images"])
    write_json(args.output, report)
    print(json.dumps({k: v for k, v in report.items() if k != "images"}, indent=2))


if __name__ == "__main__":
    main()
