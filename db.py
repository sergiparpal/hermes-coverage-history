"""SQLite persistence for the hermes-coverage-history plugin.

Standalone module: no Hermes imports here, so this is unit-testable in
isolation. The DB path is resolved at call time from `HERMES_HOME` (so tests
can monkeypatch the env var without restarting the process).

Schema evolves via the `_MIGRATIONS` list — every migration runs at most
once per database, tracked in `schema_version`. To extend the schema,
append a new (version, ddl) tuple. Never reorder or rewrite existing
entries.
"""

from __future__ import annotations

import contextlib
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

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);
"""


# Append-only list of (version, ddl) pairs. Each entry runs at most once per
# database. The first migration (v1) dedupes any (snapshot_id, path) collisions
# from pre-UNIQUE databases, then enforces uniqueness going forward.
#
# Migrations MUST be idempotent: `executescript` autocommits internally, so
# the DDL and the `INSERT INTO schema_version` row are not atomic. If the
# process dies between the two, the migration will run again on next boot.
# Use `IF NOT EXISTS` / `DELETE ... WHERE` style statements only.
_MIGRATIONS: list[tuple[int, str]] = [
    (
        1,
        """
        DELETE FROM modules
        WHERE rowid NOT IN (
            SELECT MIN(rowid) FROM modules GROUP BY snapshot_id, path
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_modules_snap_path
            ON modules(snapshot_id, path);
        """,
    ),
]


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
    conn = sqlite3.connect(str(path), timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    # Restrict perms so other users on a multi-user host can't read the
    # local coverage history (default umask 0o022 leaves the DB world-
    # readable). The parent dir at 0o700 also gates the WAL / SHM files,
    # which SQLite creates with default perms.
    _restrict_perms(path)
    return conn


def _restrict_perms(path: Path) -> None:
    # Best-effort: chmod may silently fail on filesystems that don't
    # support POSIX modes (e.g. NTFS mounts under WSL). The fallback is
    # the user's existing umask, which is no worse than today.
    try:
        os.chmod(path.parent, 0o700)
        os.chmod(path, 0o600)
    except OSError:
        pass


@contextlib.contextmanager
def session(db_path: Optional[Path] = None):
    """Open a connection, yield it, and close on exit.

    Sugar for the open/try/finally/close pattern repeated across the tool
    handlers and the pre-LLM hook.
    """
    conn = connect(db_path)
    try:
        yield conn
    finally:
        conn.close()


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    _apply_migrations(conn)
    conn.commit()


def _apply_migrations(conn: sqlite3.Connection) -> None:
    cur = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    current = cur[0] if cur and cur[0] is not None else 0
    for version, ddl in _MIGRATIONS:
        if version <= current:
            continue
        conn.executescript(ddl)
        conn.execute(
            "INSERT INTO schema_version (version) VALUES (?)", (version,)
        )


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
    return int(cur.lastrowid)


def insert_modules(
    conn: sqlite3.Connection, snapshot_id: int, rows: Iterable[dict]
) -> int:
    module_rows = [
        (
            snapshot_id,
            row["path"],
            row.get("package"),
            int(row["lines_total"]),
            int(row["lines_covered"]),
            float(row["pct"]),
        )
        for row in rows
    ]
    conn.executemany(
        "INSERT INTO modules "
        "(snapshot_id, path, package, lines_total, lines_covered, pct) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        module_rows,
    )
    return len(module_rows)
