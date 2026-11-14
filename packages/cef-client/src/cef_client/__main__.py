"""The `cef` CLI — the third client of the same service (web / HTTP / CLI).

    cef --server http://host:8080 --token T workers
    cef --server ... job submit --workload thumbnail --params '{"size":512}' *.jpg
    cef --server ... job watch <job_id>
    cef --server ... job result <job_id> -o out.zip

Server/token default to CEF_SERVER / CEF_TOKEN so on the demo machine you type
nothing after the first export.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
from pathlib import Path

from cef_protocol import CefClient
from cef_protocol.states import JobState


def _expand(patterns: list[str]) -> list[Path]:
    """Expand shell globs ourselves so agents can pass patterns without a shell."""
    out: list[Path] = []
    for pattern in patterns:
        if any(ch in pattern for ch in "*?["):
            out.extend(Path(p) for p in sorted(glob.glob(pattern)))
        else:
            out.append(Path(pattern))
    return [p for p in out if p.is_file()]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cef", description="Campus Edge Fabric client")
    p.add_argument("--server", default=os.environ.get("CEF_SERVER", "http://127.0.0.1:8080"))
    p.add_argument("--token", default=os.environ.get("CEF_TOKEN", ""))
    p.add_argument("--json", action="store_true", help="machine-readable output")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("doctor", help="health check + registered workloads")
    sub.add_parser("mcp", help="run as an MCP server over stdio (Claude Code / Codex / any MCP client)")
    sub.add_parser("workers", help="list devices")
    st = sub.add_parser("stats", help="pool statistics")
    st.add_argument("--window", type=int, default=300)

    stg = sub.add_parser("storage", help="blob store usage, quota and coordinator disk")
    stg.add_argument("--json", action="store_true", default=False, help="raw JSON")

    sub.add_parser("workloads", help="list workload kinds")

    job = sub.add_parser("job", help="job operations")
    jsub = job.add_subparsers(dest="jcmd", required=True)

    submit = jsub.add_parser("submit", help="submit a new job (sbatch equivalent)")
    submit.add_argument("--spec", default=None, help="job.json with workload/params/files (declarative, versionable)")
    submit.add_argument("--workload", default=None)
    submit.add_argument("--params", default=None, help="JSON params, e.g. '{\"size\":256}'")
    submit.add_argument("--name", default=None)
    submit.add_argument("--priority", type=int, default=0)
    submit.add_argument("--required-tag", default=None, help="pin to devices with this tag")
    submit.add_argument("files", nargs="*", help="input files or globs")

    ls = jsub.add_parser("ls", help="list jobs")
    ls.add_argument("--limit", type=int, default=20)
    ls.add_argument("--state", default=None)

    show = jsub.add_parser("show", help="job detail")
    show.add_argument("job_id")

    watch = jsub.add_parser("watch", help="poll until the job finishes")
    watch.add_argument("job_id")
    watch.add_argument("--interval", type=float, default=1.0)

    result = jsub.add_parser("result", help="download the result zip")
    result.add_argument("job_id")
    result.add_argument("-o", "--output", default=None)

    cancel = jsub.add_parser("cancel", help="cancel a job")
    cancel.add_argument("job_id")
    return p


def _fmt_ts(ts: float | None) -> str:
    if not ts:
        return "-"
    return time.strftime("%H:%M:%S", time.localtime(ts))


def cmd_doctor(c: CefClient, args: argparse.Namespace) -> int:
    health = c.health()
    print(f"coordinator : {c.base_url}")
    print(f"version     : {health.version}   uptime {health.uptime_s:.0f}s   devices online {health.workers_online}")
    for w in c.workloads().workloads:
        where = ", ".join(w.available_on) or "— nobody can run it yet"
        print(f"  {w.kind:<10} unit={w.unit:<7} devices: {where}")
    return 0


def cmd_workers(c: CefClient, args: argparse.Namespace) -> int:
    data = c.workers()
    if args.json:
        print(json.dumps([w.model_dump() for w in data.workers], indent=2, ensure_ascii=False))
        return 0
    if not data.workers:
        print("no device has ever joined")
        return 0
    print(f"{'name':<18}{'state':<10}{'cpu%':>6}{'gpu%':>6}{'slots':>6}{'done':>7}{'mean s':>9}  workloads")
    for w in data.workers:
        cpu = f"{w.cpu_pct:.0f}" if w.cpu_pct is not None else "-"
        gpu = f"{w.gpu_pct:.0f}" if w.gpu_pct is not None else "-"
        mean = f"{w.mean_duration_s:.3f}" if w.mean_duration_s else "-"
        wl = ",".join(x.kind for x in w.workloads if x.available)
        print(f"{w.name[:17]:<18}{w.state:<10}{cpu:>6}{gpu:>6}{w.slots:>6}{w.completed_total:>7}{mean:>9}  {wl}")
    return 0


def cmd_storage(c: CefClient, args: argparse.Namespace) -> int:
    body = c.request("GET", "/api/v1/storage").json()
    if getattr(args, "json", False):
        print(json.dumps(body, indent=2, ensure_ascii=False))
        return 0
    print(f"blob 存储 : {body['blob_bytes'] / 1e9:.2f} GB / {body['quota_gb']:.1f} GB ({body['used_pct_of_quota']}%)")
    print(f"文件数    : {body['blob_count']}   保留 {body['retention_days']} 天   单文件上限 {body['max_upload_mb']:.0f} MB")
    print(f"coordinator 磁盘: 可用 {body['disk_free_gb']} GB / 共 {body['disk_total_gb']} GB")
    return 0


def cmd_stats(c: CefClient, args: argparse.Namespace) -> int:
    s = c.stats(args.window)
    print(f"window {s.window_s}s   devices online {s.workers_online}   jobs {s.jobs_running} running")
    print(f"tasks/min {s.tasks_per_min}   succeeded {s.tasks_succeeded}   failed {s.tasks_failed}   queued {s.tasks_queued}   retries {s.retries}")
    if s.p50_duration_s:
        print(f"task duration p50 {s.p50_duration_s}s   p95 {s.p95_duration_s}s")
    for w in s.workers:
        cpu = f"{w.mean_cpu_pct:.0f}%" if w.mean_cpu_pct is not None else "-"
        gpu = f"{w.mean_gpu_pct:.0f}%" if w.mean_gpu_pct is not None else "-"
        print(f"  {w.name or w.worker_id:<18} cpu {cpu:>5}  gpu {gpu:>5}")
    if s.bottleneck:
        print(f"bottleneck hint: {s.bottleneck}")
    return 0


def cmd_workloads(c: CefClient, args: argparse.Namespace) -> int:
    for w in c.workloads().workloads:
        print(f"{w.kind:<10} unit={w.unit:<7} available on: {', '.join(w.available_on) or '-'}")
        print("   params:", json.dumps(w.params_schema.get("properties", {}), ensure_ascii=False))
    return 0


def cmd_job(c: CefClient, args: argparse.Namespace) -> int:
    if args.jcmd == "submit":
        spec: dict = {}
        if args.spec:
            spec = json.loads(Path(args.spec).read_text())
        workload = args.workload or spec.get("workload")
        if not workload:
            print("error: --workload or a spec file with 'workload' is required", file=sys.stderr)
            return 2
        params = json.loads(args.params) if args.params else dict(spec.get("params") or {})
        raw_files = args.files or spec.get("files") or []
        paths = _expand(raw_files)
        if not paths:
            print("error: no input files", file=sys.stderr)
            return 2
        created = c.submit_job(
            paths,
            workload,
            params,
            name=args.name or spec.get("name"),
            priority=args.priority or int(spec.get("priority") or 0),
            required_tag=args.required_tag or spec.get("required_tag"),
        )
        if args.json:
            print(created.model_dump_json())
        else:
            print(f"job {created.job_id} queued with {created.total} task(s)")
        return 0
    if args.jcmd == "ls":
        for j in c.list_jobs(args.limit, args.state).jobs:
            print(f"{j.job_id}  {j.state:<10} {j.workload:<10} {j.succeeded}/{j.total}  {_fmt_ts(j.created_at)}  {j.name or ''}")
        return 0
    if args.jcmd == "show":
        job = c.get_job(args.job_id)
        print(f"{job.job_id}  {job.name or ''}  {job.workload}  state={job.state}  {job.succeeded}/{job.total}")
        print(f"eta {job.eta_s}s" if job.eta_s is not None else "eta n/a")
        for pw in job.per_worker:
            print(f"  {pw.name or pw.worker_id:<18} {pw.completed:>4} tasks  mean {pw.mean_duration_s}s  share {pw.share * 100:.1f}%")
        return 0
    if args.jcmd == "watch":
        last = None
        for job in c.wait(args.job_id, poll_s=args.interval):
            snap = (job.succeeded, job.failed, job.state)
            if snap != last:
                eta = f" eta {job.eta_s}s" if job.eta_s is not None else ""
                print(f"[{time.strftime('%H:%M:%S')}] {job.succeeded}/{job.total} done, {job.failed} failed{eta}  {job.state}")
                last = snap
        print("final:", job.state)  # type: ignore[possibly-undefined]
        return 0 if job.state == JobState.SUCCEEDED else 1
    if args.jcmd == "result":
        out = args.output or f"{args.job_id}.zip"
        dest = c.download_result(args.job_id, out)
        print(f"saved {dest} ({dest.stat().st_size} bytes)")
        return 0
    if args.jcmd == "cancel":
        c.cancel_job(args.job_id)
        print("cancel requested")
        return 0
    return 2


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    client = CefClient(args.server, args.token)
    try:
        if args.cmd == "doctor":
            return cmd_doctor(client, args)
        if args.cmd == "workers":
            return cmd_workers(client, args)
        if args.cmd == "stats":
            return cmd_stats(client, args)
        if args.cmd == "storage":
            return cmd_storage(client, args)
        if args.cmd == "workloads":
            return cmd_workloads(client, args)
        if args.cmd == "job":
            return cmd_job(client, args)
        if args.cmd == "mcp":
            from .mcp_server import run as run_mcp

            client.close()
            run_mcp(args.server, args.token)
            return 0
        return 2
    except KeyboardInterrupt:
        return 130
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
