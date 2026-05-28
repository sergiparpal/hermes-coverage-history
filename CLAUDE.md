# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Hermes Agent plugin (standard library only) that persists per-module test coverage in SQLite and detects **silent regression** — slow erosion that never trips a per-commit gate. Ingestion runs from CI/cron via a CLI command; the LLM gets only read-only tools (`coverage_trend`, `coverage_regressions`) and one selective `pre_llm_call` hook.

The authoritative spec is `hermes-coverage-history-implementation-plan.md`. Fixed design decisions in §1.1 are settled — implement to them, don't re-litigate.

## Common commands

```bash
pytest -q                                    # full suite
pytest tests/test_trends.py -q               # single file
pytest tests/test_trends.py::test_name -q    # single test
python -c "import db, __init__"              # import smoke test (Phase 0 gate)
```

There is no lint/build step — runtime is stdlib only, tests are stdlib + pytest.

To exercise the CLI end-to-end the plugin must be installed at `~/.hermes/plugins/hermes-coverage-history/` and a Hermes runtime must be present. For development, drive the handler directly (see `tests/test_cli.py`) — no Hermes process needed.

## Module import convention (important)

Sibling modules import each other **flat**, not as a package: `import db`, `import parser as cov_parser`, `import trends`. `__init__.py` inserts the plugin directory into `sys.path` at load time so this works whether the directory is loaded by Hermes as a package, executed via `importlib.util.spec_from_file_location`, or imported by tests (`tests/conftest.py` also inserts the plugin root).

Do **not** convert these to relative imports (`from . import db`) — it will break the Hermes plugin loader and the test import path.

## Architecture

Two layers, deliberately separated:

**Pure layer** (no Hermes imports, unit-testable as plain functions):
- `db.py` — SQLite connection, schema, inserts. `HERMES_HOME` is resolved at *call* time (not import) so tests can `monkeypatch.setenv`.
- `parser.py` — `parse_report(path) -> list[ModuleCoverage]`. Cobertura XML only. A future `coverage json` parser would be an additive sibling function dispatched from `cli.py`, not a refactor.
- `trends.py` — `parse_since`, `module_series` (aggregation SQL), `detect_regression`, `worst_regressions`.

**Hermes-integration layer**:
- `schemas.py` — OpenAI-style tool schemas. The `description` field lives **inside** the schema dict, not as a kwarg to `register_tool`.
- `tools.py` — LLM tool handlers.
- `hook.py` — selective `pre_llm_call` injection.
- `cli.py` — argparse `setup_fn` + `handler_fn` for `hermes coverage-history record`.
- `__init__.py` — `register(ctx)` wires 2 tools + 1 hook + 1 CLI command.

### Data model

Per-file rows, **never** pre-aggregated. Aggregation up to a directory prefix or Cobertura package name happens at query time via:

```
(modules.path = :m OR modules.path LIKE :m || '/%' OR modules.package = :m)
```

`pct` for a multi-row aggregate is **recomputed** from summed line totals (`100 * SUM(covered) / SUM(total)`), never averaged — so package-level numbers are correctly weighted by file size.

DB path: `<HERMES_HOME>/coverage-history/coverage_history.db`. WAL mode is enabled so CI ingestion doesn't block agent reads.

### Regression semantics

A module is regressing when `window_max_pct - current_pct >= threshold` over the trailing `window_days` (defaults: 2.0 pp / 30 days). `delta_vs_previous` is reported as data but is **not** the alert trigger — that's the whole point of catching silent erosion that no single-commit gate would see. The headline test (`tests/test_trends.py`) constructs a ~1pt-per-snapshot decline where every single-step delta is below threshold and asserts the cumulative window-max delta still flags it.

## Contracts that handlers/hooks MUST obey

**LLM tool handlers** (`tools.py`):
1. Signature `def handler(args: dict, **kwargs) -> str`.
2. **Always return a JSON string** — success and error paths alike.
3. **Never raise.** Catch every exception → `json.dumps({"error": str(e)})`.
4. Accept `**kwargs` for forward compatibility.

**`pre_llm_call` hook** (`hook.py`):
- Return `{"context": "..."}`, a non-empty string, or `None`. Hermes appends to the *current turn's user message*, not the system prompt (preserves the prompt cache).
- Never raise — degrade to `None` on any error.
- Inject only when *both* a coverage keyword (`\b(coverage|cobertura)\b`) and a known module/package (substring or token match against DB-distinct values) appear. This narrowness is deliberate; broaden it only with a strong reason.

**Ingestion is CLI-only.** Do not add ingestion as an LLM tool. The LLM reads history; it cannot mutate it.

## Configuration precedence

Tool args → env vars (`HERMES_COVERAGE_REGRESSION_THRESHOLD`, `HERMES_COVERAGE_WINDOW_DAYS`) → built-in defaults (`2.0`, `30`). `HERMES_HOME` resolves to `~/.hermes` if unset.

## Test conventions

- `tests/conftest.py` provides `FakeCtx` (records registrations without a Hermes runtime), `hermes_home` (points `HERMES_HOME` at `tmp_path`), `tmp_db` (fresh schema-init'd connection), and `sample_cobertura_xml` (a known-shape fixture).
- Every test that touches the DB MUST go through the `hermes_home` / `tmp_db` fixtures — no test should write to the real `~/.hermes`.
- Handler tests must assert the return value parses as JSON and that the **error path also returns JSON** (never raises).
- Tests must not require a running Hermes process, network, or LLM provider keys.

## Hard guardrails

- **Standard library only at runtime.** No `coverage`, `lxml`, `pytest-cov`, etc. as runtime imports. `pytest` is dev/test only.
- **No FTS5** or any full-text index.
- **Never modify Hermes core** — everything lives in this plugin directory.
- **Don't invent `ctx` methods or `plugin.yaml` keys** beyond what the plan §2 documents. Use only `register_tool(name, toolset, schema, handler, check_fn=None)`, `register_hook(hook_name, callback)`, `register_cli_command(name, help, setup_fn, handler_fn)`.
- **Don't put `description=` as a kwarg** on `register_tool` — it goes inside the schema dict.
