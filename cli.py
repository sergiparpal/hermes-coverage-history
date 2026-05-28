"""CLI integration for `hermes coverage-history record`.

The ingestion path is deliberately a CLI command, not an LLM tool — so it can
run deterministically from CI or cron without an LLM in the loop.
"""

from __future__ import annotations

import dataclasses
import sys
from datetime import datetime, timezone

import db
import parser as cov_parser


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def setup_argparse(subparser) -> None:
    subs = subparser.add_subparsers(dest="coverage_command", required=False)
    rec = subs.add_parser(
        "record",
        help="Ingest a Cobertura XML coverage report into the local history DB.",
    )
    rec.add_argument("report_path", help="Path to a Cobertura XML report.")
    rec.add_argument(
        "--sha", default=None,
        help="Optional commit SHA to tag this snapshot with.",
    )
    rec.add_argument(
        "--label", default=None,
        help="Optional source label (e.g. 'ci', 'nightly', 'local').",
    )
    subparser.set_defaults(func=handle)


def handle(args) -> int:
    sub = getattr(args, "coverage_command", None)
    if sub == "record":
        return _handle_record(args)
    print(
        "Usage: hermes coverage-history record <report.xml> "
        "[--sha SHA] [--label LABEL]",
        file=sys.stderr,
    )
    return 2


def _handle_record(args) -> int:
    report_path = args.report_path
    try:
        rows = cov_parser.parse_report(report_path)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    module_dicts = [dataclasses.asdict(r) for r in rows]

    conn = db.connect()
    try:
        # `with conn:` makes snapshot + modules a single transaction —
        # if insert_modules raises (e.g. UNIQUE collision, disk full),
        # the snapshot insert is rolled back instead of left as an orphan.
        with conn:
            snapshot_id = db.insert_snapshot(
                conn,
                recorded_at=_utc_now_iso(),
                commit_sha=getattr(args, "sha", None),
                label=getattr(args, "label", None),
                source_path=str(report_path),
            )
            n = db.insert_modules(conn, snapshot_id, module_dicts)
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        conn.close()
        return 1
    conn.close()
    print(f"Recorded {n} modules from {report_path} (snapshot #{snapshot_id})")
    return 0
