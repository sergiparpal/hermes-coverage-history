"""Phase 0: schema init + snapshot/module insert+read round trip."""

import sqlite3

import pytest

import db


def test_get_db_path_uses_hermes_home(hermes_home):
    expected = hermes_home / "coverage-history" / "coverage_history.db"
    assert db.get_db_path() == expected


def test_connect_creates_parent_dir_and_schema(hermes_home):
    conn = db.connect()
    try:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        names = {r["name"] for r in cur.fetchall()}
        assert {"snapshots", "modules"}.issubset(names)
    finally:
        conn.close()
    assert (hermes_home / "coverage-history" / "coverage_history.db").exists()


def test_wal_mode_is_enabled(tmp_db):
    mode = tmp_db.execute("PRAGMA journal_mode").fetchone()[0].lower()
    assert mode == "wal"


def test_insert_snapshot_returns_rowid_and_persists(tmp_db):
    sid = db.insert_snapshot(
        tmp_db,
        recorded_at="2026-05-28T10:00:00Z",
        commit_sha="abc123",
        label="ci",
        source_path="/tmp/report.xml",
    )
    assert isinstance(sid, int) and sid > 0

    row = tmp_db.execute(
        "SELECT recorded_at, commit_sha, label, source_path "
        "FROM snapshots WHERE id=?",
        (sid,),
    ).fetchone()
    assert row["recorded_at"] == "2026-05-28T10:00:00Z"
    assert row["commit_sha"] == "abc123"
    assert row["label"] == "ci"
    assert row["source_path"] == "/tmp/report.xml"


def test_insert_modules_round_trip(tmp_db):
    sid = db.insert_snapshot(tmp_db, "2026-05-28T10:00:00Z")
    rows = [
        {"path": "pkg/a.py", "package": "pkg", "lines_total": 4,
         "lines_covered": 3, "pct": 75.0},
        {"path": "pkg/b.py", "package": "pkg", "lines_total": 2,
         "lines_covered": 2, "pct": 100.0},
    ]
    n = db.insert_modules(tmp_db, sid, rows)
    assert n == 2

    out = tmp_db.execute(
        "SELECT path, package, lines_total, lines_covered, pct "
        "FROM modules WHERE snapshot_id=? ORDER BY path",
        (sid,),
    ).fetchall()
    assert len(out) == 2
    assert out[0]["path"] == "pkg/a.py"
    assert out[0]["lines_total"] == 4
    assert out[0]["lines_covered"] == 3
    assert out[0]["pct"] == 75.0
    assert out[1]["path"] == "pkg/b.py"
    assert out[1]["pct"] == 100.0


def test_init_schema_is_idempotent(tmp_db):
    db.init_schema(tmp_db)
    db.init_schema(tmp_db)
    sid = db.insert_snapshot(tmp_db, "2026-05-28T10:00:00Z")
    assert isinstance(sid, int)


def test_schema_version_recorded_and_migrations_run_once(tmp_db):
    versions = [
        r["version"]
        for r in tmp_db.execute(
            "SELECT version FROM schema_version ORDER BY version"
        ).fetchall()
    ]
    assert versions == [v for v, _ in db._MIGRATIONS]
    # Re-init must not re-apply migrations or duplicate version rows.
    db.init_schema(tmp_db)
    again = [
        r["version"]
        for r in tmp_db.execute(
            "SELECT version FROM schema_version ORDER BY version"
        ).fetchall()
    ]
    assert again == versions


def test_unique_snapshot_path_rejects_duplicate(tmp_db):
    """H4: re-inserting the same (snapshot_id, path) must fail, not silently
    inflate aggregates."""
    sid = db.insert_snapshot(tmp_db, "2026-05-28T10:00:00Z")
    db.insert_modules(tmp_db, sid, [
        {"path": "a.py", "package": "a", "lines_total": 10,
         "lines_covered": 7, "pct": 70.0},
    ])
    with pytest.raises(sqlite3.IntegrityError):
        db.insert_modules(tmp_db, sid, [
            {"path": "a.py", "package": "a", "lines_total": 10,
             "lines_covered": 5, "pct": 50.0},
        ])


def test_busy_timeout_is_set(tmp_db):
    """M3: a long-ish busy_timeout keeps concurrent CI ingest + agent read
    from immediately erroring with 'database is locked'."""
    timeout_ms = tmp_db.execute("PRAGMA busy_timeout").fetchone()[0]
    assert timeout_ms >= 5000


def _spy_on_init_schema(db_module, monkeypatch):
    """Record each init_schema call while still running the real DDL."""
    calls = []
    real_init = db_module.init_schema

    def spy(conn):
        calls.append(1)
        return real_init(conn)

    monkeypatch.setattr(db_module, "init_schema", spy)
    return calls


def test_connect_create_false_skips_init_when_schema_present(
    hermes_home, monkeypatch
):
    """#3: once the schema exists, a reader open (create=False) must NOT
    re-run init_schema — reads don't take write intent on the hot path."""
    db.connect().close()  # writer materializes the schema first

    calls = _spy_on_init_schema(db, monkeypatch)
    conn = db.connect(create=False)
    try:
        assert calls == []  # schema present → no DDL on the read path
        # ...and the connection is still usable for reads.
        assert conn.execute("SELECT COUNT(*) FROM modules").fetchone()[0] == 0
    finally:
        conn.close()


def test_connect_create_false_lazily_materializes_virgin_db(
    hermes_home, monkeypatch
):
    """#3: a reader opening a never-written DB still gets the schema created
    once, so queries return empty instead of raising 'no such table'."""
    calls = _spy_on_init_schema(db, monkeypatch)
    conn = db.connect(create=False)
    try:
        assert calls == [1]  # materialized exactly once on the virgin DB
        assert conn.execute("SELECT COUNT(*) FROM modules").fetchone()[0] == 0
    finally:
        conn.close()
