"""Phase 2: LLM tool handlers always return JSON, never raise."""

import json
from datetime import datetime, timedelta, timezone

import db
import tools


UTC = timezone.utc


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _seed(conn, recorded_at, path, lt, lc, package=""):
    sid = db.insert_snapshot(conn, recorded_at=recorded_at)
    db.insert_modules(conn, sid, [{
        "path": path, "package": package or path.split("/")[0],
        "lines_total": lt, "lines_covered": lc,
        "pct": (100.0 * lc / lt) if lt else 0.0,
    }])
    # Tool handlers open their own connection; commit so they can see the seed.
    conn.commit()


# ---------- coverage_trend -------------------------------------------------


def test_coverage_trend_returns_json_string(tmp_db, hermes_home):
    now = datetime.now(UTC)
    _seed(tmp_db, _iso(now - timedelta(days=5)), "pkg/x.py", 10, 9)
    _seed(tmp_db, _iso(now - timedelta(days=1)), "pkg/x.py", 10, 7)

    out = tools.coverage_trend({"module": "pkg/x.py", "since": "30d"})
    assert isinstance(out, str)
    payload = json.loads(out)

    assert payload["module"] == "pkg/x.py"
    assert payload["samples"] == 2
    assert isinstance(payload["series"], list)
    assert payload["regression"] is True
    assert payload["window_days"] == 30
    assert payload["threshold"] == 2.0


def test_coverage_trend_unknown_module_returns_empty_series(tmp_db, hermes_home):
    out = tools.coverage_trend({"module": "does/not/exist"})
    payload = json.loads(out)
    assert payload["series"] == []
    assert payload["samples"] == 0
    assert payload["regression"] is False
    assert payload["current_pct"] is None


def test_coverage_trend_missing_module_returns_error(hermes_home):
    out = tools.coverage_trend({})
    payload = json.loads(out)
    assert "error" in payload
    assert "module" in payload["error"]


def test_coverage_trend_blank_module_returns_error(hermes_home):
    out = tools.coverage_trend({"module": "   "})
    payload = json.loads(out)
    assert "error" in payload


def test_coverage_trend_invalid_since_returns_error(hermes_home):
    out = tools.coverage_trend({"module": "x", "since": "garbage"})
    payload = json.loads(out)
    assert "error" in payload


def test_coverage_trend_non_dict_args_returns_json_error(hermes_home):
    # Per §2.5: never raise. Pass something pathological.
    for bad in ("not a dict", None, 42, ["a", "b"]):
        out = tools.coverage_trend(bad)
        payload = json.loads(out)
        assert "error" in payload


def test_coverage_trend_threshold_override(tmp_db, hermes_home):
    now = datetime.now(UTC)
    _seed(tmp_db, _iso(now - timedelta(days=5)), "pkg/x.py", 100, 95)
    _seed(tmp_db, _iso(now - timedelta(days=1)), "pkg/x.py", 100, 80)
    # Default threshold=2.0 → regression. Override to 100.0 → not flagged.
    out = tools.coverage_trend(
        {"module": "pkg/x.py", "since": "30d", "threshold": 100.0}
    )
    payload = json.loads(out)
    assert payload["threshold"] == 100.0
    assert payload["regression"] is False


def test_coverage_trend_window_days_override(tmp_db, hermes_home):
    now = datetime.now(UTC)
    _seed(tmp_db, _iso(now - timedelta(days=60)), "pkg/x.py", 100, 95)
    _seed(tmp_db, _iso(now - timedelta(days=10)), "pkg/x.py", 100, 90)
    _seed(tmp_db, _iso(now - timedelta(days=1)), "pkg/x.py", 100, 88)
    out = tools.coverage_trend(
        {"module": "pkg/x.py", "since": "365d", "window_days": 30}
    )
    payload = json.loads(out)
    assert payload["window_days"] == 30
    assert payload["window_max_pct"] == 90.0  # NOT 95.0 (old)


# ---------- coverage_regressions -------------------------------------------


def test_coverage_regressions_returns_json(tmp_db, hermes_home):
    now = datetime.now(UTC)
    _seed(tmp_db, _iso(now - timedelta(days=10)), "a.py", 100, 95)
    _seed(tmp_db, _iso(now - timedelta(days=1)), "a.py", 100, 70)
    out = tools.coverage_regressions({"since": "30d"})
    payload = json.loads(out)
    assert isinstance(payload["regressions"], list)
    assert payload["count"] >= 1
    assert payload["since"] == "30d"


def test_coverage_regressions_empty_db(hermes_home):
    out = tools.coverage_regressions({})
    payload = json.loads(out)
    assert payload["regressions"] == []
    assert payload["count"] == 0


def test_coverage_regressions_env_threshold_override(
    tmp_db, hermes_home, monkeypatch
):
    monkeypatch.setenv("HERMES_COVERAGE_REGRESSION_THRESHOLD", "50.0")
    now = datetime.now(UTC)
    _seed(tmp_db, _iso(now - timedelta(days=10)), "a.py", 100, 95)
    _seed(tmp_db, _iso(now - timedelta(days=1)), "a.py", 100, 70)
    out = tools.coverage_regressions({"since": "30d"})
    payload = json.loads(out)
    # 25pt drop, but env threshold is 50pt → not flagged
    assert payload["count"] == 0
    assert payload["threshold"] == 50.0


def test_coverage_regressions_invalid_since_returns_error(hermes_home):
    out = tools.coverage_regressions({"since": "garbage"})
    payload = json.loads(out)
    assert "error" in payload


def test_coverage_regressions_non_dict_returns_json_error(hermes_home):
    out = tools.coverage_regressions(None)
    payload = json.loads(out)
    assert "error" in payload


def test_coverage_regressions_limit_honored(tmp_db, hermes_home):
    now = datetime.now(UTC)
    for p, drop in [("a.py", 25), ("b.py", 20), ("c.py", 15)]:
        _seed(tmp_db, _iso(now - timedelta(days=10)), p, 100, 95)
        _seed(tmp_db, _iso(now - timedelta(days=1)), p, 100, 95 - drop)
    out = tools.coverage_regressions({"since": "30d", "limit": 2})
    payload = json.loads(out)
    assert payload["count"] == 2


def test_coverage_regressions_limit_zero_returns_no_rows(tmp_db, hermes_home):
    """H3: limit=0 should return 0 rows, not silently return all rows."""
    now = datetime.now(UTC)
    for p in ("a.py", "b.py"):
        _seed(tmp_db, _iso(now - timedelta(days=10)), p, 100, 95)
        _seed(tmp_db, _iso(now - timedelta(days=1)), p, 100, 70)
    out = tools.coverage_regressions({"since": "30d", "limit": 0})
    payload = json.loads(out)
    assert payload["count"] == 0
    assert payload["regressions"] == []


def test_coverage_regressions_negative_limit_clamps_to_zero(
    tmp_db, hermes_home
):
    """H3: negative limit should clamp to 0, not silently return all rows."""
    now = datetime.now(UTC)
    _seed(tmp_db, _iso(now - timedelta(days=10)), "a.py", 100, 95)
    _seed(tmp_db, _iso(now - timedelta(days=1)), "a.py", 100, 70)
    out = tools.coverage_regressions({"since": "30d", "limit": -5})
    payload = json.loads(out)
    assert payload["count"] == 0
