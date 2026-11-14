"""Fixed inputs and strict per-Worker backend validation for infer experiments."""
from __future__ import annotations

import hashlib
import json
import math
import random
import statistics
import zipfile
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MODEL_SHA = "b1a053916c9d667d05a53bfb43764a0aabb11dee20795af75b8ce408760685d3"
PARAMS = {"model": "yolo11n", "imgsz": 640, "conf": 0.25, "max_det": 100}
TOLERANCE = {"confidence_abs": 0.002, "box_pixels_abs": 0.3}
SIZES = (8, 32, 110)
PROVIDERS = {"cuda": "CUDAExecutionProvider", "cpu": "CPUExecutionProvider"}


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_inputs(root):
    splits = read_json(REPO / "benchmarks/datasets/splits.json")["groups"]["infer"]
    manifest = {r["path"]: r for r in read_json(REPO / "benchmarks/datasets/image-manifest.json")}
    groups = {}
    for name in ("smoke", "warmup", "benchmark"):
        files = [(root / p).resolve() for p in splits[name]["source_files"]]
        if len(files) != splits[name]["count"] or len(set(files)) != len(files):
            raise ValueError("Invalid fixed split")
        for relative, path in zip(splits[name]["source_files"], files, strict=True):
            if path.stat().st_size > 500_000 or digest(path) != manifest[relative]["sha256"]:
                raise ValueError(f"Fixed input hash/budget failed: {path.name}")
        groups[name] = files
    if sum(map(len, groups.values())) != len(set().union(*map(set, groups.values()))):
        raise ValueError("Input groups overlap")
    random.Random(20261114).shuffle(groups["benchmark"])
    return groups


def select_workers(workers, tags):
    eligible = [w for w in workers if w["state"] == "online" and any(
        c["kind"] == "infer" and c["available"] for c in w["workloads"])]
    selected = []
    for tag in tags:
        matches = [w for w in workers if tag in w["tags"]]
        if len(matches) != 1:
            raise ValueError(f"Tag {tag!r} must belong to exactly one registered Worker; found {len(matches)}")
        worker = matches[0]
        if worker["state"] != "online":
            raise ValueError(f"Tag {tag!r}: Worker is {worker['state']}, not online")
        if worker not in eligible:
            support = next((c for c in worker["workloads"] if c["kind"] == "infer"), {})
            raise ValueError(f"Tag {tag!r}: infer unavailable: {support.get('reason') or 'infer capability not registered'}")
        selected.append(matches[0])
    ids = {w["worker_id"] for w in selected}
    if len(ids) != 2 or ids != {w["worker_id"] for w in eligible}:
        raise ValueError("Pool must contain exactly the two selected online infer Workers")
    return selected


def config(worker):
    return {k: worker[k] for k in ("worker_id", "name", "os", "cpu_count", "slots", "tags", "workloads")}


def compatible_workers(workers):
    """Prefer reported CUDA, then CPU; model compatibility is required by the live scheduler."""
    compatible = []
    for worker in workers:
        support = next((c for c in worker["workloads"] if c["kind"] == "infer"), {})
        if worker["state"] == "online" and support.get("available") and any(
            m.get("name") == PARAMS["model"] and m.get("sha256") == MODEL_SHA for m in support.get("models", [])
        ):
            compatible.append(worker)
    return sorted(compatible, key=lambda w: (
        next(c for c in w["workloads"] if c["kind"] == "infer").get("engine") != "cuda", w["worker_id"]
    ))


