"""\`cef-worker\` command line.

    cef-worker run --server http://192.168.1.10:8080 --token XXX
    cef-worker doctor --server http://192.168.1.10:8080 --token XXX

\`run\` is the whole installation on a teammate's machine: it registers, advertises
the workloads it can actually execute, and blocks until Ctrl-C (which drains
gracefully so in-flight tasks are not wasted).
"""

from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
from pathlib import Path

from . import __version__
from .agent import Agent
from .client import ApiError, WorkerAPI
from .config import WorkerConfig
from .workloads import load_adapters


def _split(value: str | None) -> list[str]:
    return [p.strip() for p in (value or "").split(",") if p.strip()]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cef-worker", description="Campus Edge Fabric worker agent")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd")

    def common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--server", default=None, help="coordinator base url")
        sp.add_argument("--token", default=None, help="shared bearer token")
        sp.add_argument("--data", default=None, help="local data dir")

    run = sub.add_parser("run", help="join the pool and start computing")
    common(run)
    run.add_argument("--name", default=None, help="device name shown on the dashboard")
    run.add_argument("--tags", default="", help="comma separated tags, e.g. gpu,lab-pc")
    run.add_argument("--slots", type=int, default=None, help="parallel tasks on this device")
    run.add_argument("--max-batch", type=int, default=None, help="max tasks per lease")
    run.add_argument("--workloads", default="", help="allowlist, e.g. thumbnail,infer,blender")
    run.add_argument("--model-dir", default=None, help="where .onnx models live")
    run.add_argument("--blender", default=None, help="path to the blender binary")
    run.add_argument("--max-cpu", type=float, default=None, help="stop taking work above this CPU %%")
    run.add_argument("--log-level", default="info")

    join = sub.add_parser("join", help="alias of run")
    common(join)
    join.add_argument("--name", default=None)

    doc = sub.add_parser("doctor", help="check connectivity, workloads and models")
    common(doc)
    doc.add_argument("--workloads", default="")
    doc.add_argument("--model-dir", default=None)

    sub.add_parser("info", help="print default config")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = WorkerConfig()
    for attr, key in (
        ("server", "server"),
        ("token", "token"),
        ("name", "name"),
        ("data", "data_dir"),
        ("model_dir", "model_dir"),
        ("blender", "blender_bin"),
        ("max_cpu", "max_cpu_pct"),
    ):
        value = getattr(args, attr, None)
        if value:
            setattr(cfg, key, Path(value) if key in ("data_dir", "model_dir") else value)
    if getattr(args, "slots", None):
        cfg.slots = int(args.slots)
    if getattr(args, "max_batch", None):
        cfg.max_batch = int(args.max_batch)
    if getattr(args, "tags", None):
        cfg.tags = _split(args.tags)
    if getattr(args, "workloads", None):
        cfg.workloads = _split(args.workloads)

    logging.basicConfig(
        level=getattr(logging, str(getattr(args, "log_level", "info")).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if args.cmd in (None, "run", "join"):
        if not cfg.server:
            print("error: --server is required (e.g. --server http://192.168.1.10:8080)", file=sys.stderr)
            return 2
        print(f"  coordinator : {cfg.server}")
        print(f"  device name : {cfg.name or '<hostname>'}")
        print(f"  slots       : {cfg.slots}")
        print(f"  data dir    : {cfg.data_dir}")
        print(f"  workloads   : {', '.join(cfg.workloads) if cfg.workloads else 'all available'}")
        agent = Agent(cfg)
        signal.signal(signal.SIGTERM, lambda *_: agent._stop.set())
        try:
            signal.signal(signal.SIGINT, lambda *_: agent._stop.set())
        except ValueError:
            pass
        agent.run()
        return 0

    if args.cmd == "doctor":
        ok = True
        print(f"cef-worker {__version__}")
        print(f"coordinator: {cfg.server}")
        adapters = load_adapters(cfg.workloads or None, model_dir=cfg.model_dir, blender_bin=cfg.blender_bin)
        print("workloads on this device:")
        for adapter in adapters.values():
            spec = adapter.probe()
            mark = "OK  " if spec.get("available") else "MISS"
            print(f"  [{mark}] {adapter.kind:<10} {spec.get('version') or '-'} {spec.get('reason') or ''}")
            ok = ok and bool(spec.get("available")) or cfg.workloads != []
        try:
            api = WorkerAPI(cfg.server, cfg.token, timeout=10)
            health = api.health()
            print(f"connectivity: OK {json.dumps(health)}")
            api.close()
        except ApiError as exc:
            print(f"connectivity: FAIL {exc}", file=sys.stderr)
            ok = False
        except Exception as exc:  # noqa: BLE001
            print(f"connectivity: FAIL {exc}", file=sys.stderr)
            ok = False
        return 0 if ok else 1

    if args.cmd == "info":
        print(json.dumps(
            {
                "server": cfg.server,
                "data_dir": str(cfg.data_dir),
                "model_dir": str(cfg.model_dir),
                "slots": cfg.slots,
                "max_batch": cfg.max_batch,
            },
            indent=2,
        ))
        return 0

    build_parser().print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
