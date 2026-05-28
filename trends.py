"""Trend analysis + silent-regression detection.

Standalone module: no Hermes imports here, so this can be unit-tested as
plain functions.

Per the plan (§3, §1.1):
- Aggregation is per snapshot, matching `path = m` OR `path LIKE m || '/%'`
  OR `package = m`.
- A module is **regressing** when its latest pct is below the **trailing
  window max** by at least `threshold` percentage points. The delta vs. the
  previous snapshot is reported as data but is *not* the alert trigger —
  that is the whole point of catching silent erosion.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable, List, Optional

from defaults import ISO_Z_FORMAT, REGRESSION_THRESHOLD, WINDOW_DAYS


# ---------- "since" parsing -------------------------------------------------


def parse_since(since: Optional[str], now: Optional[datetime] = None) -> Optional[datetime]:
    """Parse a `since` string into a UTC datetime, or None for no lower bound.

    Accepts:
      - None or empty / whitespace → None
      - "Nd" → N days back from `now`
      - "Nw" → N weeks back from `now`
      - "YYYY-MM-DD" or any ISO-8601 datetime → that instant (naive → UTC)

    Raises `ValueError` on anything else.
    """
    if since is None:
        return None
    s = since.strip()
    if not s:
        return None
    base = now or datetime.now(timezone.utc)
    last = s[-1].lower()
    if last == "d" and s[:-1].isdigit():
        return base - timedelta(days=int(s[:-1]))
    if last == "w" and s[:-1].isdigit():
        return base - timedelta(weeks=int(s[:-1]))
    try:
        dt = datetime.fromisoformat(s)
    except ValueError as e:
        raise ValueError(f"invalid 'since' value: {since!r}") from e
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# ---------- SQL / shape helpers --------------------------------------------


def _module_match_clause() -> str:
    # path = :m OR path LIKE :m || '/%' OR package = :m
    return "(modules.path = ? OR modules.path LIKE ? OR modules.package = ?)"


def _module_match_params(module: str):
    return (module, module + "/%", module)


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime(ISO_Z_FORMAT)


def _parse_recorded_at(s: str) -> Optional[datetime]:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
    except (ValueError, AttributeError):
        return None
    # Coerce naive timestamps to UTC so comparisons with aware datetimes
    # don't raise. The CLI always writes Z-suffixed (aware) timestamps,
    # but hand-edited or pre-existing rows might not.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _series_point(recorded_at: str, total, covered) -> dict:
    """Shape one series row from raw line totals.

    `pct` is recomputed from the (possibly summed) line totals so that
    package-level aggregates remain correctly weighted by file size.
    """
    total = int(total or 0)
    covered = int(covered or 0)
    pct = (100.0 * covered / total) if total > 0 else 0.0
    return {
        "recorded_at": recorded_at,
        "lines_total": total,
        "lines_covered": covered,
        "pct": round(pct, 4),
    }


# ---------- queries ---------------------------------------------------------


def module_series(
    conn,
    module: str,
    since: Optional[datetime] = None,
) -> List[dict]:
    """Per-snapshot aggregated coverage series for `module`.

    Each point is `{recorded_at, lines_total, lines_covered, pct}`, with `pct`
    recomputed from the aggregated line totals (so package-level aggregation
    is correct even when files have different sizes).
    """
    params: list = list(_module_match_params(module))
    sql = (
        "SELECT s.recorded_at AS recorded_at, "
        "       SUM(modules.lines_total)   AS lines_total, "
        "       SUM(modules.lines_covered) AS lines_covered "
        "FROM modules "
        "JOIN snapshots s ON s.id = modules.snapshot_id "
        "WHERE " + _module_match_clause()
    )
    if since is not None:
        sql += " AND s.recorded_at >= ?"
        params.append(_iso(since))
    sql += " GROUP BY s.id, s.recorded_at ORDER BY s.recorded_at ASC, s.id ASC"

    return [
        _series_point(row["recorded_at"], row["lines_total"], row["lines_covered"])
        for row in conn.execute(sql, params).fetchall()
    ]


def list_known_modules(conn) -> dict:
    """Distinct paths and (non-empty) packages seen across all snapshots."""
    paths = [
        row["path"]
        for row in conn.execute(
            "SELECT DISTINCT path FROM modules ORDER BY path"
        ).fetchall()
    ]
    packages = [
        row["package"]
        for row in conn.execute(
            "SELECT DISTINCT package FROM modules "
            "WHERE package IS NOT NULL AND package != '' "
            "ORDER BY package"
        ).fetchall()
    ]
    return {"paths": paths, "packages": packages}


# ---------- regression logic ------------------------------------------------


def _empty_verdict(threshold: float, window_days: int) -> dict:
    return {
        "current_pct": None,
        "window_max_pct": None,
        "delta_vs_window_max": None,
        "delta_vs_previous": None,
        "regression": False,
        "threshold": float(threshold),
        "window_days": int(window_days),
    }


def _window_max_pct(
    series: List[dict],
    now: Optional[datetime],
    window_days: int,
    fallback: float,
) -> float:
    """Max pct over points whose `recorded_at` is within the trailing window.

    Falls back to `fallback` (typically the current pct) if no point lies in
    the window — keeps the comparison well-defined when only old data exists.
    """
    base = now or datetime.now(timezone.utc)
    window_start = base - timedelta(days=window_days)
    in_window = [
        float(p["pct"])
        for p in series
        if (dt := _parse_recorded_at(p["recorded_at"])) is not None
        and dt >= window_start
    ]
    return max(in_window) if in_window else fallback


def detect_regression(
    series: Iterable[dict],
    threshold: float = REGRESSION_THRESHOLD,
    window_days: int = WINDOW_DAYS,
    now: Optional[datetime] = None,
) -> dict:
    """Compute window-max vs. latest, flag silent regression.

    A regression is `window_max_pct - current_pct >= threshold`. The single-
    step `delta_vs_previous` is reported but is not the alert trigger.
    """
    series = list(series)
    if not series:
        return _empty_verdict(threshold, window_days)

    current_pct = float(series[-1]["pct"])
    prev_pct = float(series[-2]["pct"]) if len(series) >= 2 else None
    window_max = _window_max_pct(series, now, window_days, fallback=current_pct)

    return {
        "current_pct":         round(current_pct, 4),
        "window_max_pct":      round(window_max, 4),
        "delta_vs_window_max": round(current_pct - window_max, 4),
        "delta_vs_previous":   None if prev_pct is None else round(current_pct - prev_pct, 4),
        "regression":          (window_max - current_pct) >= float(threshold),
        "threshold":           float(threshold),
        "window_days":         int(window_days),
    }


def worst_regressions(
    conn,
    since: Optional[datetime] = None,
    threshold: float = REGRESSION_THRESHOLD,
    window_days: int = WINDOW_DAYS,
    limit: int = 10,
    now: Optional[datetime] = None,
) -> List[dict]:
    """Scan all known module paths and return those currently regressing.

    Single SQL pass over `modules` + grouping in Python, instead of N+1
    queries (one per path).
    """
    params: list = []
    sql = (
        "SELECT modules.path        AS path, "
        "       s.recorded_at       AS recorded_at, "
        "       s.id                AS snapshot_id, "
        "       modules.lines_total AS lines_total, "
        "       modules.lines_covered AS lines_covered "
        "FROM modules "
        "JOIN snapshots s ON s.id = modules.snapshot_id"
    )
    if since is not None:
        sql += " WHERE s.recorded_at >= ?"
        params.append(_iso(since))
    sql += " ORDER BY modules.path ASC, s.recorded_at ASC, s.id ASC"

    series_by_path: dict[str, List[dict]] = {}
    for row in conn.execute(sql, params).fetchall():
        point = _series_point(row["recorded_at"], row["lines_total"], row["lines_covered"])
        series_by_path.setdefault(row["path"], []).append(point)

    flagged: List[dict] = []
    for path, series in series_by_path.items():
        verdict = detect_regression(
            series, threshold=threshold, window_days=window_days, now=now,
        )
        if verdict["regression"]:
            flagged.append({"module": path, "samples": len(series), **verdict})

    # Most negative delta_vs_window_max first. Items here always satisfied
    # `regression=True`, which means `delta_vs_window_max` is set.
    flagged.sort(key=lambda r: r["delta_vs_window_max"])
    if isinstance(limit, int) and limit >= 0:
        flagged = flagged[:limit]
    return flagged
