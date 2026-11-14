"""Worker-side view of the shared model manifest."""

from __future__ import annotations

from cef_protocol.model_manifest import (  # noqa: F401  (re-export)
    MANIFEST_PATH,
    canonical,
    expected_sha,
    load_manifest,
    required_shas,
)
