"""Phase 1: CLI ingestion handler tests."""

import argparse

import cli as cli_module
import db


def _build_args(report_path, sha=None, label=None, sub="record"):
    p = argparse.ArgumentParser()
    cli_module.setup_argparse(p)
    argv = []
    if sub == "record":
        argv = ["record", str(report_path)]
        if sha:
            argv += ["--sha", sha]
        if label:
            argv += ["--label", label]
    elif sub is None:
        argv = []
    return p.parse_args(argv)


def test_record_inserts_snapshot_and_modules(
    sample_cobertura_xml, hermes_home, capsys
):
    args = _build_args(sample_cobertura_xml, sha="abc123", label="ci")
    rc = cli_module.handle(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "Recorded 3 modules" in out
    assert "snapshot #" in out

    conn = db.connect()
    try:
        snaps = conn.execute(
            "SELECT id, commit_sha, label, source_path, recorded_at "
            "FROM snapshots"
        ).fetchall()
        assert len(snaps) == 1
        snap = snaps[0]
        assert snap["commit_sha"] == "abc123"
        assert snap["label"] == "ci"
        assert snap["source_path"] == str(sample_cobertura_xml)
        # recorded_at is ISO-8601 with trailing Z
        assert snap["recorded_at"].endswith("Z")
        assert "T" in snap["recorded_at"]

        mods = conn.execute(
            "SELECT path, lines_total, lines_covered, pct, package "
            "FROM modules WHERE snapshot_id=? ORDER BY path",
            (snap["id"],),
        ).fetchall()
        by_path = {m["path"]: m for m in mods}
        assert set(by_path) == {"pkg_a/bar.py", "pkg_a/foo.py", "pkg_b/baz.py"}
        assert by_path["pkg_a/foo.py"]["pct"] == 75.0
        assert by_path["pkg_a/foo.py"]["lines_total"] == 4
        assert by_path["pkg_a/foo.py"]["lines_covered"] == 3
        assert by_path["pkg_a/foo.py"]["package"] == "pkg_a"
        assert by_path["pkg_a/bar.py"]["pct"] == 100.0
        assert by_path["pkg_b/baz.py"]["pct"] == 25.0
    finally:
        conn.close()


def test_record_without_sha_or_label(sample_cobertura_xml, hermes_home):
    args = _build_args(sample_cobertura_xml)
    rc = cli_module.handle(args)
    assert rc == 0
    conn = db.connect()
    try:
        snap = conn.execute(
            "SELECT commit_sha, label FROM snapshots"
        ).fetchone()
        assert snap["commit_sha"] is None
        assert snap["label"] is None
    finally:
        conn.close()


def test_record_missing_file_returns_nonzero(tmp_path, hermes_home, capsys):
    args = _build_args(tmp_path / "nope.xml")
    rc = cli_module.handle(args)
    assert rc != 0
    err = capsys.readouterr().err
    assert "error" in err.lower()


def test_record_malformed_file_returns_nonzero(tmp_path, hermes_home, capsys):
    bad = tmp_path / "broken.xml"
    bad.write_text("<coverage><packages")
    args = _build_args(bad)
    rc = cli_module.handle(args)
    assert rc != 0
    err = capsys.readouterr().err
    assert "error" in err.lower()


def test_handle_without_subcommand_prints_usage(hermes_home, capsys):
    args = _build_args(None, sub=None)
    rc = cli_module.handle(args)
    assert rc != 0
    err = capsys.readouterr().err
    assert "Usage" in err


def test_record_is_transactional_on_failure(
    sample_cobertura_xml, hermes_home, monkeypatch, capsys
):
    """M1: if insert_modules raises mid-record, the snapshot row must roll
    back instead of being left as an orphan."""
    args = _build_args(sample_cobertura_xml)

    def boom(*a, **k):
        raise RuntimeError("simulated db failure")

    monkeypatch.setattr(cli_module.db, "insert_modules", boom)
    rc = cli_module.handle(args)
    assert rc != 0
    err = capsys.readouterr().err
    assert "error" in err.lower()

    conn = db.connect()
    try:
        n = conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
    finally:
        conn.close()
    assert n == 0, "snapshot row should have been rolled back"
