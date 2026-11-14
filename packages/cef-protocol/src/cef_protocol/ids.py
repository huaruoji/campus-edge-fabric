"""Sortable, collision-resistant identifiers (ULID-ish, no extra dependency)."""

from __future__ import annotations

import os
import time

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _encode(value: int, length: int) -> str:
    out = []
    for _ in range(length):
        out.append(_CROCKFORD[value & 0x1F])
        value >>= 5
    return "".join(reversed(out))


def new_id(prefix: str = "") -> str:
    """Timestamp prefix keeps SQLite ordering == creation ordering."""
    ts = int(time.time() * 1000)
    rand = int.from_bytes(os.urandom(10), "big")
    body = _encode(ts, 10) + _encode(rand, 16)
    return f"{prefix}_{body}" if prefix else body
