# hermes-coverage-history — Manual

Persistent per-module test coverage history with silent-regression detection.

## What problem it solves

Per-commit coverage gates only catch big single-commit drops. A module
that loses 1% per week to silent rot will pass every gate yet hemorrhage
coverage over time. This plugin records a history and compares the latest
snapshot against the **trailing window's maximum**, so slow erosion is
detected the moment the cumulative drop crosses the threshold.

## What it does

- Ingests **Cobertura XML** coverage reports via a CLI command.
- Stores **per-file** coverage (one row per `<class filename=...>`).
- Aggregates up to a package or directory prefix **at query time** — never
  baked into the table.
- Surfaces silent coverage regression via two read-only LLM tools and an
  optional context-injection hook.

## Storage

DB path: `<HERMES_HOME>/coverage-history/coverage_history.db`.
`HERMES_HOME` defaults to `~/.hermes`.

Schema (simplified):

```
snapshots(id, recorded_at, commit_sha, label, source_path)
modules(snapshot_id, path, package, lines_total, lines_covered, pct)
```

Indexed by `modules.path`, `modules.snapshot_id`, and `snapshots.recorded_at`.
WAL mode is enabled so a CI ingestion job doesn't block agent reads.

## Ingestion (CLI)

```
hermes coverage-history record <report.xml> [--sha SHA] [--label LABEL]
```

`recorded_at` is the **ingestion** timestamp in UTC, not the underlying CI
run time. Pass `--sha` if you want to correlate snapshots with commits.

Ingestion is **deliberately CLI-only** — the LLM cannot mutate history,
only read it. Run from CI or cron.

## Aggregation rule

When you query for a module `m`, rows match if any of these is true:

- `modules.path = m` (exact file path)
- `modules.path LIKE m || '/%'` (directory prefix)
- `modules.package = m` (Cobertura package name)

`pct` for a multi-row aggregate is **recomputed** from summed line totals,
not averaged. So `pct(pkg) = 100 * SUM(lines_covered) / SUM(lines_total)`
across files in `pkg`, properly weighted by file size.

## LLM tools

Both handlers always return a JSON string. They never raise — on error,
they return `{"error": "..."}`.

### `coverage_trend(module, since="30d", threshold?, window_days?)`

Returns the per-snapshot series plus a regression verdict.

**Arguments**

- `module` *(required)* — file path (`pkg_a/foo.py`), directory prefix
  (`pkg_a`), or Cobertura package name.
- `since` — `"Nd"` / `"Nw"` / `"YYYY-MM-DD"` / omit. Default `"30d"`.
- `threshold` — pp drop vs. window max that flags regression. Default
  from `HERMES_COVERAGE_REGRESSION_THRESHOLD` or `2.0`.
- `window_days` — trailing window. Default from
  `HERMES_COVERAGE_WINDOW_DAYS` or `30`.

**Response**

```json
{
  "module": "pkg_a/foo.py",
  "since": "30d",
  "samples": 12,
  "series": [
    {"recorded_at": "2026-05-01T00:00:00Z",
     "pct": 92.4, "lines_total": 200, "lines_covered": 185},
    ...
  ],
  "current_pct": 88.1,
  "window_max_pct": 92.4,
  "delta_vs_window_max": -4.3,
  "delta_vs_previous": -0.5,
  "regression": true,
  "threshold": 2.0,
  "window_days": 30
}
```

### `coverage_regressions(since="30d", threshold?, window_days?, limit?)`

Returns the worst regressing modules.

**Arguments** — same defaults as `coverage_trend`. `limit` defaults to `10`.

**Response**

```json
{
  "since": "30d",
  "threshold": 2.0,
  "window_days": 30,
  "count": 3,
  "regressions": [
    {
      "module": "pkg/eroding.py",
      "samples": 17,
      "current_pct": 81.0,
      "window_max_pct": 95.0,
      "delta_vs_window_max": -14.0,
      "delta_vs_previous": -0.4,
      "regression": true,
      "threshold": 2.0,
      "window_days": 30
    }
  ]
}
```

## Regression semantics

> A module is **regressing** when its **latest pct** has fallen **below
> the trailing window's max** by at least the threshold.

In symbols: `window_max_pct - current_pct >= threshold`.

The single-step `delta_vs_previous` is reported as data but is **not** the
alert trigger. This is by design — a big single-snapshot drop is already
loud; this plugin exists for the case where no single drop is loud but
the cumulative trajectory is.

If only one snapshot exists, `window_max_pct == current_pct` and the
verdict is `false`. If no snapshots fall within the window, the current
point is used as the window max (degenerate fallback so the response is
always well-defined).

## `pre_llm_call` hook

The plugin injects a short coverage summary into the **current turn's
user message** when **both** are true:

1. The user message matches `\b(coverage|cobertura)\b` (case-insensitive).
2. A known module path or package appears in the message (substring or
   token match against the distinct values seen in the DB).

Otherwise it injects nothing. Injection is per-turn, never modifies the
system prompt (preserving the prompt cache), and concatenates with any
other plugins' context (joined by `\n\n`, plugin discovery order).

This narrowness is deliberate. Coverage history is interesting only when
the user is asking about coverage of something we actually have data on.

## Configuration

| Env var | Default | Effect |
|---|---|---|
| `HERMES_HOME` | `~/.hermes` | Plugin data location. |
| `HERMES_COVERAGE_REGRESSION_THRESHOLD` | `2.0` | Default regression threshold (pp). |
| `HERMES_COVERAGE_WINDOW_DAYS` | `30` | Default trailing window (days). |

Tool args override env vars; env vars override built-in defaults.

## Failure modes & guarantees

- **Malformed XML** at ingestion → CLI prints `error: ...` and exits
  non-zero; nothing is written.
- **Empty / unknown module** at query time → empty series, no regression,
  no exception.
- **Hook errors** (DB locked, unexpected schema, etc.) → return `None`,
  no injection, no crash, no log noise the user sees.
- **Tool handler exceptions** → caught and returned as
  `{"error": "..."}` JSON per §2.5.

## Adding a new ingestion format

The parser is isolated in `parser.py`. To add (e.g.) a `coverage json`
parser, add a sibling function `parse_coverage_json(path) ->
list[ModuleCoverage]` and dispatch in `cli.py` based on file
extension. No changes needed elsewhere — the DB schema is parser-agnostic.
