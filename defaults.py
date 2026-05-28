"""Shared default values and env-aware resolvers.

Centralizes the configuration constants previously duplicated across
`tools.py`, `hook.py`, `trends.py`, and `schemas.py`. A single edit here
shifts the defaults consistently for the LLM tools, the pre-LLM hook,
and the trend-math layer.

Standalone module: no Hermes imports, standard library only.
"""

from __future__ import annotations

import os
from typing import Callable, TypeVar

# Trend-math defaults.
REGRESSION_THRESHOLD: float = 2.0
WINDOW_DAYS: int = 30
LIMIT: int = 10
DEFAULT_SINCE: str = "30d"

# Env var names that override the trend-math defaults at call time.
ENV_THRESHOLD = "HERMES_COVERAGE_REGRESSION_THRESHOLD"
ENV_WINDOW_DAYS = "HERMES_COVERAGE_WINDOW_DAYS"

# Single source of truth for the ISO-8601 UTC timestamp format used when
# writing snapshots (`cli.py`) and rendering `since` lower bounds in SQL
# (`trends.py`).
ISO_Z_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


T = TypeVar("T")


def _env_number(var: str, default: T, cast: Callable[[str], T]) -> T:
    raw = os.environ.get(var, "")
    if not raw:
        return default
    try:
        return cast(raw)
    except ValueError:
        return default


def env_threshold() -> float:
    return _env_number(ENV_THRESHOLD, REGRESSION_THRESHOLD, float)


def env_window_days() -> int:
    return _env_number(ENV_WINDOW_DAYS, WINDOW_DAYS, int)
