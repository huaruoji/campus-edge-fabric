#!/usr/bin/env python3
"""Measure the numbers the competition actually scores.

    P_i   : throughput of device i alone (same workload, same input distribution)
    P_pool: throughput of the whole pool under the same load
    eta   : P_pool / sum(P_i)          -- how much of the sum of the parts we keep
    S     : P_pool / max(P_i)          -- speedup over the fastest single device

Pinning is done with the scheduler's own tag mechanism ("required_tag"), so a
device is measured *alone* without stopping the others: they stay online, they
simply are not eligible for that job. That keeps the comparison honest and the
window comparable.

Usage
    python benchmarks/bench.py --server http://127.0.0.1:8080 --token T \
        --workload thumbnail --tasks 64 --inputs ./bench-images --tag-prefix bench

Each worker must join with a unique tag, e.g. --tags bench-pc1.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

from cef_protocol import CefClient
from cef_protocol.models import JobDetail


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--server", required=True)
    p.add_argument("--token", default="")
    p.add_argument("--workload", required=True, choices=["thumbnail", "infer", "blender"])
    p.add_argument("--params", default="{}", help="JSON params for the workload")
    p.add_argument("--tasks", type=int, default=32, help="tasks per measurement round")
    p.add_argument("--inputs", required=True, help="directory of input files (cycled to reach --tasks)")
    p.add_argument("--tag-prefix", default="bench", help="worker tags starting with this identify measurement devices")
    p.add_argument("--out", default="benchmarks/results")
    p.add_argument("--poll", type=float, default=0.5)
    return p.parse_args()


def pick_inputs(directory: Path, count: int) -> list[Path]:
    files = sorted(p for p in directory.iterdir() if p.is_file())
    if not files:
        sys.exit(f"no input files in {directory}")
    return [files[i % len(files)] for i in range(count)]


def run_round(client: CefClient, args: argparse.Namespace, files: list[Path], tag: str | None) -> dict[str, Any]:
    util: list[dict[str, Any]] = []
    t0 = time.perf_counter()
    created = client.submit_job(
        files,
        args.workload,
        json.loads(args.params),
        name=f"bench-{args.workload}-{tag or 'pool'}",
        required_tag=tag,
    )
    job: JobDetail | None = None
    last_stats = 0.0
    while True:
        job = client.get_job(created.job_id)
        now = time.perf_counter()
        if now - last_stats > 2.0:
            last_stats = now
            try:
                s = client.stats(window=3600)
                util.append({w.worker_id: (w.mean_cpu_pct, w.mean_gpu_pct) for w in s.workers})
            except Exception:  # noqa: BLE001 - util sampling must never break a run
                pass
        if job.state in ("succeeded", "partial", "failed", "canceled"):
            break
        time.sleep(args.poll)
    elapsed = time.perf_counter() - t0
    assert job is not None
    ok = job.succeeded
    return {
        "job_id": job.job_id,
        "tag": tag,
        "tasks": job.total,
        "succeeded": ok,
        "failed": job.failed,
        "elapsed_s": round(elapsed, 3),
        "throughput": round(ok / elapsed, 3) if elapsed > 0 else 0.0,
        "mean_duration_s": round(
            statistics.fmean([t.duration_s for t in job.tasks if t.duration_s is not None] or [0.0]), 4
        ),
        "per_worker": [w.model_dump() for w in job.per_worker],
        "util_samples": util,
    }


def main() -> int:
    args = parse_args()
    client = CefClient(args.server, args.token)
    files = pick_inputs(Path(args.inputs), args.tasks)

    workers = client.workers().workers
    tagged = [(w.name, tag) for w in workers for tag in w.tags if tag.startswith(args.tag_prefix)]
    if not tagged:
        print(f"[warn] no worker has a tag starting with '{args.tag_prefix}' — single-device baselines will be skipped")
    print(f"devices in pool: {[w.name for w in workers]}")

    results: dict[str, Any] = {"workload": args.workload, "params": json.loads(args.params), "tasks": args.tasks, "singles": {}}
    for name, tag in tagged:
        print(f"\n== single device: {name} (tag {tag}) ==", flush=True)
        r = run_round(client, args, files, tag)
        results["singles"][name] = r
        print(f"   {r['succeeded']}/{r['tasks']} in {r['elapsed_s']}s -> P_{name} = {r['throughput']} tasks/s")

    print("\n== pool ==", flush=True)
    pool = run_round(client, args, files, None)
    results["pool"] = pool
    print(f"   {pool['succeeded']}/{pool['tasks']} in {pool['elapsed_s']}s -> P_pool = {pool['throughput']} tasks/s")

    p_i = {name: r["throughput"] for name, r in results["singles"].items() if r["succeeded"] > 0}
    summary = {
        "P_pool": pool["throughput"],
        "P_i": p_i,
        "sum_P_i": round(sum(p_i.values()), 3),
        "eta": round(pool["throughput"] / sum(p_i.values()), 4) if p_i and sum(p_i.values()) else None,
        "S": round(pool["throughput"] / max(p_i.values()), 4) if p_i else None,
        "bottleneck": None,
    }
    results["summary"] = summary

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = out_dir / f"bench_{args.workload}_{stamp}.json"
    path.write_text(json.dumps(results, ensure_ascii=False, indent=2))

    print("\n--- summary ---")
    for name, val in p_i.items():
        print(f"  P_{name:<16} {val:>8.3f} tasks/s")
    print(f"  {'P_pool':<18} {summary['P_pool']:>8.3f} tasks/s")
    print(f"  {'sum(P_i)':<18} {summary['sum_P_i']:>8.3f} tasks/s")
    print(f"  {'eta (pool/sum)':<18} {summary['eta']:.1%}" if summary["eta"] is not None else "  eta n/a")
    print(f"  {'S (pool/max_i)':<18} {summary['S']:.2f}x" if summary["S"] is not None else "  S n/a")
    print(f"\nwritten: {path}")
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
