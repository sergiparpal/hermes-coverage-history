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
| `coverage_trend(module, since="30d", threshold?, window_days?)` | Per-snapshot coverage time series plus a silent-regression verdict. `module` is a file path, directory prefix, or Cobertura package name. |
| `coverage_regressions(since="30d", threshold?, window_days?, limit?)` | Modules currently regressing, ranked worst first. |

Both return JSON. See `manual.md` for the full reference, response shape,
and regression semantics.

## Configuration

| Env var | Default | Effect |
|---|---|---|
| `HERMES_HOME` | `~/.hermes` | Plugin data dir; DB lives under `<HERMES_HOME>/coverage-history/`. |
| `HERMES_COVERAGE_REGRESSION_THRESHOLD` | `2.0` | Percentage-point drop vs. the window max that flags a regression. |
| `HERMES_COVERAGE_WINDOW_DAYS` | `30` | Trailing window (days) for regression detection. |

Tool args override env vars; env vars override built-in defaults.

## Tests

```
pytest -q
```

The suite uses standard library only (no Hermes runtime, no network,
no LLM provider keys).
