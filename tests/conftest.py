"""Shared fixtures.

Adds the plugin root to sys.path so tests can `import db`, `import parser`,
etc. directly — matching the Phase-0 acceptance gate `python -c "import
db, __init__"`.
"""

from __future__ import annotations

import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

import pytest  # noqa: E402

import db  # noqa: E402


SAMPLE_COBERTURA_XML = """<?xml version="1.0" ?>
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
"""


class FakeCtx:
    """Records plugin registrations without a real Hermes runtime."""

    def __init__(self):
        self.tools = []
        self.hooks = []
        self.cli = []

    def register_tool(self, name, toolset, schema, handler, check_fn=None):
        self.tools.append((name, toolset, schema, handler))

    def register_hook(self, hook_name, callback):
        self.hooks.append((hook_name, callback))

    def register_cli_command(self, name, help, setup_fn, handler_fn):
        self.cli.append((name, help, setup_fn, handler_fn))


@pytest.fixture
def fake_ctx():
    return FakeCtx()


@pytest.fixture
def hermes_home(tmp_path, monkeypatch):
    """Point HERMES_HOME at a temp dir for the duration of the test."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def tmp_db(hermes_home):
    """A connection to a fresh schema-initialized SQLite DB under tmp HERMES_HOME."""
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def sample_cobertura_xml(tmp_path):
    p = tmp_path / "coverage.xml"
    p.write_text(SAMPLE_COBERTURA_XML)
    return p
