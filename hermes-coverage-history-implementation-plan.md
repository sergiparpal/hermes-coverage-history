# Implementation Plan: `hermes-coverage-history` (Hermes Agent Plugin)

> **Audience:** an autonomous coding agent (Claude Code CLI).
> **Target runtime:** Hermes Agent **v0.14.0** (release `v2026.5.16`, "Foundation"), Python **3.11+**.
> **Dependencies:** **standard library only** — no third-party packages.
> **Mode:** fully autonomous. There are **no human review checkpoints between phases.** The agent self-gates each phase against an automated test command and only advances when it is green.

---

## 0. Execution Protocol (read first)

You will build this plugin phase by phase. Follow these rules for the entire run:

1. **Work strictly in phase order** (Phase 0 → 4). Do not start a phase until the previous phase's **Acceptance Gate** command exits `0`.
2. **Self-gate, don't ask.** After each phase, run that phase's Acceptance Gate. If it fails, fix the code within the same phase and re-run until green. Never pause to request human confirmation.
3. **Commit after every green phase** with a message like `feat(phaseN): <summary>`. Keep each phase's diff self-contained and reviewable.
4. **Never modify Hermes Agent core.** Everything you write lives inside the plugin directory. You do not clone, edit, or import-and-monkeypatch the `hermes-agent` repository.
5. **No external dependencies.** Standard library only (`sqlite3`, `xml.etree.ElementTree`, `argparse`, `json`, `re`, `datetime`, `os`, `pathlib`). `pytest` is allowed **only** as a dev/test tool, not as a plugin runtime import.
6. **Tests must not require a running Hermes process, a network call, or an LLM provider.** All plugin logic is testable as plain functions plus a fake `ctx`. This keeps your loop fast and hermetic.
7. **Do not invent API surface.** Use only the `ctx` methods and manifest fields documented in §2. If you think you need something else, implement it inside the plugin instead of assuming a core capability.
8. **Pin nothing you can't verify.** The `register_tool` signature, manifest fields, and hook contract in §2 are verified against the official `build-a-hermes-plugin` guide. Treat §2 as authoritative.

---

## 1. What You Are Building

A Hermes plugin (category: **`general`**) that persists per-module test-coverage figures over time in a local SQLite database and exposes read-only tools so the agent can answer questions about coverage trends and surface **silent coverage regression** (slow erosion that never trips a per-commit check).

It provides:

- A **CLI command** `hermes coverage-history record <report.xml> [--sha SHA] [--label LABEL]` to ingest a coverage report (deterministic, CI/cron-friendly; not exposed to the LLM).
- Two **read-only tools** for the LLM: `coverage_trend` and `coverage_regressions`.
- One optional, **selective `pre_llm_call` hook** that injects a short coverage summary only when the user's message is clearly about coverage of a known module.

### 1.1 Fixed design decisions (already made — implement as stated, do not re-litigate)

1. **Input format:** Parse **Cobertura XML only** (the universal CI artifact produced by `coverage xml` / `pytest-cov`, and by many non-Python tools). Put parsing behind a single function `parse_report(path) -> list[ModuleCoverage]` so a future `coverage json` parser is an additive function, not a refactor.
2. **Granularity:** Store coverage **per file** (one row per `<class filename=...>`). Aggregate up to package/prefix **at query time**. Never store only aggregated data.
3. **Regression definition:** A module is regressing when its **latest coverage** is below the **maximum coverage observed within a trailing window** by at least a configurable threshold (default **2.0** percentage points; default window **30 days**). The delta vs. the immediately previous snapshot is computed and returned as data, but it is **not** the alert trigger.
4. **Ingestion + storage:** Ingestion is a **CLI command**, not an LLM tool. Storage uses plain **indexed SQLite columns — no FTS5.**

---

## 2. Hermes Agent Plugin Contract (authoritative)

This section is the binding spec for how Hermes loads and calls a plugin. Implement exactly to it.

