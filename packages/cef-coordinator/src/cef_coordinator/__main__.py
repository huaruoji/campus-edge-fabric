"""\`cef-coord\` command line.

    cef-coord serve                       # one command, zero config
    cef-coord serve --port 9000 --data ~/cef
    cef-coord info                        # where is my data / token
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from . import __version__
from .app import create_app
from .config import Settings


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cef-coord", description="Campus Edge Fabric coordinator")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd")

    serve = sub.add_parser("serve", help="start the coordinator (API + dashboard)")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8080)
    serve.add_argument("--data", type=Path, default=None, help="data dir (default ./cef-data)")
    serve.add_argument("--token", default=None, help="shared bearer token (default: auto-generated)")
    serve.add_argument(
        "--token-file",
        default=os.environ.get("CEF_TOKEN_FILE"),
        help="file containing the bearer token (keeps it out of the process list)",
    )
    serve.add_argument("--log-level", default="info")

    info = sub.add_parser("info", help="print data dir, token and db path")
    info.add_argument("--data", type=Path, default=None)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd in (None, "serve"):
        logging.basicConfig(
            level=getattr(logging, str(getattr(args, "log_level", "info")).upper(), logging.INFO),
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
        settings = Settings()
        if getattr(args, "data", None):
            settings.data_dir = Path(args.data)
        if getattr(args, "token", None):
            settings.token = args.token
        if getattr(args, "token_file", None):
            settings.token = Path(args.token_file).read_text().strip()
        settings.host = getattr(args, "host", settings.host)
        settings.port = getattr(args, "port", settings.port)
        app = create_app(settings)
        print(f"  data dir : {settings.data_dir.resolve()}")
        print(f"  token    : {settings.token}")
        print(f"  dashboard: http://127.0.0.1:{settings.port}/")
        print(f"  api docs : http://127.0.0.1:{settings.port}/docs")
        import uvicorn

        uvicorn.run(app, host=settings.host, port=settings.port, log_level="warning")
        return 0
    if args.cmd == "info":
        settings = Settings()
        if args.data:
            settings.data_dir = Path(args.data)
        print(f"data dir: {settings.data_dir.resolve()}")
        print(f"db      : {settings.db_path.resolve()}")
        token_file = settings.data_dir / "token"
        print(f"token   : {token_file.read_text().strip() if token_file.exists() else '(not created yet)'}")
        return 0
    build_parser().print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
