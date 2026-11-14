"""The `cef-worker` command line.

    cef-worker run --server http://192.168.1.10:8080 --token XXX
    cef-worker doctor --server http://192.168.1.10:8080 --token XXX

cef-worker run is the whole installation on a device: it registers, advertises
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
    run.add_argument(
        "--allow-provision",
        default=None,
        help="让它能被远程配置的环境（逗号分隔，可选 infer,blender；默认全拒绝）",
    )
    run.add_argument("--log-level", default="info")

    join = sub.add_parser("join", help="alias of run")
    common(join)
    join.add_argument("--name", default=None)

    doc = sub.add_parser("doctor", help="check connectivity, workloads and models")
    common(doc)
    doc.add_argument("--workloads", default="")
    doc.add_argument("--model-dir", default=None)

    upd = sub.add_parser("update", help="git pull + uv sync --inexact（保留手工装的包）")
    upd.add_argument("--check", action="store_true", help="只对比版本，不更新")
    upd.add_argument("--restart", action="store_true", help="更新后尝试 systemctl restart cef-worker")
    common(upd)

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
    if getattr(args, "allow_provision", None):
        cfg.allow_provision = _split(args.allow_provision)

    logging.basicConfig(
        level=getattr(logging, str(getattr(args, "log_level", "info")).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    # one poll per second: httpx's per-request INFO lines would drown the log
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

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

    if args.cmd == "update":
        return cmd_update(args)

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


def _repo_root() -> Path | None:
    """Locate the git checkout this worker was installed from."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / ".git").exists() and (parent / "pyproject.toml").exists():
            return parent
    return None


def cmd_update(args: argparse.Namespace) -> int:
    """Update a device's worker in place.

    --inexact is deliberate: a plain uv sync would uninstall packages the owner
    added by hand (onnxruntime-gpu from the provision recipe), silently turning a
    GPU worker back into a CPU one.
    """
    import subprocess

    root = _repo_root()
    print(f"cef-worker {__version__}")
    if root is None:
        print("没有找到 git 仓库（这个 worker 不是从源码目录启动的）。", file=sys.stderr)
        print("请回到安装目录执行：git pull && uv sync --package cef-worker --inexact", file=sys.stderr)
        return 2
    print(f"仓库: {root}")

    if getattr(args, "check", False):
        try:
            subprocess.run(["git", "-C", str(root), "fetch", "-q", "origin"], timeout=60, check=False)
            remote = subprocess.run(
                ["git", "-C", str(root), "log", "-1", "--format=%h %s", "origin/main"],
                capture_output=True, text=True, timeout=30, check=False,
            ).stdout.strip()
            local = subprocess.run(
                ["git", "-C", str(root), "log", "-1", "--format=%h %s", "HEAD"],
                capture_output=True, text=True, timeout=30, check=False,
            ).stdout.strip()
        except Exception as exc:  # noqa: BLE001
            print(f"无法读取 git 状态: {exc}", file=sys.stderr)
            return 1
        print(f"本地 {local}\n远端 {remote}")
        return 0 if remote and remote == local else 1

    steps = [
        ["git", "-C", str(root), "pull", "--ff-only"],
        ["uv", "sync", "--package", "cef-worker", "--package", "cef-client", "--inexact"],
    ]
    for argv in steps:
        print("$ " + " ".join(argv))
        proc = subprocess.run(argv, cwd=str(root), check=False)
        if proc.returncode != 0:
            print(f"失败（退出码 {proc.returncode}）。", file=sys.stderr)
            return proc.returncode

    print("代码与依赖已更新。")
    if getattr(args, "restart", False):
        proc = subprocess.run(["systemctl", "restart", "cef-worker"], check=False)
        print("systemctl restart cef-worker ->", proc.returncode)
        return proc.returncode
    print("重启 worker 后生效：systemctl restart cef-worker（或 Ctrl-C 后重新运行启动命令）")
    return 0