### 2.1 Location & discovery

- A user-scope plugin is a directory at `~/.hermes/plugins/<plugin-name>/`. The directory name is the plugin's identity for discovery ordering.
- Discovery is **automatic at startup**: Hermes loads what it finds in the plugin sources (user dir, project dir, pip entry points). There is no `enabled` list to edit. Users disable a plugin afterward with `hermes plugins disable <name>`.
- The agent's `register(ctx)` runs **exactly once at startup**. If it raises, the plugin is disabled but Hermes keeps running.

### 2.2 Hard rules

- **MUST NOT modify core files** (`run_agent.py`, `cli.py`, `gateway/run.py`, `hermes_cli/main.py`, etc.). Capabilities are added via the plugin surface only.
- Standard library only for the plugin runtime.
- Resolve the Hermes home directory from the `HERMES_HOME` environment variable, falling back to `~/.hermes`. Do **not** import internal core helpers for this.

### 2.3 Manifest — `plugin.yaml`

Use **only** these documented fields:

```yaml
name: hermes-coverage-history
version: 0.1.0
description: Tracks per-module test coverage over time and detects silent coverage regression.
author: <fill in or omit>
provides_tools:
  - coverage_trend
  - coverage_regressions
provides_hooks:
  - pre_llm_call
```

- `provides_tools` / `provides_hooks` are lists of what the plugin registers.
- Optional `requires_env` gates loading on environment variables. This plugin needs no secrets, so **omit `requires_env`.**
- Do **not** add manifest keys that are not in this list (no `requires_hermes_version`, no `provides_cli`, etc. — they are not part of the manifest schema).

### 2.4 `register(ctx)` and the `ctx` API

Define `def register(ctx): ...` in `__init__.py`. Inside it you may call:

| Method | Signature | Use here |
|---|---|---|
| `ctx.register_tool` | `(name, toolset, schema, handler, check_fn=None)` | Expose `coverage_trend` and `coverage_regressions` to the LLM. `description` lives **inside** `schema["description"]` (OpenAI function-calling style), **not** as a separate kwarg. `check_fn` returning `False` hides a tool. |
| `ctx.register_hook` | `(hook_name, callback)` | Register the `pre_llm_call` callback. |
| `ctx.register_cli_command` | `(name, help, setup_fn, handler_fn)` | Add the `hermes coverage-history <subcmd>` tree. |

Do not assume other `register_tool` kwargs (`is_async`, `emoji`, `description=`, `requires_env=`) exist — they are unverified.

### 2.5 Tool schema + handler contract

**Schema** is a dict the LLM reads to decide when to call the tool:

```python
{
    "name": "coverage_trend",
    "description": "Specific, action-oriented description of when to call this.",
    "parameters": {
        "type": "object",
        "properties": { ... },
        "required": [ ... ],
    },
}
```

**Handler** rules (all mandatory):

1. Signature: `def handler(args: dict, **kwargs) -> str`.
2. **Always return a JSON string** — on success and on error alike (`json.dumps(...)`).
3. **Never raise.** Catch every exception and return `json.dumps({"error": str(e)})`.
4. Accept `**kwargs` for forward compatibility.

### 2.6 `pre_llm_call` hook contract

- It is the **only** hook whose return value is used; all other hooks are fire-and-forget observers.
- It fires **once per turn, before the tool-calling loop**.
- Callback receives keyword args: `session_id`, `user_message`, `conversation_history`, `is_first_turn`, `model`, `platform`. Always also accept `**kwargs`.
- **Return values:** a dict `{"context": "..."}`, or an equivalent plain non-empty string, to inject context; return `None` (or empty) to inject nothing.
- Injected text is appended to the **current turn's user message** (not the system prompt — this preserves the prompt cache). It is ephemeral and is not persisted.
- **Multiple plugins:** if several plugins return context, the outputs are **concatenated** (joined with `\n\n`) and appended together, in plugin **discovery order (alphabetical by plugin directory name)**. Your hook therefore coexists safely with other context-injecting plugins; never assume you are the only one.
- Fail silently: on any internal error, return `None`. A crashing hook is logged and skipped.

