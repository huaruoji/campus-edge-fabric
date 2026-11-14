"""Tag normalisation and grouping.

Tags are free-form today (--tags bench-jochi,rtx5060), which is how a device ends
up with GPU, "gpu ", gpu-x and gpu_x on three machines. The server is the only
place that can enforce one spelling, so every write path funnels through
normalize_tags.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

#: underscores become dashes on purpose: gpu_x and gpu-x are the same intent
_ALLOWED = re.compile(r"[^a-z0-9.:-]+")
_DASHES = re.compile(r"-{2,}")

#: prefixes with a meaning; everything else is a free-form label
RESERVED_PREFIXES = ("role:", "hw:", "site:")
BENCH_PREFIX = "bench-"

MAX_TAGS = 8
MAX_LEN = 24


def normalize_tag(raw: str) -> str:
    value = raw.strip().lower()
    value = _ALLOWED.sub("-", value.replace(" ", "-"))
    value = _DASHES.sub("-", value).strip("-._:")
    return value[:MAX_LEN].strip("-._:")


def normalize_tags(raw: Iterable[str] | None, max_tags: int = MAX_TAGS) -> tuple[list[str], list[str]]:
    """Return (kept, rejected).

    kept is de-duplicated and sorted so two devices with the same intent always
    serialise identically; rejected is what the worker/UI should show back.
    """
    kept: list[str] = []
    rejected: list[str] = []
    seen: set[str] = set()
    for item in raw or []:
        if not isinstance(item, str):
            continue
        if not item.strip():
            continue
        tag = normalize_tag(item)
        if not tag:
            rejected.append(item)
            continue
        if tag in seen:
            continue
        if len(kept) >= max_tags:
            rejected.append(item)
            continue
        seen.add(tag)
        kept.append(tag)
    return sorted(kept), rejected


def categorize(tag: str) -> str:
    if tag.startswith(BENCH_PREFIX):
        return "bench"
    for prefix in RESERVED_PREFIXES:
        if tag.startswith(prefix):
            return prefix.rstrip(":")
    return "free"


CATEGORY_LABELS = {
    "role": "角色",
    "hw": "硬件",
    "site": "场地",
    "bench": "基准",
    "free": "其他",
}