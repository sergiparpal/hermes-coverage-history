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


# ---------- "since" parsing -------------------------------------------------


def parse_since(since: Optional[str], now: Optional[datetime] = None) -> Optional[datetime]:
    """Parse a `since` string into a UTC datetime, or None for no lower bound.

    Accepts:
      - None or empty / whitespace → None
      - "Nd" → N days back from `now`
      - "Nw" → N weeks back from `now`
      - "YYYY-MM-DD" → midnight UTC on that date

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
        dt = datetime.strptime(s, "%Y-%m-%d")
        return dt.replace(tzinfo=timezone.utc)
    except ValueError as e:
        raise ValueError(f"invalid 'since' value: {since!r}") from e


# ---------- SQL helpers -----------------------------------------------------


def _module_match_clause() -> str:
    # path = :m OR path LIKE :m || '/%' OR package = :m
    return "(modules.path = ? OR modules.path LIKE ? OR modules.package = ?)"


def _module_match_params(module: str):
    return (module, module + "/%", module)


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_recorded_at(s: str) -> Optional[datetime]:
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except (ValueError, AttributeError):
        return None


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

    rows = conn.execute(sql, params).fetchall()
    series: List[dict] = []
    for r in rows:
        total = int(r["lines_total"] or 0)
        covered = int(r["lines_covered"] or 0)
        pct = (100.0 * covered / total) if total > 0 else 0.0
        series.append(
            {
                "recorded_at": r["recorded_at"],
                "lines_total": total,
                "lines_covered": covered,
                "pct": round(pct, 4),
            }
        )
    return series


def list_known_modules(conn) -> dict:
    """Distinct paths and (non-empty) packages seen across all snapshots."""
    paths = [
        r["path"]
        for r in conn.execute(
            "SELECT DISTINCT path FROM modules ORDER BY path"
        ).fetchall()
    ]
    packages = [
        r["package"]
        for r in conn.execute(
            "SELECT DISTINCT package FROM modules "
            "WHERE package IS NOT NULL AND package != '' "
            "ORDER BY package"
        ).fetchall()
    ]
    return {"paths": paths, "packages": packages}


# ---------- regression logic ------------------------------------------------


def detect_regression(
    series: Iterable[dict],
    threshold: float = 2.0,
    window_days: int = 30,
    now: Optional[datetime] = None,
) -> dict:
    """Compute window-max vs. latest, flag silent regression.

    A regression is `window_max_pct - current_pct >= threshold`. The single-
    step `delta_vs_previous` is reported but is not the alert trigger.
    """
    series = list(series)
    result = {
        "current_pct": None,
        "window_max_pct": None,
        "delta_vs_window_max": None,
        "delta_vs_previous": None,
        "regression": False,
        "threshold": float(threshold),
        "window_days": int(window_days),
    }
    if not series:
        return result

    base = now or datetime.now(timezone.utc)
    window_start = base - timedelta(days=window_days)

    current = series[-1]
    current_pct = float(current["pct"])
    result["current_pct"] = round(current_pct, 4)

    if len(series) >= 2:
        prev_pct = float(series[-2]["pct"])
        result["delta_vs_previous"] = round(current_pct - prev_pct, 4)

    window_points = []
    for point in series:
        dt = _parse_recorded_at(point["recorded_at"])
        if dt is None:
            continue
        if dt >= window_start:
            window_points.append(point)
    # If no point fits in the window (e.g. only old data with a far-back since),
    # fall back to the current point so the comparison is well-defined.
    if not window_points:
        window_points = [current]

    window_max = max(float(p["pct"]) for p in window_points)
    result["window_max_pct"] = round(window_max, 4)
    result["delta_vs_window_max"] = round(current_pct - window_max, 4)
    result["regression"] = (window_max - current_pct) >= float(threshold)
    return result


def worst_regressions(
    conn,
    since: Optional[datetime] = None,
    threshold: float = 2.0,
    window_days: int = 30,
    limit: int = 10,
    now: Optional[datetime] = None,
) -> List[dict]:
    """Scan all known module paths and return those currently regressing."""
    paths = [
        r["path"]
        for r in conn.execute(
            "SELECT DISTINCT path FROM modules ORDER BY path"
        ).fetchall()
    ]
    out: List[dict] = []
    for path in paths:
        series = module_series(conn, path, since=since)
        if not series:
            continue
        info = detect_regression(
            series, threshold=threshold, window_days=window_days, now=now,
        )
        if info["regression"]:
            out.append({"module": path, "samples": len(series), **info})
    # Sort by most negative delta_vs_window_max first.
    out.sort(key=lambda r: (r["delta_vs_window_max"] or 0.0))
    if limit and limit > 0:
        out = out[:limit]
    return out