### 2.7 CLI command contract

`ctx.register_cli_command(name, help, setup_fn, handler_fn)`:

- `setup_fn(subparser)` builds the argparse tree for `hermes <name> ...` and should call `subparser.set_defaults(func=handler_fn)`.
- `handler_fn(args)` receives the argparse `Namespace`.
- This command is terminal-only and is **not** visible to the LLM — which is exactly what we want for ingestion.

---

## 3. Data Model (SQLite, no FTS5)

DB path: `<HERMES_HOME>/coverage-history/coverage_history.db`, where `HERMES_HOME` comes from the env var or defaults to `~/.hermes`. Create the directory if missing. Open with `sqlite3` in WAL mode.

```sql
CREATE TABLE IF NOT EXISTS snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_at TEXT    NOT NULL,        -- ISO-8601 UTC, e.g. 2026-05-28T10:00:00Z
    commit_sha  TEXT,                    -- optional
    label       TEXT,                    -- optional source label
    source_path TEXT                     -- the ingested report path
);

CREATE TABLE IF NOT EXISTS modules (
    snapshot_id   INTEGER NOT NULL REFERENCES snapshots(id),
    path          TEXT    NOT NULL,      -- file path from <class filename=...>
    package       TEXT,                  -- <package name=...>
    lines_total   INTEGER NOT NULL,
    lines_covered INTEGER NOT NULL,
    pct           REAL    NOT NULL       -- 0.0 .. 100.0
);

CREATE INDEX IF NOT EXISTS idx_modules_path  ON modules(path);
CREATE INDEX IF NOT EXISTS idx_modules_snap  ON modules(snapshot_id);
CREATE INDEX IF NOT EXISTS idx_snapshots_time ON snapshots(recorded_at);
```

Aggregation rule (per snapshot): for a requested `module`, sum `lines_total` and `lines_covered` over all matching `modules` rows and compute `pct = 100 * covered / total` (guard divide-by-zero → `0.0` when total is 0). Matching: `path = :m` OR `path LIKE :m || '/%'` OR `package = :m`.

`since` parsing: accept `"<N>d"` / `"<N>w"` (relative days/weeks from now, UTC), an ISO date `YYYY-MM-DD`, or `None`/empty (no lower bound). Defaults: trailing window for regression = `HERMES_COVERAGE_WINDOW_DAYS` env or `30`; threshold = `HERMES_COVERAGE_REGRESSION_THRESHOLD` env or `2.0`. Tool args override env defaults.

---

## 4. Project Layout

```
hermes-coverage-history/
├── plugin.yaml          # manifest (§2.3)
├── __init__.py          # register(ctx): wires tools, hook, CLI command
├── db.py                # connection, schema init, inserts, queries (pure, no ctx)
├── parser.py            # parse_report(path) -> list[ModuleCoverage]  (Cobertura XML)
├── trends.py            # since-parsing, aggregation, regression logic (pure)
├── schemas.py           # COVERAGE_TREND, COVERAGE_REGRESSIONS schema dicts
├── tools.py             # coverage_trend / coverage_regressions handlers
├── hook.py              # pre_llm_call selective injection
├── cli.py               # argparse setup_fn + handler_fn for `record`
├── manual.md            # human + AI documentation
├── README.md
└── tests/
    ├── __init__.py
    ├── conftest.py      # FakeCtx, tmp DB fixtures, sample Cobertura XML fixture
    ├── test_parser.py
    ├── test_db.py
    ├── test_trends.py
    ├── test_tools.py
    ├── test_hook.py
    ├── test_cli.py
    └── test_register.py
```

Keep `db.py`, `parser.py`, and `trends.py` free of any Hermes import so they are unit-testable in isolation.

---

## 5. Phased Implementation (autonomous, self-gated)

