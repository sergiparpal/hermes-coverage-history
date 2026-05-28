"""Phase 2: trend analysis + the headline silent-regression test."""

from datetime import datetime, timedelta, timezone

import pytest

import db
import trends


UTC = timezone.utc


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _record_snapshot(conn, when_iso: str, *modules):
    sid = db.insert_snapshot(conn, recorded_at=when_iso)
    rows = []
    for path, package, lt, lc in modules:
        pct = (100.0 * lc / lt) if lt > 0 else 0.0
        rows.append(
            {
                "path": path, "package": package,
                "lines_total": lt, "lines_covered": lc, "pct": pct,
            }
        )
    db.insert_modules(conn, sid, rows)
    return sid


# ---------- parse_since ----------------------------------------------------


def test_parse_since_relative_days():
    base = datetime(2026, 5, 28, 12, 0, tzinfo=UTC)
    assert trends.parse_since("7d", now=base) == base - timedelta(days=7)


def test_parse_since_relative_weeks():
    base = datetime(2026, 5, 28, 12, 0, tzinfo=UTC)
    assert trends.parse_since("2w", now=base) == base - timedelta(weeks=2)


def test_parse_since_iso_date():
    assert trends.parse_since("2026-01-15") == datetime(2026, 1, 15, tzinfo=UTC)


def test_parse_since_empty_returns_none():
    assert trends.parse_since(None) is None
    assert trends.parse_since("") is None
    assert trends.parse_since("   ") is None


def test_parse_since_invalid_raises():
    with pytest.raises(ValueError):
        trends.parse_since("garbage")
    with pytest.raises(ValueError):
        trends.parse_since("2026-13-99")


# ---------- module_series --------------------------------------------------


def test_module_series_exact_path(tmp_db):
    _record_snapshot(tmp_db, "2026-05-01T00:00:00Z",
                     ("pkg/a.py", "pkg", 10, 8))
    _record_snapshot(tmp_db, "2026-05-15T00:00:00Z",
                     ("pkg/a.py", "pkg", 10, 9))
    series = trends.module_series(tmp_db, "pkg/a.py")
    assert len(series) == 2
    assert series[0]["pct"] == 80.0
    assert series[1]["pct"] == 90.0
    assert series[0]["recorded_at"] < series[1]["recorded_at"]


def test_module_series_directory_prefix(tmp_db):
    _record_snapshot(
        tmp_db, "2026-05-01T00:00:00Z",
        ("pkg/a.py", "pkg", 10, 8),
        ("pkg/b.py", "pkg", 10, 7),
        ("other/c.py", "other", 10, 10),
    )
    _record_snapshot(
        tmp_db, "2026-05-15T00:00:00Z",
        ("pkg/a.py", "pkg", 10, 9),
        ("pkg/b.py", "pkg", 10, 6),
        ("other/c.py", "other", 10, 10),
    )
    series = trends.module_series(tmp_db, "pkg")
    assert len(series) == 2
    # snapshot 1: 15/20 = 75%, snapshot 2: 15/20 = 75%
    assert series[0]["lines_total"] == 20
    assert series[0]["lines_covered"] == 15
    assert series[0]["pct"] == 75.0
    assert series[1]["lines_covered"] == 15


def test_module_series_package_match(tmp_db):
    _record_snapshot(
        tmp_db, "2026-05-01T00:00:00Z",
        ("src/x/foo.py", "x", 10, 8),
        ("src/y/bar.py", "y", 10, 9),
    )
    series = trends.module_series(tmp_db, "x")
    assert len(series) == 1
    # matches package "x" + path-prefix "x/..." → both should match the x rows
    assert series[0]["lines_covered"] == 8


def test_module_series_filters_by_since(tmp_db):
    _record_snapshot(tmp_db, "2026-01-01T00:00:00Z",
                     ("pkg/a.py", "pkg", 10, 9))
    _record_snapshot(tmp_db, "2026-05-15T00:00:00Z",
                     ("pkg/a.py", "pkg", 10, 8))
    base = datetime(2026, 5, 28, tzinfo=UTC)
    series = trends.module_series(
        tmp_db, "pkg/a.py", since=base - timedelta(days=30)
    )
    assert len(series) == 1
    assert series[0]["recorded_at"] == "2026-05-15T00:00:00Z"


