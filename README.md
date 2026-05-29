# hermes-coverage-history

A Hermes Agent plugin that persists per-module test coverage figures over
time in a local SQLite database and exposes read-only tools so the agent can
answer questions about coverage trends and surface **silent coverage
regression** — slow erosion that never trips a per-commit check.

- Standard library only. No third-party runtime dependencies.
- Ingestion is a CLI command (deterministic, CI/cron-friendly) — never an
  LLM tool.
- Two read-only LLM tools: `coverage_trend`, `coverage_regressions`.
- One selective `pre_llm_call` hook that only injects context when the user
  is clearly asking about coverage of a known module.

## Install

Drop this directory into Hermes' user-scope plugin path:

```
~/.hermes/plugins/hermes-coverage-history/
```

Hermes discovers plugins automatically at startup. No `enabled` list to
edit. Disable later, if needed, with `hermes plugins disable
hermes-coverage-history`.

## Record a coverage report

```
hermes coverage-history record <report.xml> [--sha SHA] [--label LABEL]
```

`<report.xml>` is a **Cobertura XML** report — what `coverage xml` and
`pytest --cov --cov-report=xml` produce, and also what many non-Python
tools emit.

`record` writes the snapshot row and its per-file module rows in a single
transaction, so a mid-ingest failure (a UNIQUE collision, a full disk) rolls
the snapshot back rather than leaving an orphan with no module rows. On a
parse or DB error it prints `error: ...` to stderr and exits 1; running
`hermes coverage-history` with no subcommand prints a usage line and exits 2.

