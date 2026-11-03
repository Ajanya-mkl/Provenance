"""The one writer for the agent's own tables.

Every write to agent.investigations, agent.actions, agent.audit_log,
agent.watchlist and agent.review_requests goes through writer(). The audit log
is append-only: this module only ever INSERTs into it, and each row carries a
hash of the previous row so an edited or deleted row is detectable.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

AGENT_TABLES = ("investigations", "actions", "audit_log", "watchlist", "review_requests")
ALLOWED_ACTIONS = ("no_action", "watchlist", "request_review")
AUDIT_GENESIS = "0" * 64

_DDL = (
    """
    CREATE TABLE IF NOT EXISTS agent.investigations (
        investigation_id VARCHAR PRIMARY KEY,
        event_id VARCHAR NOT NULL,
        report JSON,
        status VARCHAR NOT NULL
            CHECK (status IN ('proposed', 'rejected_ungrounded', 'no_report')),
        grounded BOOLEAN NOT NULL,
        grounded_ratio DOUBLE NOT NULL,
        ungrounded_numbers JSON NOT NULL,
        tool_calls JSON NOT NULL,
        steps INTEGER NOT NULL,
        prompt_tokens INTEGER NOT NULL,
        completion_tokens INTEGER NOT NULL,
        total_tokens INTEGER NOT NULL,
        model VARCHAR NOT NULL,
        requires_approval BOOLEAN NOT NULL,
        created_at TIMESTAMPTZ NOT NULL
    )
    """,
    # Phase 7b: the human decision is written back so later runs can recall it.
    "ALTER TABLE agent.investigations ADD COLUMN IF NOT EXISTS human_decision VARCHAR",
    "ALTER TABLE agent.investigations ADD COLUMN IF NOT EXISTS decision_reason VARCHAR",
    "ALTER TABLE agent.investigations ADD COLUMN IF NOT EXISTS decided_by VARCHAR",
    "ALTER TABLE agent.investigations ADD COLUMN IF NOT EXISTS decided_at TIMESTAMPTZ",
    # Phase 10c: NULL for DataCo, the onboarded dataset's id otherwise.
    "ALTER TABLE agent.investigations ADD COLUMN IF NOT EXISTS dataset_id VARCHAR",
    f"""
    CREATE TABLE IF NOT EXISTS agent.actions (
        action_id VARCHAR PRIMARY KEY,
        investigation_id VARCHAR NOT NULL,
        event_id VARCHAR NOT NULL,
        supplier_key VARCHAR,
        quarter DATE,
        action_type VARCHAR NOT NULL
            CHECK (action_type IN ({", ".join(f"'{name}'" for name in ALLOWED_ACTIONS)})),
        status VARCHAR NOT NULL
            CHECK (status IN ('proposed', 'approved', 'rejected', 'applied')),
        proposed_at TIMESTAMPTZ NOT NULL,
        decided_at TIMESTAMPTZ,
        decided_by VARCHAR,
        reason VARCHAR,
        applied_at TIMESTAMPTZ
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS agent.audit_log (
        seq BIGINT PRIMARY KEY,
        ts TIMESTAMPTZ NOT NULL,
        actor VARCHAR NOT NULL,
        event VARCHAR NOT NULL,
        detail JSON NOT NULL,
        prev_hash VARCHAR NOT NULL,
        row_hash VARCHAR NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS agent.watchlist (
        action_id VARCHAR PRIMARY KEY,
        event_id VARCHAR NOT NULL,
        supplier_key VARCHAR,
        quarter DATE,
        added_by VARCHAR NOT NULL,
        added_at TIMESTAMPTZ NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS agent.review_requests (
        action_id VARCHAR PRIMARY KEY,
        event_id VARCHAR NOT NULL,
        supplier_key VARCHAR,
        quarter DATE,
        requested_by VARCHAR NOT NULL,
        reason VARCHAR,
        requested_at TIMESTAMPTZ NOT NULL
    )
    """,
)


def ensure_agent_tables(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute("CREATE SCHEMA IF NOT EXISTS agent")
    for statement in _DDL:
        connection.execute(statement)


@contextmanager
def writer(db_path: str | Path) -> Iterator[duckdb.DuckDBPyConnection]:
    """Open the single read-write connection and run the body in one transaction."""
    with duckdb.connect(str(db_path)) as connection:
        ensure_agent_tables(connection)
        connection.execute("BEGIN TRANSACTION")
        try:
            yield connection
        except BaseException:
            connection.execute("ROLLBACK")
            raise
        connection.execute("COMMIT")


def now() -> datetime:
    return datetime.now(UTC)


def _audit_hash(
    seq: int, ts: datetime, actor: str, event: str, detail_json: str, prev_hash: str
) -> str:
    canonical = json.dumps(
        [seq, ts.astimezone(UTC).isoformat(), actor, event, detail_json, prev_hash],
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def append_audit(
    connection: duckdb.DuckDBPyConnection,
    actor: str,
    event: str,
    detail: dict[str, Any],
) -> int:
    """Append one audit row. There is deliberately no update or delete helper."""
    last = connection.execute(
        "SELECT seq, row_hash FROM agent.audit_log ORDER BY seq DESC LIMIT 1"
    ).fetchone()
    seq = int(last[0]) + 1 if last else 1
    prev_hash = last[1] if last else AUDIT_GENESIS
    ts = now()
    detail_json = json.dumps(detail, sort_keys=True, default=str)
    connection.execute(
        "INSERT INTO agent.audit_log VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            seq,
            ts,
            actor,
            event,
            detail_json,
            prev_hash,
            _audit_hash(seq, ts, actor, event, detail_json, prev_hash),
        ],
    )
    return seq


def verify_audit_log(connection: duckdb.DuckDBPyConnection) -> list[int]:
    """Return the seq of every row whose hash chain is broken (empty when intact)."""
    rows = connection.execute(
        "SELECT seq, ts, actor, event, detail::VARCHAR, prev_hash, row_hash "
        "FROM agent.audit_log ORDER BY seq"
    ).fetchall()
    broken = []
    expected_prev, expected_seq = AUDIT_GENESIS, 1
    for seq, ts, actor, event, detail_json, prev_hash, row_hash in rows:
        recomputed = _audit_hash(seq, ts, actor, event, detail_json, prev_hash)
        if seq != expected_seq or prev_hash != expected_prev or row_hash != recomputed:
            broken.append(int(seq))
        expected_prev, expected_seq = row_hash, int(seq) + 1
    return broken


def table_exists(connection: duckdb.DuckDBPyConnection, schema: str, table: str) -> bool:
    return bool(
        connection.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_schema = ? AND table_name = ?",
            [schema, table],
        ).fetchone()
    )