def observed_providers(archive_path, allowed_ids):
    """Discovery is only used for new smoke jobs; strict archive validation follows."""
    seen = {}
    with zipfile.ZipFile(archive_path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        for entry in manifest["entries"]:
            wid = entry["worker_id"]
            if wid not in allowed_ids or len(entry["output_files"]) != 1:
                raise ValueError("Unexpected Worker or output count during backend discovery")
            payload = json.loads(archive.read(f"outputs/{entry['idx']:06d}_{entry['output_files'][0]}"))
            provider = payload.get("provider")
            if provider not in PROVIDERS.values():
                raise ValueError("Unsupported/missing provider during backend discovery")
            if wid in seen and seen[wid] != provider:
                raise ValueError("Worker changed backend within the smoke job")
            seen[wid] = provider
    if seen.keys() != allowed_ids:
        raise ValueError("Smoke job did not return results for every selected Worker")
    return seen


def job_can_use(job, workers):
    """Conservative routing check; uncertainty keeps a competing job in scope."""
    selected = job.get("worker_ids")
    for worker in workers:
        if selected is not None and worker["worker_id"] not in selected:
            continue
        if job.get("required_tag") and job["required_tag"] not in worker["tags"]:
            continue
        support = next((c for c in worker["workloads"] if c["kind"] == job.get("workload")), {})
        if not support.get("available"):
            continue
        required = job.get("required_model_sha")
        if required and not any(m.get("sha256") == required for m in support.get("models", [])):
            continue
        return True
    return False


def model_evidence(workers, expected_providers=None):
    """Check model/engine metadata when exposed, retaining missing evidence honestly."""
    result = {}
    for worker in workers:
        support = next(c for c in worker["workloads"] if c["kind"] == "infer")
        engine = support.get("engine")
        expected = expected_providers[worker["worker_id"]] if expected_providers is not None else PROVIDERS["cuda"]
        if expected not in PROVIDERS.values():
            raise ValueError("Expected provider must be CUDAExecutionProvider or CPUExecutionProvider")
        if expected == PROVIDERS["cuda"] and engine and engine != "cuda":
            raise ValueError(f"{worker['name']} reports infer engine {engine!r}, not cuda")
        models = [m for m in support.get("models", []) if m["name"] == PARAMS["model"]]
        if support.get("models") and len(models) != 1:
            raise ValueError(f"{worker['name']} does not report exactly one pinned model")
        sha = models[0].get("sha256") if models else None
        if sha and sha != MODEL_SHA:
            raise ValueError(f"{worker['name']} reports a different model SHA256")
        result[worker["worker_id"]] = {"engine": engine, "reported_model_sha256": sha,
                                      "matches_reference_hash": sha == MODEL_SHA,
                                      "expected_output_provider": expected,
                                      "capability_engine_may_differ_from_session": expected == PROVIDERS["cpu"] and engine == "cuda",
                                      "source": "Worker capability self-report; not independent remote file inspection"}
    return result


def validate_payload(payload, source_name, image_size, require_cuda, expected_provider=None):
    if payload["model"] != PARAMS["model"] or payload["image"] != source_name or payload["size"] != image_size:
        raise ValueError("Output image identity, model or dimensions mismatch")
    expected_provider = expected_provider or (PROVIDERS["cuda"] if require_cuda else None)
    if expected_provider is not None and payload.get("provider") != expected_provider:
        raise ValueError(f"Expected {expected_provider}, received {payload.get('provider')!r} for {source_name}; backend changed")
    detections = payload["detections"]
    if not isinstance(detections, list) or len(detections) > PARAMS["max_det"]:
        raise ValueError("Invalid detection count")
    width, height = image_size
    for det in detections:
        if type(det["class_id"]) is not int or not 0 <= det["class_id"] < 80:
            raise ValueError("Invalid COCO class")
        box, score = det["xyxy"], det["conf"]
        if len(box) != 4 or not all(isinstance(x, (int, float)) and math.isfinite(x) for x in [*box, score]):
            raise ValueError("Non-finite detection")
        if not (0 <= score <= 1 and 0 <= box[0] <= box[2] <= width and 0 <= box[1] <= box[3] <= height):
            raise ValueError("Detection confidence/coordinates outside valid range")


def detections_match(actual, reference):
    """Bipartite matching avoids ordering and ambiguous greedy-match failures."""
    if len(actual) != len(reference):
        return False
    edges = [[j for j, b in enumerate(reference) if a["class_id"] == b["class_id"]
              and abs(a["conf"] - b["conf"]) <= TOLERANCE["confidence_abs"] + 1e-9
              and max(abs(x - y) for x, y in zip(a["xyxy"], b["xyxy"], strict=True))
              <= TOLERANCE["box_pixels_abs"] + 1e-9] for a in actual]
    owners = {}

    def assign(i, seen):
        for j in edges[i]:
            if j in seen:
                continue
            seen.add(j)
            if j not in owners or assign(owners[j], seen):
                owners[j] = i
                return True
        return False

    return all(assign(i, set()) for i in range(len(actual)))


def verify_archive(path, files, job_id, reference, allowed_ids, expected_providers=None):
    expected_providers = expected_providers if expected_providers is not None else {wid: PROVIDERS["cuda"] for wid in allowed_ids}
    if not allowed_ids <= expected_providers.keys() or any(expected_providers[wid] not in PROVIDERS.values() for wid in allowed_ids):
        raise ValueError("Every allowed Worker needs an explicit supported output provider")
    checks = []
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or archive.testzip() is not None:
            raise ValueError("Duplicate ZIP members or invalid CRC")
        manifest = json.loads(archive.read("manifest.json"))
        n = len(files)
        if (manifest["job_id"], manifest["workload"], manifest["state"], manifest["total"],
            manifest["succeeded"], manifest["failed"]) != (job_id, "infer", "succeeded", n, n, 0):
            raise ValueError("Unexpected job identity/status/count")
        if any(manifest["params"].get(k) != v for k, v in PARAMS.items()):
            raise ValueError("Unexpected infer parameters")
        entries = manifest["entries"]
        if len(entries) != n or len({e["task_id"] for e in entries}) != n or {e["idx"] for e in entries} != set(range(n)):
            raise ValueError("Missing or repeated task IDs/indices")
        expected = set()
        for e in sorted(entries, key=lambda r: r["idx"]):
            source = files[e["idx"]]
            if e["name"] != source.name or e.get("error") or e["worker_id"] not in allowed_ids or len(e["output_files"]) != 1:
                raise ValueError("Input identity, output count, task error or Worker placement mismatch")
            member = f"outputs/{e['idx']:06d}_{e['output_files'][0]}"
            expected.add(member)
            raw = archive.read(member)
            if hashlib.sha256(raw).hexdigest() != e["output_sha256"]:
                raise ValueError("Output hash mismatch")
            payload = json.loads(raw)
            ref = reference["images"][source.name]["output"]
            validate_payload(payload, source.name, ref["size"], False, expected_providers[e["worker_id"]])
            matched = detections_match(payload["detections"], ref["detections"])
            checks.append({"idx": e["idx"], "task_id": e["task_id"], "input": source.name,
                           "worker_id": e["worker_id"], "attempt": e["attempt"], "provider": payload["provider"],
                           "detections": len(payload["detections"]), "cpu_reference_match": matched,
                           "output_sha256": e["output_sha256"], "passed": matched})
        if {name for name in names if name.startswith("outputs/") and not name.endswith("/")} != expected:
            raise ValueError("Unexpected or missing output inventory")
    counts = Counter(c["worker_id"] for c in checks if c["passed"])
    return {"job_id": job_id, "images_total": n, "verified_images": sum(counts.values()),
            "valid": all(c["passed"] for c in checks), "per_worker": dict(counts),
            "per_provider": dict(Counter(c["provider"] for c in checks if c["passed"])),
            "returned_per_worker": dict(Counter(c["worker_id"] for c in checks)),
            "numerical_mismatches": sum(not c["passed"] for c in checks),
            "both_workers_contributed": len(counts) == 2,
            "retries_in_final_attempts": sum(c["attempt"] - 1 for c in checks), "checks": checks}


def resource_summary(snapshots, start, end, ids):
    samples = {wid: {} for wid in ids}
    for snap in snapshots:
        for w in snap["workers"]:
            if w["worker_id"] in samples:
                for p in w["util_series"]:
                    if start <= p["ts"] <= end:
                        samples[w["worker_id"]][p["ts"]] = p
    result = {}
    for wid, series in samples.items():
        points = [series[k] for k in sorted(series)]
        row = {"sample_count": len(points), "points": points}
        for field in ("cpu_pct", "gpu_pct"):
            values = [p[field] for p in points if p.get(field) is not None]
            row["mean_" + field] = statistics.fmean(values) if values else None
        result[wid] = row
    return result


def aggregate(rows, record_numerical_mismatches=False):
    if len(rows) != 27:
        raise ValueError("Exactly 27 measured trials are required")
    summary = {}
    for n in SIZES:
        conditions = {}
        for name in ("single-1", "single-2", "pool"):
            group = [r for r in rows if r["condition"] == name and r["size"] == n]
            if len(group) != 3 or {r["repeat"] for r in group} != {1, 2, 3}:
                raise ValueError("All 27 valid measured rounds are required; do not omit failed/missing trials")
            for row in group:
                if record_numerical_mismatches:
                    if not row.get("integrity_passed") or not row.get("configuration_stable"):
                        raise ValueError("Diagnostic comparisons still require integrity and stable configuration")
                    if row.get("numerical_mismatches") != n - row["verified_images"]:
                        raise ValueError("Numerical pass/fail counts do not add up")
                    if row["valid"] != (row["verified_images"] == n):
                        raise ValueError("A numerical mismatch cannot be marked valid")
                elif not row["valid"]:
                    raise ValueError("All 27 valid measured rounds are required; do not omit failed/missing trials")
                if not 0 <= row["verified_images"] <= n or not math.isfinite(row["end_to_end_s"]) or row["end_to_end_s"] <= 0:
                    raise ValueError("Invalid qualified output count or timing")
            rates = [r["verified_images"] / r["end_to_end_s"] for r in group]
            conditions[name] = {"median_images_s": statistics.median(rates), "min_images_s": min(rates),
                                "max_images_s": max(rates), "median_end_to_end_s": statistics.median(r["end_to_end_s"] for r in group)}
            if record_numerical_mismatches:
                conditions[name].update(verified_images=sum(r["verified_images"] for r in group),
                                        outputs_total=3 * n, all_numerical_checks_passed=all(r["valid"] for r in group))
        p1, p2, pool = [conditions[c]["median_images_s"] for c in ("single-1", "single-2", "pool")]
        summary[str(n)] = {"conditions": conditions, "eta": pool / (p1 + p2) if p1 + p2 else None,
                           "speedup": pool / max(p1, p2) if max(p1, p2) else None,
                           "both_workers_in_every_pool_trial": all(r["both_workers_contributed"] for r in rows
                                                                   if r["condition"] == "pool" and r["size"] == n)}
    return summary
