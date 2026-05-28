"""LLM tool handlers for coverage_trend and coverage_regressions.

Per §2.5: handlers **always** return a JSON string and **never** raise — on
success and on every error path.
"""

from __future__ import annotations

import functools
import json
import logging
from typing import Callable

import db
import defaults
import trends

logger = logging.getLogger(__name__)


# ---------- shared scaffolding ---------------------------------------------


def _resolve_threshold(args: dict) -> float:
    v = args.get("threshold")
    if v is None:
        return defaults.env_threshold()
    try:
        return float(v)
    except (TypeError, ValueError):
        return defaults.env_threshold()


def _resolve_window_days(args: dict) -> int:
    v = args.get("window_days")
    if v is None:
        return defaults.env_window_days()
    try:
        return int(v)
    except (TypeError, ValueError):
        return defaults.env_window_days()


def _resolve_limit(args: dict) -> int:
    raw = args.get("limit", defaults.LIMIT)
    try:
        limit = int(raw) if raw is not None else defaults.LIMIT
    except (TypeError, ValueError):
        return defaults.LIMIT
    # `limit=0` means "return zero rows" (the natural interpretation), and
    # negatives clamp to zero. Without this, the worst_regressions slice
    # falls through and silently returns everything.
    return max(limit, 0)


def _json_tool(fn: Callable[..., dict]) -> Callable[..., str]:
    """Wrap a handler so it always returns a JSON string and never raises.

    The wrapped function should take `(args: dict, **kwargs)` and return a
    dict; this decorator handles input validation, JSON encoding, and the
    catch-all error path (logging the traceback before discarding it).
    """
    @functools.wraps(fn)
    def wrapper(args, **kwargs) -> str:
        try:
            if not isinstance(args, dict):
                return json.dumps({"error": "args must be a JSON object"})
            return json.dumps(fn(args, **kwargs))
        except ValueError as e:
            return json.dumps({"error": str(e)})
        except Exception as e:
            logger.exception("tool %s failed", fn.__name__)
            return json.dumps({"error": str(e)})
    return wrapper


# ---------- handlers --------------------------------------------------------


@_json_tool
def coverage_trend(args: dict, **kwargs) -> dict:
    module = args.get("module")
    if not isinstance(module, str) or not module.strip():
        raise ValueError("module is required")
    module = module.strip()

    since_str = args.get("since", defaults.DEFAULT_SINCE)
    threshold = _resolve_threshold(args)
    window_days = _resolve_window_days(args)
    since = trends.parse_since(since_str)

    with db.session() as conn:
        series = trends.module_series(conn, module, since)

    verdict = trends.detect_regression(
        series, threshold=threshold, window_days=window_days
    )
    return {
        "module": module,
        "since": since_str,
        "samples": len(series),
        "series": series,
        **verdict,
    }


@_json_tool
def coverage_regressions(args: dict, **kwargs) -> dict:
    since_str = args.get("since", defaults.DEFAULT_SINCE)
    threshold = _resolve_threshold(args)
    window_days = _resolve_window_days(args)
    limit = _resolve_limit(args)
    since = trends.parse_since(since_str)

    with db.session() as conn:
        regressions = trends.worst_regressions(
            conn,
            since=since,
            threshold=threshold,
            window_days=window_days,
            limit=limit,
        )

    return {
        "since": since_str,
        "threshold": threshold,
        "window_days": window_days,
        "count": len(regressions),
        "regressions": regressions,
    }
