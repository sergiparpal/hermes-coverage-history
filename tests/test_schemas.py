"""#2: tool schemas single-source their defaults from defaults.py.

These lock in the no-drift property: the values and env-var names the LLM
reads in the schema are the same ones the handlers actually apply.
"""

import defaults
import schemas


def _prop(schema, name):
    return schema["parameters"]["properties"][name]


def test_since_default_tracks_defaults():
    assert _prop(schemas.COVERAGE_TREND, "since")["default"] == defaults.DEFAULT_SINCE
    assert (
        _prop(schemas.COVERAGE_REGRESSIONS, "since")["default"]
        == defaults.DEFAULT_SINCE
    )


def test_limit_default_tracks_defaults():
    assert _prop(schemas.COVERAGE_REGRESSIONS, "limit")["default"] == defaults.LIMIT


def test_threshold_description_reflects_current_default_and_env():
    for schema in (schemas.COVERAGE_TREND, schemas.COVERAGE_REGRESSIONS):
        desc = _prop(schema, "threshold")["description"]
        assert str(defaults.REGRESSION_THRESHOLD) in desc
        assert defaults.ENV_THRESHOLD in desc


def test_window_days_description_reflects_current_default_and_env():
    for schema in (schemas.COVERAGE_TREND, schemas.COVERAGE_REGRESSIONS):
        desc = _prop(schema, "window_days")["description"]
        assert str(defaults.WINDOW_DAYS) in desc
        assert defaults.ENV_WINDOW_DAYS in desc
