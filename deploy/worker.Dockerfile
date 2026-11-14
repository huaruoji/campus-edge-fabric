# Worker image for devices that should not get a Python toolchain (NAS, mini PCs).
# Same code path as a native install: uv resolves the workspace, the venv is the entrypoint.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app
COPY . /app

# --inexact keeps packages the owner installed by hand (e.g. onnxruntime-gpu)
RUN uv sync --package cef-worker --package cef-client --frozen --inexact

ENV CEF_DATA=/data \
    PYTHONUNBUFFERED=1
VOLUME ["/data"]

ENTRYPOINT ["/app/.venv/bin/cef-worker", "run"]
