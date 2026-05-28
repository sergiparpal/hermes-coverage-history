"""SQLite persistence for the hermes-coverage-history plugin.

Standalone module: no Hermes imports here, so this is unit-testable in
isolation. The DB path is resolved at call time from `HERMES_HOME` (so tests
can monkeypatch the env var without restarting the process).
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Iterable, Optional


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_at TEXT    NOT NULL,
    commit_sha  TEXT,
    label       TEXT,
    source_path TEXT
);

CREATE TABLE IF NOT EXISTS modules (
    snapshot_id   INTEGER NOT NULL REFERENCES snapshots(id),
    path          TEXT    NOT NULL,
    package       TEXT,
    lines_total   INTEGER NOT NULL,
    lines_covered INTEGER NOT NULL,
    pct           REAL    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_modules_path   ON modules(path);
CREATE INDEX IF NOT EXISTS idx_modules_snap   ON modules(snapshot_id);
CREATE INDEX IF NOT EXISTS idx_snapshots_time ON snapshots(recorded_at);
"""


def hermes_home() -> Path:
    home = os.environ.get("HERMES_HOME")
    if home:
        return Path(home).expanduser()
    return Path.home() / ".hermes"


def get_db_path() -> Path:
    return hermes_home() / "coverage-history" / "coverage_history.db"


def connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path is not None else get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    conn.commit()


def insert_snapshot(
    conn: sqlite3.Connection,
    recorded_at: str,
    commit_sha: Optional[str] = None,
    label: Optional[str] = None,
    source_path: Optional[str] = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO snapshots (recorded_at, commit_sha, label, source_path) "
        "VALUES (?, ?, ?, ?)",
        (recorded_at, commit_sha, label, source_path),
    )
    conn.commit()
    return int(cur.lastrowid)


def _row_field(row, key: str):
    if hasattr(row, key):
        return getattr(row, key)
    return row[key]


def insert_modules(conn: sqlite3.Connection, snapshot_id: int, rows: Iterable) -> int:
    payload = []
    for r in rows:
        payload.append(
            (
                snapshot_id,
                _row_field(r, "path"),
                _row_field(r, "package"),
                int(_row_field(r, "lines_total")),
                int(_row_field(r, "lines_covered")),
                float(_row_field(r, "pct")),
            )
        )
    conn.executemany(
        "INSERT INTO modules "
        "(snapshot_id, path, package, lines_total, lines_covered, pct) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        payload,
    )
    conn.commit()
    return len(payload)
