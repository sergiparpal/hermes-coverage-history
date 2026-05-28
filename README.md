# hermes-coverage-history
Hermes plugin that persists per-module test-coverage figures over time in a local SQLite database and exposes read-only tools so the agent can answer questions about coverage trends and surface silent coverage regression (slow erosion that never trips a per-commit check).
