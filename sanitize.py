"""Output-side text hygiene for DB-sourced strings.

Standalone module: no Hermes imports, standard library only.

A single primitive shared by the LLM tool handlers (`tools.py`) and the
`pre_llm_call` hook (`hook.py`): scrub control characters and cap length on a
stored string before it crosses into LLM-visible output.

This is defence in depth against indirect prompt injection. The Cobertura
parser already rejects pathological `filename` / `package` values at ingestion
(see `parser._is_safe_field`), but a row poisoned with newlines or
instruction-shaped prose that predated that validation must still be unable to
reshape the prompt by the time it leaves the plugin.
"""

from __future__ import annotations


def sanitize_text(value: str, *, max_len: int = 512) -> str:
    """Strip control characters (and DEL) and cap length on `value`.

    Non-string input is returned unchanged, so callers can route a value
    through defensively without a separate type check.
    """
    if not isinstance(value, str):
        return value
    cleaned = "".join(c for c in value if ord(c) >= 0x20 and ord(c) != 0x7f)
    return cleaned[:max_len]
