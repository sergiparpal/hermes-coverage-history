"""Tool JSON Schemas exposed to the LLM (OpenAI function-calling style).

Per §2.5: the `description` field belongs *inside* the schema; do not pass
`description=` to `register_tool`.

Default values and env-var names are interpolated from `defaults` so the
schema the LLM reads can never drift from the values the handlers actually
apply — `defaults.py` is the single source of truth.
"""

import defaults

COVERAGE_TREND = {
    "name": "coverage_trend",
    "description": (
        "Return the per-snapshot coverage history for a module along with "
        "a silent-regression verdict. Call this when the user asks about "
        "coverage over time for a specific file, directory, or package, "
        "or to confirm whether a module is trending up or down. The "
        "module argument accepts a file path ('pkg_a/foo.py'), a "
        "directory prefix ('pkg_a'), or a Cobertura package name."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "module": {
                "type": "string",
                "description": (
                    "Identifier to look up: file path, directory prefix, "
                    "or Cobertura package name."
                ),
            },
            "since": {
                "type": "string",
                "description": (
                    "Lower bound for the series. Accepts 'Nd' (days), "
                    "'Nw' (weeks), an ISO date 'YYYY-MM-DD', or omit "
                    f"for all history. Defaults to '{defaults.DEFAULT_SINCE}'."
                ),
                "default": defaults.DEFAULT_SINCE,
            },
            "threshold": {
                "type": "number",
                "description": (
                    "Percentage-point drop vs. the trailing window's max "
                    "that constitutes a regression. Defaults to "
                    f"{defaults.ENV_THRESHOLD} or {defaults.REGRESSION_THRESHOLD}."
                ),
            },
            "window_days": {
                "type": "integer",
                "description": (
                    "Trailing window in days for the regression "
                    f"comparison. Defaults to {defaults.ENV_WINDOW_DAYS} "
                    f"or {defaults.WINDOW_DAYS}."
                ),
            },
        },
        "required": ["module"],
    },
}


COVERAGE_REGRESSIONS = {
    "name": "coverage_regressions",
    "description": (
        "List modules whose latest coverage has fallen below the trailing "
        "window's max by at least the configured threshold (silent "
        "regressions). Call this when the user asks 'what is regressing?', "
        "'is anything slipping?', or to audit coverage hygiene across the "
        "project."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "since": {
                "type": "string",
                "description": (
                    "Lower bound for the scan. Accepts 'Nd', 'Nw', an "
                    "ISO date 'YYYY-MM-DD', or omit for all history. "
                    f"Defaults to '{defaults.DEFAULT_SINCE}'."
                ),
                "default": defaults.DEFAULT_SINCE,
            },
            "threshold": {
                "type": "number",
                "description": (
                    "Percentage-point drop vs. window max that flags a "
                    f"regression. Default {defaults.REGRESSION_THRESHOLD} or env "
                    f"{defaults.ENV_THRESHOLD}."
                ),
            },
            "window_days": {
                "type": "integer",
                "description": (
                    f"Trailing window in days. Default {defaults.WINDOW_DAYS} or env "
                    f"{defaults.ENV_WINDOW_DAYS}."
                ),
            },
            "limit": {
                "type": "integer",
                "description": f"Cap the number of regressions returned. Default {defaults.LIMIT}.",
                "default": defaults.LIMIT,
            },
        },
        "required": [],
    },
}