Each phase lists the work and a single **Acceptance Gate** command. Advance only when it exits `0`.

### Phase 0 — Scaffold & DB bootstrap
**Work:**
- Create the directory layout in §4 and a valid `plugin.yaml` (§2.3).
- `db.py`: `get_db_path()`, `connect()` (WAL), `init_schema(conn)` per §3, plus `insert_snapshot(...)` and `insert_modules(...)`.
- `__init__.py`: define `register(ctx)` as a stub that imports cleanly (wiring added later).
- `tests/conftest.py`: a `FakeCtx` (see §6), a `tmp_db` fixture, and a `sample_cobertura_xml` fixture (use Appendix A verbatim).
- `tests/test_db.py`: schema initializes; a snapshot + its modules insert and read back.

**Acceptance Gate:** `python -c "import db, __init__" && pytest tests/test_db.py -q`

### Phase 1 — Cobertura parser + ingestion CLI
**Work:**
- `parser.py`: `parse_report(path) -> list[ModuleCoverage]`. For each `<class>`: read `filename` (→ `path`), owning `<package name>` (→ `package`); count `<line>` elements for `lines_total` and those with `hits > 0` for `lines_covered`; compute `pct`. A `ModuleCoverage` is a small dataclass or `NamedTuple` (`path, package, lines_total, lines_covered, pct`). Raise a clear `ValueError` on malformed/empty XML (caught at the CLI boundary).
- `cli.py`: `setup_fn(subparser)` adding a `record` subcommand with positional `report_path` and optional `--sha`, `--label`; `handler_fn(args)` that parses, opens DB, inserts a snapshot (`recorded_at` = now UTC ISO) + module rows, and prints a one-line summary (`Recorded N modules from <path> (snapshot #id)`). On error, print a readable message and exit non-zero.
- `tests/test_parser.py`: parses the fixture; line counts, pct, package, and path are correct; malformed XML raises.
- `tests/test_cli.py`: invoking the handler with the fixture inserts exactly the expected rows.

**Acceptance Gate:** `pytest tests/test_parser.py tests/test_cli.py -q`

### Phase 2 — Read tools + regression logic
**Work:**
- `trends.py` (pure): `parse_since(s) -> datetime|None`; `module_series(conn, module, since) -> list[point]` (each point: `recorded_at, pct, lines_total, lines_covered`, aggregated per snapshot per §3); `detect_regression(series, threshold, window_days) -> dict` returning `current_pct`, `window_max_pct`, `delta_vs_window_max`, `delta_vs_previous`, `regression: bool`, `threshold`, `window_days`; `worst_regressions(conn, since, threshold, window_days) -> list` scanning all known module paths.
- `schemas.py`: schema dicts for both tools (clear `description`, typed `parameters`). `coverage_trend(module: str, since: str = "30d", threshold?: number, window_days?: integer)`. `coverage_regressions(since: str = "30d", threshold?: number, window_days?: integer, limit?: integer)`.
- `tools.py`: `coverage_trend(args, **kwargs)` and `coverage_regressions(args, **kwargs)` handlers obeying §2.5 (always JSON string, never raise). Defaults pulled from env per §3.
- `tests/test_trends.py`: build a synthetic multi-snapshot dataset that **declines ~1pt per snapshot over 20 snapshots** (each single-step delta below threshold) and assert `detect_regression` flags it via `delta_vs_window_max` — this is the headline "silent regression" behavior and must be covered.
- `tests/test_tools.py`: handlers return valid JSON for success and for forced errors (e.g. unknown module → empty series, not an exception).

**Acceptance Gate:** `pytest tests/test_trends.py tests/test_tools.py -q`

