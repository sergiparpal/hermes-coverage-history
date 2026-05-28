"""Phase 0: schema init + snapshot/module insert+read round trip."""

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
