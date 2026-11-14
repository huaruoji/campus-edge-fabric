"""Controlled service benchmark: 100 BSDS images, two singles and a pool, three repeats.

Default mode validates inputs and prints the plan without network access.
--run requires two unique Worker tags; it never changes Worker configuration.
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import math
import os
import statistics
import subprocess
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path

from run_thumbnail_smoke import verify_result, write_json

REPO = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
PARAMS = {"size": 256, "fmt": "png"}
TERMINAL = {"succeeded", "partial", "failed", "canceled"}


def load_inputs(dataset_root, split_name):
    metadata = REPO / "benchmarks/datasets"
    split = json.loads((metadata / "splits.json").read_text(encoding="utf-8"))["groups"]["thumbnail"][split_name]
    manifest = {r["path"]: r for r in json.loads((metadata / "image-manifest.json").read_text(encoding="utf-8"))}
    files = [(dataset_root / name).resolve() for name in split["source_files"]]
    for relative, path in zip(split["source_files"], files, strict=True):
        data = path.read_bytes()
        if len(data) > 500_000 or hashlib.sha256(data).hexdigest() != manifest[relative]["sha256"]:
            raise ValueError(f"Input exceeds 500 KB or differs from fixed dataset: {path}")
    if len(files) != split["count"] or len(set(files)) != len(files):
        raise ValueError("Invalid split size or repeated source path")
    return files


def select_workers(workers, tags):
    selected = []
    for tag in tags:
        matches = [w for w in workers if tag in w["tags"]]
        if len(matches) != 1:
            raise ValueError(f"Tag {tag!r} must belong to exactly one registered Worker; found {len(matches)}")
        worker = matches[0]
        if worker["state"] != "online" or not any(c["kind"] == "thumbnail" and c["available"] for c in worker["workloads"]):
            raise ValueError(f"Worker for tag {tag!r} is not an online thumbnail Worker")
        selected.append(worker)
    wanted = {w["worker_id"] for w in selected}
    if len(wanted) != 2:
        raise ValueError("The two tags must identify two different Worker IDs")
    eligible = {w["worker_id"] for w in workers if w["state"] == "online" and any(
        c["kind"] == "thumbnail" and c["available"] for c in w["workloads"])}
    if eligible != wanted:
        raise ValueError("The unrestricted pool must contain exactly the two measured thumbnail Workers")
    return selected


def resource_summary(snapshots, start, end, worker_ids):
    series = {wid: {} for wid in worker_ids}
    for snapshot in snapshots:
        for worker in snapshot["workers"]:
            if worker["worker_id"] in series:
                for point in worker["util_series"]:
                    if start <= point["ts"] <= end:
                        series[worker["worker_id"]][point["ts"]] = point
    result = {}
    for wid, samples in series.items():
        points = [samples[ts] for ts in sorted(samples)]
        def mean(field, current_points=points):
            values = [p[field] for p in current_points if p.get(field) is not None]
            return statistics.fmean(values) if values else None
        result[wid] = {
            "sample_count": len(points), "mean_cpu_pct": mean("cpu_pct"), "mean_gpu_pct": mean("gpu_pct"),
            "first_sample_ts": points[0]["ts"] if points else None,
            "last_sample_ts": points[-1]["ts"] if points else None,
            "points": points,
        }
    return result


def aggregate(rounds):
    groups = {name: [r for r in rounds if r["condition"] == name] for name in ("single-1", "single-2", "pool")}
    if any(len(rows) != 3 or any(not r["valid"] for r in rows) for rows in groups.values()):
        raise ValueError("Need three valid measured repetitions per condition; failed rounds must not be dropped")
    per_group = {}
    for name, rows in groups.items():
        rates = [r["verified_throughput_images_s"] for r in rows]
        per_group[name] = {"median_images_s": statistics.median(rates), "min_images_s": min(rates),
                           "max_images_s": max(rates), "rounds": len(rows)}
    p1, p2, pool = [per_group[n]["median_images_s"] for n in ("single-1", "single-2", "pool")]
    return {"groups": per_group, "eta": pool / (p1 + p2), "speedup": pool / max(p1, p2),
            "metric": "verified unique outputs / client submit-to-result-download wall time",
            "note": "Finite-batch end-to-end service throughput, not bare-device peak throughput or proof of saturation."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--tags", nargs=2, metavar=("DEVICE1_TAG", "DEVICE2_TAG"))
    parser.add_argument("--server", default=os.environ.get("CEF_SERVER") or "https://cef.huaruoji.com")
    parser.add_argument("--dataset-root", type=Path, default=REPO / "data/test-datasets")
    parser.add_argument("--output-root", type=Path, default=REPO / "data/d-acceptance")
    parser.add_argument("--timeout", type=float, default=600, help="job completion timeout in seconds")
    args = parser.parse_args()
    if not math.isfinite(args.timeout) or not 1 <= args.timeout <= 86400:
        parser.error("--timeout must be between 1 and 86400 seconds")
    measured = load_inputs(args.dataset_root, "benchmark")
    warmup = load_inputs(args.dataset_root, "warmup")
    if set(measured) & set(warmup):
        raise ValueError("Warmup and measurement inputs overlap")
    print(f"Plan: {len(measured)} unique images per measured round; PNG, longest edge 256.", flush=True)
    print("3 conditions x 3 repetitions = 9 measured rounds (900 outputs).", flush=True)
    print(f"Each measured round follows a separate {len(warmup)}-image warmup; warmups are excluded from metrics.", flush=True)
    if not args.run:
        print("Local hashes and budgets passed. No network requests. Use --run --tags TAG1 TAG2 after B configures tags.")
        return 0
    if not args.tags:
        parser.error("--run requires --tags TAG1 TAG2")

    import PIL
    from cef_protocol import CefClient

    token = os.environ.get("CEF_TOKEN") or getpass.getpass("CEF Token (hidden; not saved): ")
    if not token.strip():
        raise ValueError("CEF_TOKEN is required")
    out = args.output_root.resolve() / ("benchmark-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    out.mkdir(parents=True)
    print(f"Evidence directory: {out}", flush=True)
    with CefClient(args.server, token.strip(), timeout=60) as client:
        def workers():
            return [w.model_dump() for w in client.workers().workers]

        initial = workers()
        write_json(out / "workers-initial.json", initial)
        selected = select_workers(initial, args.tags)
        ids = [w["worker_id"] for w in selected]
        fingerprints = {w["worker_id"]: {k: w[k] for k in ("name", "hostname", "os", "cpu_count", "slots", "tags", "workloads")}
                        for w in selected}
        health = client.health().model_dump()
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip()
        write_json(out / "experiment.json", {
            "client_commit": commit, "python": sys.version, "pillow": PIL.__version__, "server": args.server,
            "script_sha256": {name: hashlib.sha256((HERE / name).read_bytes()).hexdigest()
                              for name in ("run_thumbnail_benchmark.py", "run_thumbnail_smoke.py")},
            "server_health": health, "server_commit": "not exposed by API; obtain from A separately",
            "params": PARAMS, "tags": args.tags, "worker_ids": ids, "worker_configs": fingerprints,
            "measurement_count": len(measured), "warmup_count": len(warmup), "repetitions": 3,
            "metric": "validated outputs / client submit-to-result-download wall time",
            "resource_window": "server job created_at to finished_at; heartbeat samples, not process-only utilization",
            "order": [["single-1", "single-2", "pool"], ["single-2", "pool", "single-1"], ["pool", "single-1", "single-2"]],
        })
        write_json(out / "input-hashes.json", {name: [{"file": p.relative_to(args.dataset_root.resolve()).as_posix(),
                                                       "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                                                      for p in files] for name, files in (("warmup", warmup), ("measurement", measured))})

        def run_round(condition, repeat, files, warming):
            label = f"r{repeat}-{condition}-{'warmup' if warming else 'measured'}"
            folder = out / label
            folder.mkdir()
            before = workers()
            write_json(folder / "workers-before.json", before)
            current = select_workers(before, args.tags)
            for worker in current:
                if fingerprints.get(worker["worker_id"]) != {k: worker[k] for k in fingerprints[ids[0]]}:
                    raise ValueError("Worker identity/configuration changed during experiment")
            for state in ("queued", "running"):
                if client.list_jobs(limit=1, state=state).jobs:
                    raise ValueError("Another job is queued/running; finish it before the controlled benchmark")
            index = int(condition[-1]) - 1 if condition != "pool" else None
            required_tag = args.tags[index] if index is not None else None
            expected_ids = {ids[index]} if index is not None else set(ids)
            snapshots = []
            sample_errors = []
            t0 = time.perf_counter()
            created = client.submit_job(files, "thumbnail", PARAMS, name=f"D-bench-{label}", required_tag=required_tag)
            t_submitted = time.perf_counter()
            write_json(folder / "created.json", created.model_dump())
            print(f"{label}: {created.job_id}", flush=True)
            next_sample = 0.0

            def sample():
                try:
                    snapshots.append(client.stats(window=60).model_dump())
                except Exception as error:
                    sample_errors.append({"type": type(error).__name__, "elapsed_s": time.perf_counter() - t0})

            while True:
                job = client.get_job(created.job_id)
                if job.state in TERMINAL:
                    break
                now = time.perf_counter()
                if now - t0 > args.timeout:
                    write_json(folder / "last-job.json", job.model_dump())
                    raise TimeoutError("Job exceeded timeout; inspect saved Job ID before retrying. It was not canceled automatically.")
                if now >= next_sample:
                    sample()
                    next_sample = time.perf_counter() + 5
                time.sleep(0.5)
            t_terminal = time.perf_counter()
            write_json(folder / "job-detail.json", job.model_dump())
            archive = client.download_result(created.job_id, folder / "result.zip")
            t_downloaded = time.perf_counter()
            sample()
            write_json(folder / "resource-snapshots.json", snapshots)
            write_json(folder / "resource-sampling-errors.json", sample_errors)
            with zipfile.ZipFile(archive) as z:
                result_manifest = json.loads(z.read("manifest.json"))
            write_json(folder / "manifest.json", result_manifest)
            if job.finished_at is None:
                raise ValueError("Terminal job has no finished_at timestamp; cannot define a measurement window")
            resources = resource_summary(snapshots, job.created_at, job.finished_at, set(ids))
            write_json(folder / "resource-window.json", resources)
            verified = verify_result(archive, files, created.job_id)
            verified["limits"][-1] = "This is the output validation for this round; measured timing is recorded separately in round.json."
            write_json(folder / "verification.json", verified)
            actual_ids = {row["worker_id"] for row in verified["checks"]}
            placement_ok = actual_ids == expected_ids
            valid = verified["all_image_checks_passed"] and placement_ok
            record = {
                "condition": condition, "repeat": repeat, "warmup": warming, "job_id": created.job_id,
                "valid": valid, "placement_matches_condition": placement_ok, "verified_images": verified["images_passed"],
                "submit_request_s": t_submitted - t0, "submit_to_terminal_s": t_terminal - t0,
                "download_s": t_downloaded - t_terminal, "end_to_end_s": t_downloaded - t0,
                "server_job_s": job.finished_at - job.created_at,
                "verified_throughput_images_s": verified["images_passed"] / (t_downloaded - t0),
                "retries_in_final_attempts": sum(e["attempt"] - 1 for e in result_manifest["entries"]),
                "per_worker": verified["per_worker"], "resources": resources, "sampling_errors": sample_errors,
            }
            write_json(folder / "round.json", record)
            if not valid:
                raise ValueError(f"{label} failed correctness or participation checks; experiment stopped with evidence retained")
            print(f"  {verified['images_passed']}/{len(files)} verified; {record['end_to_end_s']:.3f}s including upload/download", flush=True)
            return record

        rounds = []
        order = ["single-1", "single-2", "pool"]
        for repeat in range(1, 4):
            for condition in order:
                run_round(condition, repeat, warmup, True)
                rounds.append(run_round(condition, repeat, measured, False))
                write_json(out / "measured-rounds.json", rounds)
            order = order[1:] + order[:1]
        summary = aggregate(rounds)
        summary["resource_limitations"] = ["Heartbeat samples describe whole-device load, including other applications.",
                                            "Missing GPU samples remain null; no GPU utilization is inferred.",
                                            "The fixed workload may not saturate devices; inspect timings before claiming peak throughput."]
        write_json(out / "summary.json", summary)
        lines = ["# D thumbnail 单机与算力池对照", "", "同一批 100 张输入；256 PNG；各条件 3 次。预热不计入。", "",
                 "吞吐计时包含客户端提交、等待、结果 ZIP 下载；离线结果校验不计入。", "",
                 "| 条件 | 中位 images/s | 最小 | 最大 |", "| --- | ---: | ---: | ---: |"]
        for name, data in summary["groups"].items():
            lines.append(f"| {name} | {data['median_images_s']:.4f} | {data['min_images_s']:.4f} | {data['max_images_s']:.4f} |")
        lines += ["", f"η = {summary['eta']:.2%}；相对最快单机 S = {summary['speedup']:.3f}。", "",
                  "这是固定批次的端到端服务吞吐，包含同一路径上的网络开销。不能当作设备理论性能或 GPU 利用率。", "",
                  "资源样本已按服务端本轮作业的创建/结束时间过滤；查看各轮 resource-window.json 的样本数与时间覆盖。", "",
                  "最终报告仍需补 B 确认的物理设备配置、A 提供的服务端提交版本及根据数据分析的瓶颈。"]
        (out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        print(f"Report: {out / 'report.md'}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        print("Evidence already written is preserved. Check saved Job IDs before submitting again.", file=sys.stderr)
        raise SystemExit(1) from error
