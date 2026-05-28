"""hermes-coverage-history plugin entry point.

`register(ctx)` wires:
  - 2 read-only LLM tools: `coverage_trend`, `coverage_regressions`
  - 1 selective `pre_llm_call` hook
  - 1 CLI command tree: `hermes coverage-history <subcmd>`

The plugin uses standard library only. Ingestion is intentionally CLI-only;
the LLM can read coverage history but cannot mutate it.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# Ensure sibling modules resolve whether this file is loaded as a Hermes
# plugin package's __init__.py, imported as a top-level module by tests
# (`import __init__`), or executed via `importlib.util.spec_from_file_location`.
_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

import cli  # noqa: E402
import hook  # noqa: E402
import schemas  # noqa: E402
import tools  # noqa: E402

logger = logging.getLogger(__name__)


def register(ctx):
    """Wire the plugin into Hermes."""
    ctx.register_tool(
        name="coverage_trend",
        toolset="coverage-history",
        schema=schemas.COVERAGE_TREND,
        handler=tools.coverage_trend,
    )
    ctx.register_tool(
        name="coverage_regressions",
        toolset="coverage-history",
        schema=schemas.COVERAGE_REGRESSIONS,
        handler=tools.coverage_regressions,
    )
    ctx.register_hook("pre_llm_call", hook.inject_coverage_summary)
    ctx.register_cli_command(
        name="coverage-history",
        help="Manage coverage history (record reports). Not exposed to the LLM.",
        setup_fn=cli.setup_argparse,
        handler_fn=cli.handle,
    )