### Phase 3 — Selective `pre_llm_call` hook
**Work:**
- `hook.py`: `inject_coverage_summary(session_id=None, user_message="", conversation_history=None, is_first_turn=False, model=None, platform=None, **kwargs)`.
  - Return `None` immediately if `user_message` is empty.
  - Require a coverage keyword: `re.search(r"\b(coverage|cobertura)\b", user_message, re.I)`.
  - Require a **known module** reference: look up distinct `path`/`package` values from the DB and only proceed if one is mentioned in the message (substring match on a path/package token). This keeps injection rare and cache-friendly.
  - On match, compute a short trend/regression summary for that module and return `{"context": "Coverage summary for <module>: ..."}`. Otherwise return `None`.
  - Wrap the whole body in try/except → return `None` on any error.
- `tests/test_hook.py`: returns a context dict when keyword + known module are present; returns `None` when either is missing, when the DB is empty, and when `user_message` is empty.

**Acceptance Gate:** `pytest tests/test_hook.py -q`

### Phase 4 — Wiring, docs, full suite
**Work:**
- `__init__.py` `register(ctx)`: register both tools (`toolset="coverage-history"`), register the `pre_llm_call` hook, and register the CLI command (`name="coverage-history"`). Optionally guard tools with `check_fn` that returns `True` (DB is created lazily, so no gating is strictly needed).
- `tests/test_register.py`: call `register(FakeCtx())` and assert it registered 2 tools, 1 hook (`pre_llm_call`), and 1 CLI command (`coverage-history`), with the tool names matching `plugin.yaml`. This is the integration smoke test that replaces a human "does it load" check.
- Write `README.md` (install, the `record` workflow, example CI/cron line) and `manual.md` (tool reference, regression semantics, config env vars).

**Acceptance Gate:** `pytest -q` (entire suite green).

---

## 6. Testing Requirements

- **`FakeCtx`** (in `conftest.py`) records registrations without a real Hermes:

  ```python
  class FakeCtx:
      def __init__(self):
          self.tools, self.hooks, self.cli = [], [], []
      def register_tool(self, name, toolset, schema, handler, check_fn=None):
          self.tools.append((name, toolset, schema, handler))
      def register_hook(self, hook_name, callback):
          self.hooks.append((hook_name, callback))
      def register_cli_command(self, name, help, setup_fn, handler_fn):
          self.cli.append((name, help, setup_fn, handler_fn))
  ```

- Every handler test must assert the return value **parses as JSON** and that the **error path also returns JSON** (never raises).
- Point the DB at a `tmp_path` DB in tests by setting `HERMES_HOME` to a temp dir via `monkeypatch.setenv` so no test touches the real `~/.hermes`.
- Use the Appendix A fixture for all parser/CLI tests so the parser is validated against real Cobertura structure, not assumptions.
- Tests must pass with `pytest` and require no network, no provider keys, and no running Hermes.

---

## 7. Definition of Done

- `pytest -q` is fully green.
- `register(FakeCtx())` registers exactly 2 tools, 1 `pre_llm_call` hook, and 1 `coverage-history` CLI command; names match `plugin.yaml`.
- `parse_report` correctly handles the Appendix A fixture and raises on malformed input.
- The silent-regression test (Phase 2) passes: a slow multi-snapshot decline is flagged via `delta_vs_window_max` even though no single step exceeds the threshold.
- All four fixed decisions in §1.1 are implemented as stated.
- No third-party runtime dependency is imported; no Hermes core file was created or modified.
- `README.md` and `manual.md` exist and describe install, ingestion, tools, and config.

---

## 8. Guardrails — Do NOT

- Do not add external dependencies (no `coverage`, `lxml`, `pytest-cov`, etc. as runtime imports — parse XML with `xml.etree.ElementTree`).
- Do not use FTS5 or any full-text index.
- Do not expose ingestion as an LLM tool; ingestion is the CLI command only.
- Do not mutate the system prompt from the hook; only return context (which Hermes appends to the user message).
- Do not invent `ctx` methods or `plugin.yaml` keys beyond §2.
- Do not pause for human approval between phases; gate on the Acceptance commands.

---

## Appendix A — Cobertura XML test fixture

