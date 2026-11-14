# AGENTS.md — for coding agents (Claude Code / Codex / Cursor / others)

This repo is **Campus Edge Fabric**: a coordinator + worker agents that turn a few personal computers
into a small batch-compute pool. If you were asked to "run something on the pool", you do not need to
read the whole codebase — use the CLI or the MCP server below.

## 1. Submitting work (the sbatch equivalent)

The live pool is at `https://cef.huaruoji.com` (see `deploy/vps/INSTANCE.md`). You need a token from
the repo owner; it is never committed. Export it once:

```bash
export CEF_SERVER=https://cef.huaruoji.com
export CEF_TOKEN=<token>
```

Inspect what the pool can run before submitting (the parameter schemas are authoritative):

```bash
uv run cef doctor            # connectivity + workloads + which devices offer them
uv run cef workloads         # parameter JSON Schema per workload
uv run cef workers           # devices, load, completed counts
```

Submit declaratively (preferred — versionable, reproducible):

```bash
uv run cef job submit --spec examples/job-thumbnail.json
uv run cef job watch <job_id>              # blocks until terminal state
uv run cef job show <job_id>               # per-device breakdown
uv run cef job result <job_id> -o out.zip  # manifest.json + outputs/
```

Or inline:

```bash
uv run cef job submit --workload thumbnail --params '{"size":512}' ./photos/*.jpg
```

## 2. As an MCP server

```bash
# Claude Code
claude mcp add cef -- uv run --project /path/to/campus-edge-fabric cef mcp \
  --server https://cef.huaruoji.com --token "$CEF_TOKEN"
```

```toml
# Codex (~/.codex/config.toml)
[mcp_servers.cef]
command = "uv"
args = ["run", "--project", "/path/to/campus-edge-fabric", "cef", "mcp",
        "--server", "https://cef.huaruoji.com", "--token", "<TOKEN>"]
```

Tools exposed: `list_workloads`, `list_workers`, `pool_stats`, `submit_job`, `job_status`,
`watch_job`, `job_manifest`, `download_result`, `cancel_job`, `health`.

## 3. Rules you must respect

- **Never fake throughput.** Report `succeeded / elapsed`; failed tasks are not work.
- **Do not exceed the input budget**: compress inputs to ≤ 500 KB (long edge 1024, JPEG q85) — the
  coordinator sits behind a 400 GB/month link in Los Angeles.
- Uploads are content-addressed: submitting the same file twice does not double storage, but it does
  transfer again — reuse a dataset instead of re-uploading in a loop.
- Do not commit tokens, never write them into source files; `/etc/cef/token` is the source of truth
  on the server side.
- The worker only runs pre-approved adapters (`thumbnail`, `infer`, `blender`). There is no arbitrary
  shell execution by design — do not try to add one.

## 4. Working on the codebase

```bash
uv sync --all-packages
uv run pytest -q                # scheduler invariants (lease, fencing, expiry)
uv run ruff check .
uv run cef-coord serve --port 8080 --data ./cef-data       # local coordinator
uv run cef-worker run --server http://127.0.0.1:8080 --token <printed> --workloads thumbnail
```

- Wire contract: `docs/api.md` + `packages/cef-protocol`. Change those first, then both sides.
- Push to `main` deploys itself: the VPS pulls every 60 s and restarts the coordinator. The dashboard
  build is committed, so do not leave `web/dist` stale — run `cd web && npm run build` before pushing
  frontend changes.
