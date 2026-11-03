"""One-off repair: undo flag events superseded by float noise alone.

    python -m provenance.agent.repair_noise_events [--db-path PATH] [--dry-run]

Before the payload hash rounded floats, a gold-mart rebuild (which sums floats in
a different order each time) moved scores by ~1e-16 and superseded flag events
whose payload had not really changed. For every such pair (superseded event and
its replacement equal under the rounded hash) this reopens the original event,
resolves the replacement as a duplicate, and points the flag back at the
original. Genuine supersedes are left alone. Nothing is deleted, investigations
and actions are not touched, and the whole repair is one transaction with one
audit-log entry.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import duckdb

from provenance.agent.store import append_audit, writer
from provenance.risk_engine.scorer import DEFAULT_DB_PATH, payload_hash

SUPERSEDED_PREFIX = "superseded: payload changed; replaced by "


@dataclass(frozen=True)
class NoisePair:
    original: str
    duplicate: str


def _payload(raw: object) -> dict:
    return json.loads(raw) if isinstance(raw, str) else raw


def find_noise_pairs(connection: duckdb.DuckDBPyConnection) -> list[NoisePair]:
    """Superseded flag events whose replacement differs only by float noise."""
    rows = connection.execute(
        """
        SELECT old.event_id, old.payload, new.event_id, new.payload
        FROM agent.events AS old
        INNER JOIN agent.events AS new
            ON new.event_id = substr(old.resolution, length(?) + 1)
        WHERE old.event_type = 'risk_flag'
          AND starts_with(old.resolution, ?)
          AND new.resolved_at IS NULL
        ORDER BY old.event_id
        """,
        [SUPERSEDED_PREFIX, SUPERSEDED_PREFIX],
    ).fetchall()
    return [
        NoisePair(old_id, new_id)
        for old_id, old_payload, new_id, new_payload in rows
        if payload_hash(_payload(old_payload)) == payload_hash(_payload(new_payload))
    ]


def repair(db_path: str | Path, *, expected: int | None = None) -> list[NoisePair]:
    """Apply the repair in one transaction; returns the pairs it repaired."""
    with writer(db_path) as connection:
        pairs = find_noise_pairs(connection)
        if expected is not None and len(pairs) != expected:
            raise RuntimeError(
                f"Expected {expected} noise pairs, found {len(pairs)}; nothing was changed."
            )
        for pair in pairs:
            connection.execute(
                "UPDATE agent.events SET resolved_at = NULL, resolution = NULL "
                "WHERE event_id = ?",
                [pair.original],
            )
            connection.execute(
                "UPDATE agent.events SET resolved_at = current_timestamp, resolution = ? "
                "WHERE event_id = ?",
                [f"resolved: duplicate of {pair.original} (float noise)", pair.duplicate],
            )
            connection.execute(
                "UPDATE gold.supplier_risk_flags SET event_id = ? WHERE event_id = ?",
                [pair.original, pair.duplicate],
            )
        if pairs:
            append_audit(
                connection,
                "maintenance",
                "flag_events_repaired",
                {
                    "reason": "superseded by float noise from a gold-mart rebuild",
                    "pairs": [
                        {"reopened": pair.original, "resolved_duplicate": pair.duplicate}
                        for pair in pairs
                    ],
                },
            )
    return pairs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db-path", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true", help="list pairs, change nothing")
    parser.add_argument("--expect", type=int, default=None, help="refuse unless this many pairs")
    args = parser.parse_args(argv)
    db_path = args.db_path or Path(os.environ.get("PROVENANCE_DUCKDB_PATH", DEFAULT_DB_PATH))

    if args.dry_run:
        with duckdb.connect(str(db_path), read_only=True) as connection:
            pairs = find_noise_pairs(connection)
    else:
        pairs = repair(db_path, expected=args.expect)
    for pair in pairs:
        print(
            f"{'would reopen' if args.dry_run else 'reopened'} {pair.original}  "
            f"<- duplicate {pair.duplicate}"
        )
    print(f"{len(pairs)} noise pair(s){' (dry run)' if args.dry_run else ' repaired'}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
