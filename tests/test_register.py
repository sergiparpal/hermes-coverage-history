"""Phase 4: integration smoke test — register() wires what the manifest declares."""

import importlib
import re
from pathlib import Path


def _load_plugin_init():
    return importlib.import_module("__init__")


def test_register_wires_two_tools_one_hook_one_cli(fake_ctx):
    plugin = _load_plugin_init()
    plugin.register(fake_ctx)

    tool_names = sorted(t[0] for t in fake_ctx.tools)
    assert tool_names == ["coverage_regressions", "coverage_trend"]

    for name, toolset, schema, handler in fake_ctx.tools:
        assert toolset == "coverage-history"
        assert callable(handler)
        assert isinstance(schema, dict)
        # Per §2.5 the description lives inside the schema.
        assert schema.get("name") == name
        assert "description" in schema and schema["description"]
        assert "parameters" in schema
        assert schema["parameters"].get("type") == "object"

    assert len(fake_ctx.hooks) == 1
    hook_name, callback = fake_ctx.hooks[0]
    assert hook_name == "pre_llm_call"
    assert callable(callback)

    assert len(fake_ctx.cli) == 1
    cli_name, cli_help, setup_fn, handler_fn = fake_ctx.cli[0]
    assert cli_name == "coverage-history"
    assert isinstance(cli_help, str) and cli_help
    assert callable(setup_fn)
    assert callable(handler_fn)


def test_tool_names_match_manifest():
    root = Path(__file__).resolve().parent.parent
    manifest = (root / "plugin.yaml").read_text()
    # Minimal manual parse — no PyYAML dependency.
    declared = set(re.findall(r"-\s+(coverage_\w+)", manifest))
    assert declared == {"coverage_trend", "coverage_regressions"}


def test_manifest_declares_pre_llm_call_hook():
    root = Path(__file__).resolve().parent.parent
    manifest = (root / "plugin.yaml").read_text()
    assert re.search(r"-\s+pre_llm_call\b", manifest)


def test_handler_returns_json_via_register_path(fake_ctx, hermes_home):
    """End-to-end: registered handlers behave like the §2.5 contract."""
    plugin = _load_plugin_init()
    plugin.register(fake_ctx)

    handlers = {name: handler for name, _, _, handler in fake_ctx.tools}

    out = handlers["coverage_trend"]({"module": "nope"})
    assert isinstance(out, str)
    import json
    payload = json.loads(out)
    assert "series" in payload

    out2 = handlers["coverage_regressions"]({})
    payload2 = json.loads(out2)
    assert "regressions" in payload2


def test_registered_hook_callable_with_kwargs(fake_ctx, hermes_home):
    plugin = _load_plugin_init()
    plugin.register(fake_ctx)
    _, callback = fake_ctx.hooks[0]
    # No DB, no coverage keyword → must return None and not raise.
    assert callback(user_message="hi there", session_id="s1") is None