def test_module_series_unknown_module_empty(tmp_db):
    series = trends.module_series(tmp_db, "does/not/exist")
    assert series == []


def test_list_known_modules(tmp_db):
    _record_snapshot(
        tmp_db, "2026-05-01T00:00:00Z",
        ("pkg/a.py", "pkg", 10, 8),
        ("pkg/b.py", "pkg", 10, 7),
        ("other/c.py", "other", 10, 10),
    )
    known = trends.list_known_modules(tmp_db)
    assert known["paths"] == ["other/c.py", "pkg/a.py", "pkg/b.py"]
    assert known["packages"] == ["other", "pkg"]


# ---------- detect_regression: empty/edge cases ---------------------------


def test_detect_regression_empty_series():
    info = trends.detect_regression([], threshold=2.0, window_days=30)
    assert info["regression"] is False
    assert info["current_pct"] is None
    assert info["window_max_pct"] is None
    assert info["delta_vs_window_max"] is None
    assert info["delta_vs_previous"] is None


def test_detect_regression_single_point():
    series = [{"recorded_at": "2026-05-15T00:00:00Z", "pct": 80.0,
               "lines_total": 100, "lines_covered": 80}]
    info = trends.detect_regression(series, threshold=2.0, window_days=30,
                                    now=datetime(2026, 5, 28, tzinfo=UTC))
    assert info["regression"] is False
    assert info["current_pct"] == 80.0
    assert info["delta_vs_previous"] is None
    # window_max equals current → delta is 0
    assert info["delta_vs_window_max"] == 0.0


# ---------- HEADLINE: silent regression over 20 snapshots ------------------


def test_detect_regression_silent_decline_over_20_snapshots(tmp_db):
    """The plan's headline behavior: ~1pt decline per snapshot over 20 snapshots.

    Each single-step delta is below the 2.0pp threshold (so per-commit checks
    would never alert), but the trailing-window max vs. latest is >> 2.0pp,
    so detect_regression flags it via delta_vs_window_max.
    """
    now = datetime(2026, 5, 28, tzinfo=UTC)

    for i in range(20):
        ts = now - timedelta(days=20 - i)  # 20 days ago → 1 day ago
        # 95.0% → 76.0%, 1pt per snapshot
        lt, lc = 100, 95 - i
        sid = db.insert_snapshot(tmp_db, recorded_at=_iso(ts))
        db.insert_modules(tmp_db, sid, [
            {"path": "pkg/slow.py", "package": "pkg",
             "lines_total": lt, "lines_covered": lc, "pct": float(lc)},
        ])

    series = trends.module_series(tmp_db, "pkg/slow.py")
    assert len(series) == 20

    info = trends.detect_regression(
        series, threshold=2.0, window_days=30, now=now,
    )
    # The headline: regression detected because window_max - current >= 2.0,
    # NOT because of a single-step delta.
    assert info["regression"] is True
    assert info["window_max_pct"] == 95.0
    assert info["current_pct"] == 76.0
    assert info["delta_vs_window_max"] == pytest.approx(-19.0)
    # Confirm the per-step delta is *below* threshold → silent.
    assert abs(info["delta_vs_previous"]) < 2.0


def test_detect_regression_below_threshold_not_flagged(tmp_db):
    """1pt total drop should NOT be flagged at threshold=2.0."""
    now = datetime(2026, 5, 28, tzinfo=UTC)
    for days_ago, lc in [(10, 90), (1, 89)]:
        sid = db.insert_snapshot(
            tmp_db, recorded_at=_iso(now - timedelta(days=days_ago))
        )
        db.insert_modules(tmp_db, sid, [
            {"path": "p/x.py", "package": "p",
             "lines_total": 100, "lines_covered": lc, "pct": float(lc)},
        ])
    series = trends.module_series(tmp_db, "p/x.py")
    info = trends.detect_regression(
        series, threshold=2.0, window_days=30, now=now,
    )
    assert info["regression"] is False
    assert info["delta_vs_window_max"] == pytest.approx(-1.0)


