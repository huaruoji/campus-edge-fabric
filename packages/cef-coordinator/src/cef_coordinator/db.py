"""SQLite access layer.

Deliberately plain `sqlite3` (no ORM): the whole state machine is ~10 queries and
being able to read the SQL in the demo is a feature, not a limitation.

Concurrency model: WAL + a process-wide write lock. Reads run concurrently, every
write happens inside `transaction()` which serialises writers and takes the
database write lock immediately (`BEGIN IMMEDIATE`) so a claim is atomic.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id            TEXT PRIMARY KEY,
  name          TEXT,
  workload      TEXT NOT NULL,
  params        TEXT NOT NULL DEFAULT '{}',
  state         TEXT NOT NULL,
  priority      INTEGER NOT NULL DEFAULT 0,
  required_tag  TEXT,
  submitter     TEXT,
  created_at    REAL NOT NULL,
  started_at    REAL,
  finished_at   REAL
);

CREATE TABLE IF NOT EXISTS tasks (
  id            TEXT PRIMARY KEY,
  job_id        TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  idx           INTEGER NOT NULL,
  state         TEXT NOT NULL,
  attempt       INTEGER NOT NULL DEFAULT 0,
  max_attempts  INTEGER NOT NULL DEFAULT 3,
  params        TEXT NOT NULL DEFAULT '{}',
  inputs        TEXT NOT NULL DEFAULT '[]',
  output        TEXT,
  output_sha    TEXT,
  error         TEXT,
  worker_id     TEXT,
  queued_at     REAL NOT NULL,
  lease_expires REAL,
  started_at    REAL,
  finished_at   REAL,
  duration      REAL,
  updated_at    REAL NOT NULL,
  UNIQUE (job_id, idx)
);
CREATE INDEX IF NOT EXISTS idx_tasks_state ON tasks(state, job_id);
CREATE INDEX IF NOT EXISTS idx_tasks_job ON tasks(job_id, idx);
CREATE INDEX IF NOT EXISTS idx_tasks_worker ON tasks(worker_id, state);

CREATE TABLE IF NOT EXISTS workers (
  id            TEXT PRIMARY KEY,
  name          TEXT NOT NULL,
  hostname      TEXT,
  os            TEXT,
  cpu_count     INTEGER,
  tags          TEXT NOT NULL DEFAULT '[]',
  workloads     TEXT NOT NULL DEFAULT '[]',
  slots         INTEGER NOT NULL DEFAULT 1,
  max_batch     INTEGER NOT NULL DEFAULT 4,
  state         TEXT NOT NULL,
  last_seen     REAL,
  registered_at REAL,
  speed_s       REAL,
  meta          TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS worker_stats (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  worker_id TEXT NOT NULL,
  ts        REAL NOT NULL,
  cpu_pct   REAL,
  mem_pct   REAL,
  gpu_pct   REAL,
  disk_free_gb  REAL,
  disk_total_gb REAL,
  running   INTEGER
);
CREATE INDEX IF NOT EXISTS idx_wstats ON worker_stats(worker_id, ts);

CREATE TABLE IF NOT EXISTS leases (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id    TEXT NOT NULL,
  worker_id  TEXT NOT NULL,
  attempt    INTEGER NOT NULL,
  granted_at REAL NOT NULL,
  expires_at REAL NOT NULL,
  closed_at  REAL,
  outcome    TEXT
);
CREATE INDEX IF NOT EXISTS idx_leases_task ON leases(task_id, attempt);

CREATE TABLE IF NOT EXISTS blobs (
  sha256     TEXT PRIMARY KEY,
  size       INTEGER NOT NULL,
  path       TEXT NOT NULL,
  mime       TEXT,
  name       TEXT,
  created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS provision_requests (
  id         TEXT PRIMARY KEY,
  worker_id  TEXT NOT NULL,
  recipe     TEXT NOT NULL,
  dry_run    INTEGER NOT NULL DEFAULT 1,
  state      TEXT NOT NULL DEFAULT 'queued',
  log        TEXT,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_provision_worker ON provision_requests(worker_id, state);

CREATE TABLE IF NOT EXISTS datasets (
  id          TEXT PRIMARY KEY,
  name        TEXT UNIQUE NOT NULL,
  description TEXT,
  files       TEXT NOT NULL,
  created_at  REAL NOT NULL,
  updated_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_messages (
  id         TEXT PRIMARY KEY,
  room       TEXT NOT NULL DEFAULT 'pool',
  role       TEXT NOT NULL,
  author     TEXT NOT NULL DEFAULT '',
  content    TEXT NOT NULL,
  trace      TEXT,
  preview    TEXT,
  job_id     TEXT,
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_room ON chat_messages(room, created_at);

CREATE TABLE IF NOT EXISTS events (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  ts        REAL NOT NULL,
  kind      TEXT NOT NULL,
  worker_id TEXT,
  job_id    TEXT,
  task_id   TEXT,
  message   TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
"""

_WRITE_LOCK = threading.RLock()


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=10.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    try:
        with _WRITE_LOCK:
            conn.executescript(SCHEMA)
            _migrate(conn)
    finally:
        conn.close()


def _migrate(conn: sqlite3.Connection) -> None:
    """Additive migrations only: `CREATE TABLE IF NOT EXISTS` never alters an
    existing DB, and a running pool must survive an upgrade without losing jobs."""
    wanted = {
        "worker_stats": {
            "disk_free_gb": "REAL",
            "disk_total_gb": "REAL",
        },
        "workers": {
            "speed_s": "REAL",
        },
        "blobs": {
            "mime": "TEXT",
            "name": "TEXT",
        },
    }
    for table, columns in wanted.items():
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        for name, ddl in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
    _normalize_existing_tags(conn)


def _normalize_existing_tags(conn: sqlite3.Connection) -> None:
    """One-time cleanup: tags written before normalisation existed (GPU, 'gpu ',
    gpu_x) are rewritten to the canonical form. Idempotent and quiet once done."""
    import json

    from cef_protocol.tags import normalize_tags

    changed = 0
    for row in conn.execute("SELECT id, tags FROM workers").fetchall():
        try:
            current = json.loads(row[1] or "[]")
        except ValueError:
            current = []
        fixed, _rejected = normalize_tags(current)
        if fixed != current:
            conn.execute("UPDATE workers SET tags=? WHERE id=?", (json.dumps(fixed), row[0]))
            changed += 1
    if changed:
        import logging

        logging.getLogger("cef.db").info("normalised tags on %d worker row(s)", changed)


@contextmanager
def transaction(db_path: Path) -> Iterator[sqlite3.Connection]:
    """Serialised write transaction with immediate write lock."""
    with _WRITE_LOCK:
        conn = connect(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            conn.close()


def reading(db_path: Path) -> sqlite3.Connection:
    return connect(db_path)


def rows(conn: sqlite3.Connection, sql: str, args: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
    return conn.execute(sql, args).fetchall()


def one(conn: sqlite3.Connection, sql: str, args: tuple[Any, ...] = ()) -> sqlite3.Row | None:
    return conn.execute(sql, args).fetchone()
