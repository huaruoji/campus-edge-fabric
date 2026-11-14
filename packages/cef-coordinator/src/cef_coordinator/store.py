"""Content-addressed blob store.

Inputs and outputs are stored once by sha256 under `<data>/blobs/ab/cd/<sha>`.
That gives us free deduplication (the same 3 MB photo submitted twice is uploaded
once), integrity checking (worker verifies the sha it was told) and makes
"download the job result" a pure zip of already-existing files.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import BinaryIO

CHUNK = 1 << 20


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class BlobStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.tmp = self.root / ".tmp"
        self.tmp.mkdir(parents=True, exist_ok=True)

    def path_for(self, sha: str) -> Path:
        return self.root / sha[:2] / sha[2:4] / sha

    def exists(self, sha: str) -> bool:
        return self.path_for(sha).exists()

    def put_stream(self, fh: BinaryIO) -> tuple[str, int]:
        """Stream to a temp file while hashing, then move into place atomically."""
        fd, tmp_name = tempfile.mkstemp(dir=self.tmp)
        digest = hashlib.sha256()
        size = 0
        try:
            with os.fdopen(fd, "wb") as out:
                while True:
                    chunk = fh.read(CHUNK)
                    if not chunk:
                        break
                    digest.update(chunk)
                    size += len(chunk)
                    out.write(chunk)
            sha = digest.hexdigest()
            dest = self.path_for(sha)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(tmp_name, dest)
            return sha, size
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise

    def put_bytes(self, data: bytes) -> tuple[str, int]:
        import io

        return self.put_stream(io.BytesIO(data))

    def open(self, sha: str) -> BinaryIO:
        return self.path_for(sha).open("rb")

    def cleanup_tmp(self, older_than_s: float = 3600) -> None:
        cutoff = time.time() - older_than_s
        for p in self.tmp.glob("*"):
            try:
                if p.stat().st_mtime < cutoff:
                    p.unlink()
            except OSError:
                pass
