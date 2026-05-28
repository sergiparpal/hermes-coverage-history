"""Selective `pre_llm_call` hook.

The hook injects context **only** when:
  1. The user's message mentions "coverage" or "cobertura", AND
  2. A known module path or package name is referenced in the message
     (full-path substring, OR token match for the basename / package).

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
import defaults
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

        threshold = defaults.env_threshold()
        window_days = defaults.env_window_days()

        with db.session() as conn:
            known = trends.list_known_modules(conn)
            module = _match_known_module(user_message, known)
            if not module:
                return None
            since = trends.parse_since(defaults.DEFAULT_SINCE)
            series = trends.module_series(conn, module, since=since)

        if not series:
            return None

        verdict = trends.detect_regression(
            series, threshold=threshold, window_days=window_days,
        )
        if verdict["current_pct"] is None:
            return None

        summary = _format_summary(module, series, verdict)
        if not summary:
            return None
        return {"context": summary}
    except Exception:
        return None


def _match_known_module(message: str, known: dict) -> Optional[str]:
    """Return the most specific known module the message references, or None.

    Matching rules:
      - A stored *path* matches if it appears as a substring anywhere in the
        message, OR its basename appears as a standalone token. Path strings
        are distinctive (they contain `/` or `.`), so substring matches are
        safe.
      - A stored *package* matches only as a standalone token — substring
        matching short package names (e.g. `"x"`) would fire on any word
        that contains those letters (`"expat"` etc.).
      - If two candidates tie at the longest length, the reference is
        ambiguous and we skip injection rather than guess.
    """
    tokens = set(_TOKEN_RE.findall(message))
    candidates: list[str] = []
    for path in known.get("paths", []):
        if not path:
            continue
        if path in message:
            candidates.append(path)
            continue
        tail = path.rsplit("/", 1)[-1]
        if tail and tail in tokens:
            candidates.append(path)
    for pkg in known.get("packages", []):
        if not pkg:
            continue
        if pkg in tokens:
            candidates.append(pkg)

    if not candidates:
        return None
    # Deduplicate while preserving first-seen order, then prefer the longest
    # (most specific) match. Two-way ties at the top → ambiguous → skip.
    unique = list(dict.fromkeys(candidates))
    unique.sort(key=len, reverse=True)
    if len(unique) >= 2 and len(unique[0]) == len(unique[1]):
        return None
    return unique[0]


def _format_summary(module: str, series, verdict) -> str:
    current = verdict["current_pct"]
    window_max = verdict["window_max_pct"]
    delta = verdict["delta_vs_window_max"]
    safe_module = _sanitize_for_prompt(module)
    summary_fields = [
        f"current={current:.2f}%",
        f"samples={len(series)}",
    ]
    if window_max is not None:
        summary_fields.append(f"window_max={window_max:.2f}%")
    if delta is not None:
        summary_fields.append(f"delta_vs_window_max={delta:+.2f}pp")
    if verdict.get("regression"):
        summary_fields.append("regression=YES")
    return f"Coverage summary for {safe_module}: " + ", ".join(summary_fields)


def _sanitize_for_prompt(value: str, *, max_len: int = 256) -> str:
    """Strip control characters and cap length before embedding a stored
    value into the LLM-visible prompt context.

    Defence in depth against indirect prompt injection: even if a poisoned
    Cobertura row slipped past the parser-side validation, it cannot
    shape the prompt across newlines or carry a long instruction payload.
    """
    cleaned = "".join(c for c in value if ord(c) >= 0x20 and ord(c) != 0x7f)
    return cleaned[:max_len]
