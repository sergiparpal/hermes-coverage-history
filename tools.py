"""LLM tool handlers for coverage_trend and coverage_regressions.

Per §2.5: handlers **always** return a JSON string and **never** raise — on
success and on every error path.
"""

from __future__ import annotations

import json
import os

import db
import trends


_DEFAULT_THRESHOLD = 2.0
_DEFAULT_WINDOW_DAYS = 30
_DEFAULT_LIMIT = 10


def _env_threshold() -> float:
    v = os.environ.get("HERMES_COVERAGE_REGRESSION_THRESHOLD")
    if v is None or v == "":
        return _DEFAULT_THRESHOLD
    try:
        return float(v)
    except ValueError:
        return _DEFAULT_THRESHOLD


def _env_window_days() -> int:
    v = os.environ.get("HERMES_COVERAGE_WINDOW_DAYS")
    if v is None or v == "":
        return _DEFAULT_WINDOW_DAYS
    try:
        return int(v)
    except ValueError:
        return _DEFAULT_WINDOW_DAYS


def _resolve_threshold(args: dict) -> float:
    v = args.get("threshold")
    if v is None:
        return _env_threshold()
    try:
        return float(v)
    except (TypeError, ValueError):
        return _env_threshold()


def _resolve_window_days(args: dict) -> int:
    v = args.get("window_days")
    if v is None:
        return _env_window_days()
    try:
        return int(v)
    except (TypeError, ValueError):
        return _env_window_days()


def coverage_trend(args, **kwargs) -> str:
    try:
        if not isinstance(args, dict):
            return json.dumps({"error": "args must be a JSON object"})
        module = (args.get("module") or "").strip()
        if not module:
            return json.dumps({"error": "module is required"})

        since_str = args.get("since", "30d")
        threshold = _resolve_threshold(args)
        window_days = _resolve_window_days(args)

        try:
            since = trends.parse_since(since_str)
        except ValueError as e:
            return json.dumps({"error": str(e)})

        conn = db.connect()
        try:
            series = trends.module_series(conn, module, since)
        finally:
            conn.close()

        info = trends.detect_regression(
            series, threshold=threshold, window_days=window_days
        )
        return json.dumps(
            {
                "module": module,
                "since": since_str,
                "samples": len(series),
                "series": series,
                **info,
            }
        )
    except Exception as e:
        return json.dumps({"error": str(e)})


def coverage_regressions(args, **kwargs) -> str:
    try:
        if not isinstance(args, dict):
            return json.dumps({"error": "args must be a JSON object"})
        since_str = args.get("since", "30d")
        threshold = _resolve_threshold(args)
        window_days = _resolve_window_days(args)
        limit_in = args.get("limit", _DEFAULT_LIMIT)
        try:
            limit = int(limit_in) if limit_in is not None else _DEFAULT_LIMIT
        except (TypeError, ValueError):
            limit = _DEFAULT_LIMIT

        try:
            since = trends.parse_since(since_str)
        except ValueError as e:
            return json.dumps({"error": str(e)})

        conn = db.connect()
        try:
            rows = trends.worst_regressions(
                conn,
                since=since,
                threshold=threshold,
                window_days=window_days,
                limit=limit,
            )
        finally:
            conn.close()

        return json.dumps(
            {
                "since": since_str,
                "threshold": threshold,
                "window_days": window_days,
                "count": len(rows),
                "regressions": rows,
            }
        )
    except Exception as e:
        return json.dumps({"error": str(e)})
