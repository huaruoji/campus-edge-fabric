"""Canonical model manifest, shared by coordinator and workers.

Lives in cef-protocol so the 'which weights are we allowed to compare' decision is
one file, not two copies that drift.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

MANIFEST_PATH = Path(__file__).with_name("models.json")


def load_manifest() -> dict[str, Any]:
    try:
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"models": []}


def canonical(name: str) -> dict[str, Any] | None:
    for entry in load_manifest().get("models", []):
        if entry.get("status") != "required":
            continue
        if name in (entry.get("name"), Path(str(entry.get("file", ""))).stem):
            return entry
    return None


def expected_sha(name: str) -> str | None:
    entry = canonical(name)
    return str(entry["sha256"]) if entry and entry.get("sha256") else None


def required_shas() -> dict[str, str]:
    out: dict[str, str] = {}
    for entry in load_manifest().get("models", []):
        if entry.get("status") == "required" and entry.get("sha256"):
            out[str(entry.get("name"))] = str(entry["sha256"])
    return out
