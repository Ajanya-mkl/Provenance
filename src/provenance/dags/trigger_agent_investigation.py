import hashlib
import json
import os
from pathlib import Path
from typing import Any

import duckdb
from dagster import (
    Array,
    DefaultSensorStatus,
    Field,
    OpExecutionContext,
    Permissive,
    RunRequest,
    SensorEvaluationContext,
    SkipReason,
    job,
    op,
    sensor,
)

from provenance.data_quality.gate import ensure_agent_events_table
from provenance.risk_engine.scorer import DEFAULT_DB_PATH

DUCKDB_WRITER_TAGS = {"dagster/concurrency_key": "duckdb_writer"}


def _database_path() -> Path:
    return Path(os.environ.get("PROVENANCE_DUCKDB_PATH", DEFAULT_DB_PATH))


def queue_open_events(db_path: str | Path) -> list[dict[str, Any]]:
    """Mark every open, unresolved risk or quality event queued and return its case."""
    with duckdb.connect(str(db_path)) as connection:
        connection.execute("CREATE SCHEMA IF NOT EXISTS agent")
        ensure_agent_events_table(connection)
        # DuckDB 1.1 rejects UPDATE ... RETURNING on a primary-key table, so
        # select and update inside one transaction instead.
        connection.execute("BEGIN TRANSACTION")
        rows = connection.execute(
            """
            SELECT event_id, event_type, source, batch_id, created_at, payload
            FROM agent.events
            WHERE event_type IN ('risk_flag', 'dq_failure')
              AND resolved_at IS NULL
              AND resolution IS NULL
              AND investigation_status = 'open'
            """
        ).fetchall()
        if rows:
            connection.execute(
                "UPDATE agent.events SET investigation_status = 'queued' "
                f"WHERE event_id IN ({', '.join('?' for _ in rows)})",
                [row[0] for row in rows],
            )
        connection.execute("COMMIT")
    cases = [
        {
            "event_id": str(event_id),
            "event_type": str(event_type),
            "source": str(source),
            "batch_id": str(batch_id),
            "created_at": created_at.isoformat(),
            "payload": json.loads(payload) if isinstance(payload, str) else payload,
        }
        for event_id, event_type, source, batch_id, created_at, payload in rows
    ]
    return sorted(cases, key=lambda case: (case["created_at"], case["event_id"]))


def build_investigation_request(db_path: str | Path) -> RunRequest | SkipReason:
    try:
        cases = queue_open_events(db_path)
    except duckdb.IOException as error:
        # A pipeline run holds the single DuckDB writer lock; try next tick.
        return SkipReason(f"Warehouse is locked by another writer: {error}")
    if not cases:
        return SkipReason("No open, unqueued risk or quality events.")
    event_ids = [case["event_id"] for case in cases]
    return RunRequest(
        run_key=hashlib.sha256("\n".join(event_ids).encode("utf-8")).hexdigest(),
        run_config={"ops": {"log_investigation_cases": {"config": {"cases": cases}}}},
        tags={**DUCKDB_WRITER_TAGS, "provenance/case_count": str(len(cases))},
    )


@op(config_schema={"cases": Field(Array(Permissive()), default_value=[])})
def log_investigation_cases(context: OpExecutionContext) -> int:
    """Phase 6 stub: log each queued case. Phase 7 replaces this with the agent."""
    cases = context.op_config["cases"]
    for case in cases:
        context.log.info(
            "Investigation case %s (%s): %s",
            case["event_id"],
            case["event_type"],
            json.dumps(case["payload"], sort_keys=True, default=str),
        )
    context.log.info("Received %d queued investigation case(s).", len(cases))
    return len(cases)


@job(tags=DUCKDB_WRITER_TAGS)
def agent_investigation_job() -> None:
    log_investigation_cases()


@sensor(
    name="agent_investigation_sensor",
    job=agent_investigation_job,
    minimum_interval_seconds=30,
    default_status=DefaultSensorStatus.STOPPED,
)
def agent_investigation_sensor(context: SensorEvaluationContext) -> RunRequest | SkipReason:
    return build_investigation_request(_database_path())
