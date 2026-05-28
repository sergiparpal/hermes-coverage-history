"""Phase 3: pre_llm_call selective injection tests."""

import db
import hook


def _seed_module(conn, path="pkg_a/foo.py", package="pkg_a", lt=10, lc=9):
    sid = db.insert_snapshot(conn, recorded_at="2026-05-28T10:00:00Z")
    db.insert_modules(conn, sid, [{
        "path": path, "package": package,
        "lines_total": lt, "lines_covered": lc,
        "pct": 100.0 * lc / lt,
    }])


def test_returns_none_for_empty_or_none_message(hermes_home):
    assert hook.inject_coverage_summary(user_message="") is None
    assert hook.inject_coverage_summary(user_message=None) is None


def test_returns_none_without_coverage_keyword(tmp_db, hermes_home):
    _seed_module(tmp_db)
    out = hook.inject_coverage_summary(
        user_message="how is pkg_a/foo.py doing today?"
    )
    assert out is None


def test_returns_none_when_no_known_module_mentioned(tmp_db, hermes_home):
    _seed_module(tmp_db)
    out = hook.inject_coverage_summary(
        user_message="what's the coverage trend for some_random_thing?"
    )
    assert out is None


def test_returns_none_when_db_empty(hermes_home):
    """With no recorded snapshots, even a perfect message yields no injection."""
    out = hook.inject_coverage_summary(
        user_message="show me coverage for pkg_a/foo.py"
    )
    assert out is None


def test_returns_context_on_full_path_match(tmp_db, hermes_home):
    _seed_module(tmp_db)
    out = hook.inject_coverage_summary(
        user_message="What's the coverage trend on pkg_a/foo.py?"
    )
    assert isinstance(out, dict)
    assert "context" in out
    assert "pkg_a/foo.py" in out["context"]
    assert "current=" in out["context"]


def test_returns_context_on_filename_tail_match(tmp_db, hermes_home):
    _seed_module(tmp_db)
    out = hook.inject_coverage_summary(
        user_message="how's coverage looking for foo.py these days?"
    )
    assert isinstance(out, dict)
    assert "pkg_a/foo.py" in out["context"]


def test_returns_context_on_package_match(tmp_db, hermes_home):
    _seed_module(tmp_db)
    out = hook.inject_coverage_summary(
        user_message="any cobertura issues in pkg_a?"
    )
    assert isinstance(out, dict)
    assert "pkg_a" in out["context"]


def test_prefers_specific_path_over_package(tmp_db, hermes_home):
    _seed_module(tmp_db, path="pkg_a/foo.py", package="pkg_a")
    _seed_module(tmp_db, path="pkg_a/bar.py", package="pkg_a")
    out = hook.inject_coverage_summary(
        user_message="coverage of pkg_a/foo.py please"
    )
    # Should match the full path, not the package
    assert "pkg_a/foo.py" in out["context"]


def test_keyword_is_case_insensitive(tmp_db, hermes_home):
    _seed_module(tmp_db)
    out = hook.inject_coverage_summary(
        user_message="Coverage summary on pkg_a/foo.py?"
    )
    assert out is not None


def test_hook_accepts_forward_compat_kwargs(tmp_db, hermes_home):
    _seed_module(tmp_db)
    out = hook.inject_coverage_summary(
        user_message="coverage of pkg_a/foo.py",
        session_id="abc",
        conversation_history=[{"role": "user", "content": "hi"}],
        is_first_turn=True,
        model="claude-opus-4-7",
        platform="cli",
        future_unknown_kwarg="zzz",
    )
    assert out is not None


def test_hook_swallows_exceptions(monkeypatch, hermes_home):
    """If anything internal blows up, return None — never raise."""
    def boom(*a, **k):
        raise RuntimeError("simulated failure")
    monkeypatch.setattr(hook.db, "connect", boom)
    out = hook.inject_coverage_summary(
        user_message="show coverage of pkg_a/foo.py"
    )
    assert out is None