Use this exact content for `sample_cobertura_xml` (2 packages, 3 files, mixed coverage). Expected: `pkg_a/foo.py` → 3/4 lines (75.0%), `pkg_a/bar.py` → 2/2 (100.0%), `pkg_b/baz.py` → 1/4 (25.0%).

```xml
<?xml version="1.0" ?>
<coverage line-rate="0.6" branch-rate="0.0" version="7.4.0" timestamp="1716800000">
  <sources>
    <source>/repo/src</source>
  </sources>
  <packages>
    <package name="pkg_a" line-rate="0.83">
      <classes>
        <class name="foo" filename="pkg_a/foo.py" line-rate="0.75">
          <lines>
            <line number="1" hits="1"/>
            <line number="2" hits="3"/>
            <line number="3" hits="0"/>
            <line number="4" hits="2"/>
          </lines>
        </class>
        <class name="bar" filename="pkg_a/bar.py" line-rate="1.0">
          <lines>
            <line number="1" hits="5"/>
            <line number="2" hits="1"/>
          </lines>
        </class>
      </classes>
    </package>
    <package name="pkg_b" line-rate="0.25">
      <classes>
        <class name="baz" filename="pkg_b/baz.py" line-rate="0.25">
          <lines>
            <line number="1" hits="1"/>
            <line number="2" hits="0"/>
            <line number="3" hits="0"/>
            <line number="4" hits="0"/>
          </lines>
        </class>
      </classes>
    </package>
  </packages>
</coverage>
```

---

## Appendix B — Reference snippets (shape only; complete them per the phases)

**`__init__.py` registration:**

```python
import logging
from . import schemas, tools, hook, cli

logger = logging.getLogger(__name__)

def register(ctx):
    ctx.register_tool(name="coverage_trend", toolset="coverage-history",
                      schema=schemas.COVERAGE_TREND, handler=tools.coverage_trend)
    ctx.register_tool(name="coverage_regressions", toolset="coverage-history",
                      schema=schemas.COVERAGE_REGRESSIONS, handler=tools.coverage_regressions)
    ctx.register_hook("pre_llm_call", hook.inject_coverage_summary)
    ctx.register_cli_command(name="coverage-history",
                             help="Manage coverage history (record reports)",
                             setup_fn=cli.setup_argparse, handler_fn=cli.handle)
```

**Tool handler shape:**

```python
import json

def coverage_trend(args: dict, **kwargs) -> str:
    try:
        module = (args.get("module") or "").strip()
        if not module:
            return json.dumps({"error": "module is required"})
        # ... query series, run detect_regression ...
        return json.dumps({"module": module, "series": series, **regression})
    except Exception as e:
        return json.dumps({"error": str(e)})
```

**`pre_llm_call` hook shape:**

```python
import re

def inject_coverage_summary(session_id=None, user_message="", conversation_history=None,
                            is_first_turn=False, model=None, platform=None, **kwargs):
    try:
        if not user_message:
            return None
        if not re.search(r"\b(coverage|cobertura)\b", user_message, re.I):
            return None
        module = _match_known_module(user_message)  # None if no known module mentioned
        if not module:
            return None
        summary = _short_summary(module)
        return {"context": f"Coverage summary for {module}: {summary}"} if summary else None
    except Exception:
        return None
```

**CLI shape:**

```python
def setup_argparse(subparser):
    subs = subparser.add_subparsers(dest="coverage_command")
    rec = subs.add_parser("record", help="Ingest a Cobertura XML coverage report")
    rec.add_argument("report_path")
    rec.add_argument("--sha", default=None)
    rec.add_argument("--label", default=None)
    subparser.set_defaults(func=handle)

def handle(args):
    sub = getattr(args, "coverage_command", None)
    if sub == "record":
        # parse_report -> insert snapshot + modules -> print summary
        ...
    else:
        print("Usage: hermes coverage-history record <report.xml> [--sha SHA] [--label LABEL]")
```