A report is rejected before anything is written if it exceeds 50 MB, carries a
`<!DOCTYPE>` / `<!ENTITY>` declaration, or yields no valid `<class>` entries
(see [Security](#security)).

### CI example (GitHub Actions)

```yaml
- name: Test with coverage
  run: pytest --cov --cov-report=xml

- name: Record coverage history
  run: |
    hermes coverage-history record coverage.xml \
        --sha "${{ github.sha }}" --label ci
```

### Cron example

```
0 4 * * *  cd /path/to/repo && coverage xml -o /tmp/cov.xml \
                            && hermes coverage-history record /tmp/cov.xml \
                                   --label nightly
```

## LLM tools

| Tool | What it does |
|---|---|
| `coverage_trend(module, since="30d", threshold?, window_days?)` | Per-snapshot coverage time series plus a silent-regression verdict. `module` is a file path, directory prefix, or Cobertura package name (required, whitespace-trimmed). |
| `coverage_regressions(since="30d", threshold?, window_days?, limit?)` | Modules currently regressing, ranked worst first. `limit` defaults to 10, clamped to `0..1000` (`0` returns no rows). |

Both return JSON. The `since` argument accepts `Nd` (days), `Nw` (weeks), an
ISO-8601 date or datetime (e.g. `2026-05-01` or `2026-05-01T12:30:00Z`; naive
timestamps are treated as UTC), or omit it for all history. `since` controls
how much history the series spans; `window_days` is the independent trailing
window the regression high-water mark is taken over — `since="90d"` returns 90
days of points but a 30-day `window_days` still measures the drop against the
last 30 days only.

**On error, both tools return JSON, never raising.** Validation problems
(missing `module`, a malformed `since`, non-object args) are echoed back as
`{"error": "<reason>"}`, but any unexpected internal error is logged locally
and returned as a generic `{"error": "internal error"}` so host paths and
SQLite internals never leak to the model.

See `manual.md` for the full reference, response shape, and regression
semantics.

## How silent regression is detected

For a module, the plugin compares the **latest** coverage against the
**maximum** coverage seen within the trailing window (default 30 days):

```
regression  ⇔  window_max_pct - current_pct >= threshold   (default 2.0 pp)
```

The trigger is the **cumulative** drop from the window's high-water mark, *not*
the change versus the previous snapshot — that is the whole point. A module
that loses ~1 pp per snapshot passes every per-commit gate (each single step is
below threshold) yet still gets flagged once the total slide crosses the
threshold. Each verdict reports both `delta_vs_window_max` (the trigger) and
`delta_vs_previous` (the single-step change, for context only — it never drives
the `regression` flag).

Edge cases: with a single snapshot `window_max_pct == current_pct`, so the
verdict is `false`. If no snapshot falls inside the window, the current point
is used as the window max, keeping the verdict well-defined and `false`.

## Data model

Coverage is stored **per file** — one `modules` row per Cobertura
`<class filename=...>` — never pre-aggregated. A query for a module `m`
aggregates at query time over rows where `path = m`, `path LIKE m || '/%'`
(directory prefix), or `package = m`. Aggregate `pct` is **recomputed** from
summed line totals (`100 * SUM(covered) / SUM(total)`), so package-level
numbers are weighted by file size rather than averaged.

The directory-prefix match escapes SQL `LIKE` metacharacters (`%`, `_`, `\`) in
the supplied module name, so a value like `%` is matched literally and cannot
match-all. See `manual.md` for the response shapes and full aggregation detail.

## Configuration

| Env var | Default | Effect |
|---|---|---|
| `HERMES_HOME` | `~/.hermes` | Plugin data dir; DB lives under `<HERMES_HOME>/coverage-history/`. |
| `HERMES_COVERAGE_REGRESSION_THRESHOLD` | `2.0` | Percentage-point drop from the trailing-window maximum that flags a regression. |
| `HERMES_COVERAGE_WINDOW_DAYS` | `30` | Trailing window (days) over which the regression high-water mark is taken. |

Tool args override env vars; env vars override built-in defaults. All three are
read at call time, so changing the environment takes effect without a restart.

## Storage and concurrency

Each connection opens with `journal_mode=WAL`, `foreign_keys=ON`,
`busy_timeout=30000` (30s), and a 30-second SQLite connect timeout. WAL lets a
CI ingestion write and an agent read proceed concurrently; the busy timeout
means a contended connection waits up to 30s for the lock rather than failing
immediately.

The CLI `record` path opens the DB with full schema/migration setup. The read
paths (the two LLM tools and the hook) open with `create=False`: they skip the
DDL + migration commit when the schema already exists, so a read-only call
never takes write intent or contends with a concurrent ingestion. A reader
opening a never-recorded DB still materializes the schema lazily, so querying
before anything is recorded returns empty results rather than `no such table`.

The schema self-migrates on connect: a `schema_version` table tracks an
append-only migration list applied at most once each, so upgrading from an
earlier install is automatic on first connect. Migration v1 deduplicates any
pre-existing `(snapshot_id, path)` collisions and then enforces a
`UNIQUE (snapshot_id, path)` index, so a snapshot holds at most one row per file
path.

## Security

This plugin parses untrusted Cobertura XML (often a CI artifact) and surfaces
stored strings back to the LLM, so ingestion and output are both hardened.

**Hardened XML ingestion.** Before parsing, `record` rejects any report that
exceeds 50 MB on disk, or that carries a `<!DOCTYPE>` / `<!ENTITY>` declaration
— the stdlib XML parser expands internal entities with no size cap, so this
blocks billion-laughs / quadratic-blowup attacks. Legitimate `coverage xml`
output never contains a DTD. On rejection nothing is written and `record` exits
non-zero.

**Field validation at ingestion.** Each `<class>` is dropped (not ingested) if
its `filename` or `package` contains a control character or DEL, exceeds 512
characters, or contains a `..` path segment — preventing a hostile report from
smuggling control characters or instruction-shaped prose into data later shown
to the LLM. The recorded module count may therefore be lower than the number of
`<class>` elements, and a report yielding zero valid entries is rejected. File
paths are normalized on ingest (backslashes become forward slashes, repeated
separators collapse, leading `./` and `/` are stripped), so the same module
lands under one `path` across snapshots and OS producers; query module
references against this normalized form.

**Output sanitization.** As defense in depth, every DB-sourced `module` string
is scrubbed (control characters and DEL stripped, length capped — 512 in
`coverage_regressions` responses, 256 in the hook summary) before it leaves the
plugin. So even a row predating the ingestion-time validation cannot reshape the
prompt.

**File permissions.** On first creation the plugin restricts the data directory
to `0o700` and `coverage_history.db` to `0o600`, so other users on a
multi-user host cannot read your coverage history (the directory mode also gates
the WAL/SHM sidecar files SQLite creates). This is best-effort: on filesystems
without POSIX modes (e.g. NTFS under WSL) the chmod is silently skipped and the
process umask applies.

## Tests

```
pytest -q
```

The suite uses standard library only (no Hermes runtime, no network,
no LLM provider keys).