def test_detect_regression_window_excludes_old_max(tmp_db):
    """An old high-water mark outside the window should NOT count."""
    now = datetime(2026, 5, 28, tzinfo=UTC)
    # 90 days ago: 95%. 10 days ago: 90%. 1 day ago: 88%.
    # Within a 30-day window, max is 90% (not 95%) → drop is 2.0pp, flagged.
    for days_ago, lc in [(90, 95), (10, 90), (1, 88)]:
        sid = db.insert_snapshot(
            tmp_db, recorded_at=_iso(now - timedelta(days=days_ago))
        )
        db.insert_modules(tmp_db, sid, [
            {"path": "p/y.py", "package": "p",
             "lines_total": 100, "lines_covered": lc, "pct": float(lc)},
        ])
    series = trends.module_series(tmp_db, "p/y.py")
    info = trends.detect_regression(
        series, threshold=2.0, window_days=30, now=now,
    )
    assert info["window_max_pct"] == 90.0  # NOT 95.0
    assert info["regression"] is True
    assert info["delta_vs_window_max"] == pytest.approx(-2.0)


# ---------- worst_regressions ----------------------------------------------


def test_worst_regressions_returns_only_regressing(tmp_db):
    now = datetime(2026, 5, 28, tzinfo=UTC)
    sid1 = db.insert_snapshot(tmp_db,
                              recorded_at=_iso(now - timedelta(days=10)))
    db.insert_modules(tmp_db, sid1, [
        {"path": "stable.py", "package": "", "lines_total": 100,
         "lines_covered": 90, "pct": 90.0},
        {"path": "falling.py", "package": "", "lines_total": 100,
         "lines_covered": 95, "pct": 95.0},
        {"path": "tiny_dip.py", "package": "", "lines_total": 100,
         "lines_covered": 80, "pct": 80.0},
    ])
    sid2 = db.insert_snapshot(tmp_db,
                              recorded_at=_iso(now - timedelta(days=1)))
    db.insert_modules(tmp_db, sid2, [
        {"path": "stable.py", "package": "", "lines_total": 100,
         "lines_covered": 90, "pct": 90.0},
        {"path": "falling.py", "package": "", "lines_total": 100,
         "lines_covered": 70, "pct": 70.0},
        {"path": "tiny_dip.py", "package": "", "lines_total": 100,
         "lines_covered": 79, "pct": 79.0},  # 1pt dip, under threshold
    ])
    out = trends.worst_regressions(
        tmp_db, threshold=2.0, window_days=30, now=now,
    )
    names = [r["module"] for r in out]
    assert "falling.py" in names
    assert "stable.py" not in names
    assert "tiny_dip.py" not in names
    # Worst first
    assert out[0]["module"] == "falling.py"


def test_worst_regressions_respects_limit(tmp_db):
    now = datetime(2026, 5, 28, tzinfo=UTC)
    # 3 regressing modules; limit=1.
    for path in ("a.py", "b.py", "c.py"):
        sid1 = db.insert_snapshot(
            tmp_db, recorded_at=_iso(now - timedelta(days=10))
        )
        db.insert_modules(tmp_db, sid1, [
            {"path": path, "package": "", "lines_total": 100,
             "lines_covered": 95, "pct": 95.0},
        ])
        sid2 = db.insert_snapshot(
            tmp_db, recorded_at=_iso(now - timedelta(days=1, microseconds=int(ord(path[0]))))
        )
        db.insert_modules(tmp_db, sid2, [
            {"path": path, "package": "", "lines_total": 100,
             "lines_covered": 70, "pct": 70.0},
        ])
    out = trends.worst_regressions(
        tmp_db, threshold=2.0, window_days=30, limit=1, now=now,
    )
    assert len(out) == 1
