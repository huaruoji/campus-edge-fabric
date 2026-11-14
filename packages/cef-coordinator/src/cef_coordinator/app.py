"""FastAPI app factory.

One process serves everything: REST API + dashboard static files + the background
reaper. That is deliberate for a one-day deployment: \`cef-coord serve\` is the
whole control plane, there is nothing else to start.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from cef_protocol.models import ErrorResponse
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__, api, db, scheduler  # noqa: F401
from .config import Settings
from .store import BlobStore

log = logging.getLogger("cef.coordinator")

FALLBACK_HTML = """<!doctype html><meta charset=utf-8><title>Campus Edge Fabric</title>
<style>body{font:15px/1.6 system-ui;margin:64px auto;max-width:720px;color:#1b2430}
code{background:#f2f4f7;padding:2px 6px;border-radius:4px}</style>
<h1>Campus Edge Fabric coordinator is running</h1>
<p>Dashboard assets are not built in this tree. Either build them:</p>
<pre><code>cd web &amp;&amp; npm install &amp;&amp; npm run build</code></pre>
<p>or use the API directly: <a href="/docs">/docs</a> (OpenAPI), <code>/api/v1/health</code>.</p>
"""


async def _maintenance(app: FastAPI) -> None:
    s: Settings = app.state.settings
    last_prune = 0.0
    last_gc = 0.0
    while True:
        try:
            scheduler.reap_expired_leases(s.db_path, s)
            scheduler.sweep_workers(s.db_path, s)
            now = time.time()
            if now - last_prune > 300:
                scheduler.prune_stats(s.db_path, s)
                app.state.blobs.cleanup_tmp()
                last_prune = now
            if now - last_gc > 600:
                deleted, freed = scheduler.gc_blobs(s.db_path, s, app.state.blobs.root)
                if deleted:
                    log.info("blob gc: %d files, %.1f MB", deleted, freed / 1e6)
                last_gc = now
        except Exception:  # noqa: BLE001 - never let the loop die
            log.exception("maintenance loop error")
        await asyncio.sleep(s.reaper_interval_s)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    settings.ensure_dirs()
    db.init_db(settings.db_path)
    token = settings.token or _load_or_create_token(settings)
    settings.token = token

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        task = asyncio.create_task(_maintenance(app))
        try:
            yield
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    app = FastAPI(
        title="Campus Edge Fabric",
        version=__version__,
        description="Personal devices as a distributed compute service — coordinator API v1",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.blobs = BlobStore(settings.blob_dir)
    app.state.started_at = time.time()
    app.state.version = __version__

    @app.middleware("http")
    async def _auth(request: Request, call_next):  # type: ignore[no-untyped-def]
        path = request.url.path
        if (
            settings.token
            and path.startswith("/api/v1")
            and path not in ("/api/v1/health",)
            and request.method != "OPTIONS"
        ):
            header = request.headers.get("authorization", "")
            supplied = header[7:] if header.lower().startswith("bearer ") else ""
            if not secrets.compare_digest(supplied, settings.token):
                return JSONResponse(
                    ErrorResponse(error={"code": "unauthorized", "message": "missing or invalid bearer token"}).model_dump(),
                    status_code=401,
                )
        return await call_next(request)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException):
        body = exc.detail if isinstance(exc.detail, dict) and "error" in exc.detail else {
            "error": {"code": "http_error", "message": str(exc.detail)}
        }
        return JSONResponse(body, status_code=exc.status_code, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError):
        return JSONResponse(
            {"error": {"code": "invalid_params", "message": "request validation failed"}}, status_code=422
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):  # type: ignore[no-untyped-def]
        if isinstance(exc, __import__("fastapi").HTTPException) and isinstance(exc.detail, dict):  # type: ignore[attr-defined]
            return JSONResponse(exc.detail, status_code=exc.status_code)  # type: ignore[attr-defined]
        log.exception("unhandled error on %s", request.url.path)
        return JSONResponse(
            ErrorResponse(error={"code": "internal", "message": str(exc)[:200]}).model_dump(), status_code=500
        )

    app.include_router(api.router)

    @app.get("/api/v1/dashboard", response_class=PlainTextResponse, include_in_schema=False)
    def dashboard_hint() -> str:
        return "Dashboard is served at / — the API lives under /api/v1."

    if (settings.web_dir / "index.html").exists():
        app.mount("/", StaticFiles(directory=str(settings.web_dir), html=True), name="web")
    else:

        @app.get("/", response_class=HTMLResponse, include_in_schema=False)
        def fallback() -> str:
            return FALLBACK_HTML

    return app


def _load_or_create_token(settings: Settings) -> str:
    """Zero-config security: mint a token on first run and persist it, so a fresh
    \`cef-coord serve\` is never an unauthenticated service."""
    path = settings.data_dir / "token"
    if path.exists():
        token = path.read_text().strip()
        if token:
            return token
    token = secrets.token_urlsafe(24)
    path.write_text(token)
    log.info("generated coordinator token -> %s", path)
    return token
