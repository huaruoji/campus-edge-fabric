"""MCP server: lets Claude Code / Codex / any MCP client drive the pool directly.

Transport is stdio, so the client just spawns \`cef mcp\` and gets tools. The
server is a thin adapter over the same CefClient the CLI uses — no separate
privileges, no separate auth: whatever token the host passes is what you get.

    claude mcp add cef -- uv run --project /path/to/campus-edge-fabric cef mcp \
        --server https://cef.huaruoji.com --token <TOKEN>

Tool results are JSON strings so the calling model can reason about them.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cef_protocol import CefClient

INSTRUCTIONS = """Campus Edge Fabric (CEF) is a small distributed compute pool made of teammates' \
own computers. Jobs are batch tasks: each uploaded file (or each Blender frame) becomes one task, \
spread over the online devices.

Rules of thumb:
- Call list_workloads first: it returns the exact parameter schema (JSON Schema) for each workload.
- Inputs must be real files on this machine; pass absolute paths.
- A job is asynchronous: submit returns a job_id, then poll job_status (or call watch_job).
- Report throughput as completed tasks per second; never count failed tasks.
- Use required_tag to pin a job to specific devices (that is how single-device baselines are measured).
"""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def _server_class():
    """mcp 2.x renamed FastMCP -> MCPServer; support both so a teammate's older
    SDK still works."""
    try:
        from mcp.server.mcpserver import MCPServer

        return MCPServer
    except ImportError:  # mcp 1.x
        from mcp.server.fastmcp import FastMCP

        return FastMCP


def build_server(server: str, token: str, name: str = "campus-edge-fabric"):
    server_cls = _server_class()
    client = CefClient(server, token)
    app = server_cls(name, instructions=INSTRUCTIONS)

    @app.tool()
    def list_workloads() -> str:
        """List workload kinds (thumbnail / infer / blender) with their parameter JSON Schema and which devices can run them."""
        return _json(
            [
                {
                    "kind": w.kind,
                    "unit": w.unit,
                    "params_schema": w.params_schema,
                    "available_on": w.available_on,
                }
                for w in client.workloads().workloads
            ]
        )

    @app.tool()
    def list_workers() -> str:
        """List devices in the pool: state, CPU/GPU load, slots and completed task counts."""
        return _json([w.model_dump() for w in client.workers().workers])

    @app.tool()
    def pool_stats(window_s: int = 300) -> str:
        """Pool-level statistics for the last window: tasks/min, p50/p95 duration, per-device utilization, bottleneck hint."""
        return _json(client.stats(window_s).model_dump())

    @app.tool()
    def submit_job(
        files: list[str],
        workload: str,
        params: dict[str, Any] | None = None,
        name: str | None = None,
        required_tag: str | None = None,
    ) -> str:
        """Submit a batch job. Each file becomes one task (blender: one task per frame).

        Args:
            files: absolute paths of input files on this machine.
            workload: one of the kinds returned by list_workloads.
            params: workload parameters, validated against the schema.
            name: human readable job name.
            required_tag: only let devices carrying this tag run the job.
        """
        paths = [Path(f) for f in files]
        missing = [str(p) for p in paths if not p.is_file()]
        if missing:
            return _json({"error": "missing input files", "files": missing})
        created = client.submit_job(
            paths, workload, params or {}, name=name, required_tag=required_tag
        )
        return _json(created.model_dump())

    @app.tool()
    def job_status(job_id: str) -> str:
        """Current state of a job: progress, ETA, per-device breakdown (who did how many tasks)."""
        job = client.get_job(job_id)
        return _json(job.model_dump())

    @app.tool()
    def watch_job(job_id: str, timeout_s: int = 600) -> str:
        """Block until the job reaches a terminal state (or timeout), then return the final status."""
        last = None
        for job in client.wait(job_id, poll_s=1.0, timeout_s=float(timeout_s)):
            last = job
        return _json(last.model_dump() if last else {"error": "no status"})

    @app.tool()
    def job_manifest(job_id: str) -> str:
        """Verifiable per-task record: device, attempt, duration, output sha256. Use this as the evidence trail."""
        return _json(client.manifest(job_id))

    @app.tool()
    def download_result(job_id: str, dest: str) -> str:
        """Download the result zip (manifest.json + outputs/) to a local path."""
        path = client.download_result(job_id, dest)
        return _json({"saved": str(path.resolve()), "bytes": path.stat().st_size})

    @app.tool()
    def cancel_job(job_id: str) -> str:
        """Cancel a job: queued and leased tasks are dropped, finished results are kept."""
        client.cancel_job(job_id)
        return _json({"ok": True, "job_id": job_id})

    @app.tool()
    def health() -> str:
        """Coordinator version, uptime and number of online devices."""
        return _json(client.health().model_dump())

    return app


def run(server: str, token: str) -> None:
    app = build_server(server, token)
    app.run()  # stdio transport
