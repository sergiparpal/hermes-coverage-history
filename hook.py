"""Selective `pre_llm_call` hook.

The hook injects context **only** when:
  1. The user's message mentions "coverage" or "cobertura", AND
  2. A known module path or package name is referenced in the message.

This keeps injection rare and prompt-cache-friendly — coverage context only
shows up when the user is actually talking about coverage of something we
know about.

Per §2.6:
- Always accept **kwargs for forward compatibility.
- Return `{"context": "..."}`, a non-empty string, or `None`.
- Never raise; degrade to `None` on any error.
"""

from __future__ import annotations

import re
from typing import Optional

import db
import trends


_COVERAGE_RE = re.compile(r"\b(coverage|cobertura)\b", re.IGNORECASE)
_TOKEN_RE = re.compile(r"[A-Za-z0-9_./-]+")


def inject_coverage_summary(
    session_id=None,
    user_message: str = "",
    conversation_history=None,
    is_first_turn: bool = False,
    model=None,
    platform=None,
    **kwargs,
):
    try:
        if not user_message:
            return None
        if not _COVERAGE_RE.search(user_message):
            return None

        conn = db.connect()
        try:
            known = trends.list_known_modules(conn)
            module = _match_known_module(user_message, known)
            if not module:
                return None
            since = trends.parse_since("30d")
            series = trends.module_series(conn, module, since=since)
        finally:
            conn.close()

        if not series:
            return None

        info = trends.detect_regression(series, threshold=2.0, window_days=30)
        if info["current_pct"] is None:
            return None

        summary = _format_summary(module, series, info)
        if not summary:
            return None
        return {"context": summary}
    except Exception:
        return None


def _match_known_module(message: str, known: dict) -> Optional[str]:
    """Return the most specific known module the message references, or None."""
    tokens = set(_TOKEN_RE.findall(message))
    candidates = []
    for p in known.get("paths", []):
        if not p:
            continue
        # Full path mention, or the tail (e.g. "foo.py") as a standalone token.
        if p in message:
            candidates.append(p)
            continue
        tail = p.rsplit("/", 1)[-1]
        if tail and tail in tokens:
            candidates.append(p)
    for pkg in known.get("packages", []):
        if not pkg:
            continue
        if pkg in message or pkg in tokens:
            candidates.append(pkg)
    if not candidates:
        return None
    # Prefer the longest (most specific) match — full paths win over packages.
    candidates.sort(key=len, reverse=True)
    return candidates[0]


def _format_summary(module: str, series, info) -> str:
    current = info["current_pct"]
    window_max = info["window_max_pct"]
    delta = info["delta_vs_window_max"]
    parts = [
        f"current={current:.2f}%",
        f"samples={len(series)}",
    ]
    if window_max is not None:
        parts.append(f"window_max={window_max:.2f}%")
    if delta is not None:
        parts.append(f"delta_vs_window_max={delta:+.2f}pp")
    if info.get("regression"):
        parts.append("regression=YES")
    return f"Coverage summary for {module}: " + ", ".join(parts)
