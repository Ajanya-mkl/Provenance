"""Generic bronze for onboarded datasets.

Every column is stored as text, losslessly (the inferred types live in meta),
with the same lineage columns as the flagship bronze tables. A batch is staged
as parquet under data/datasets/<id>/batch_id=<sha>/ with a generated filename,
then either loaded into ds_<id>.bronze_raw (gate passed) or MOVED to
data/quarantine/datasets/<id>/ (gate failed), never copied.
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from provenance.agent.store import append_audit, ensure_agent_tables
from provenance.data_quality.gate import ensure_agent_events_table
from provenance.datasets.identifiers import DATASET_ID_PATTERN, quote_schema, schema_for

LINEAGE_COLUMNS = ("_batch_id", "_source", "_source_file", "_loaded_at")
PARQUET_NAME = "part-0.parquet"
REPORT_NAME = "gate_report.json"
AUDIT_ACTOR = "onboarding"


def _batch_dir(root: Path, dataset_id: str, batch_id: str) -> Path:
    if not DATASET_ID_PATTERN.match(dataset_id) or not all(
        c in "0123456789abcdef" for c in batch_id
    ):
        raise ValueError("dataset and batch ids must be generated hex values")
    return Path(root) / dataset_id / f"batch_id={batch_id}"


def stage_batch(
    frame: pd.DataFrame,
    *,
    dataset_id: str,
    batch_id: str,
    source_filename: str,
    data_root: Path,
) -> Path:
    """Write the raw text batch plus lineage to parquet; returns the batch folder."""
    target = _batch_dir(data_root, dataset_id, batch_id)
    target.mkdir(parents=True, exist_ok=True)
    output = frame.astype(str).copy()
    output["_batch_id"] = batch_id
    output["_source"] = f"dataset:{dataset_id}"
    output["_source_file"] = source_filename
    output["_loaded_at"] = datetime.now(UTC)
    output.to_parquet(target / PARQUET_NAME, index=False)
    return target


def load_bronze(
    connection: duckdb.DuckDBPyConnection,
    *,
    dataset_id: str,
    batch_dir: Path,
    expected_columns: list[str],
) -> int:
    """Create ds_<id>.bronze_raw from the staged parquet (writer connection)."""
    schema = quote_schema(schema_for(dataset_id))
    connection.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    # The parquet path is bound as a parameter; column names come from the file
    # itself, never from SQL text.
    connection.execute(
        f"CREATE OR REPLACE TABLE {schema}.bronze_raw AS "
        "SELECT * FROM read_parquet(?, hive_partitioning = false)",
        [str((batch_dir / PARQUET_NAME).resolve())],
    )
    loaded = [
        row[0]
        for row in connection.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = ? AND table_name = 'bronze_raw' ORDER BY ordinal_position",
            [schema_for(dataset_id)],
        ).fetchall()
    ]
    if loaded != [*expected_columns, *LINEAGE_COLUMNS]:
        raise RuntimeError("bronze_raw columns do not match the profiled columns")
    return connection.execute(f"SELECT count(*) FROM {schema}.bronze_raw").fetchone()[0]


def quarantine_batch(
    batch_dir: Path, *, dataset_id: str, batch_id: str, quarantine_root: Path, report: dict
) -> Path:
    """Move (never copy) a failed batch out of data/datasets, with its gate report."""
    target = _batch_dir(quarantine_root, dataset_id, batch_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        shutil.rmtree(target)
    shutil.move(str(batch_dir), str(target))
    if batch_dir.parent.exists() and not any(batch_dir.parent.iterdir()):
        batch_dir.parent.rmdir()
    (target / REPORT_NAME).write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return target


def record_failure_event(
    connection: duckdb.DuckDBPyConnection,
    *,
    dataset_id: str,
    batch_id: str,
    name: str,
    summary: dict[str, Any],
) -> str:
    """A dq_failure event (agent handoff) plus an audit-log entry, same transaction."""
    ensure_agent_tables(connection)  # creates the agent schema and audit log
    ensure_agent_events_table(connection)
    event_id = f"dq_failure:dataset:{dataset_id}:{batch_id[:12]}"
    payload = {"dataset_id": dataset_id, "dataset_name": name, **summary}
    connection.execute(
        """
        INSERT INTO agent.events
            (event_id, event_type, source, batch_id, created_at, payload)
        VALUES (?, 'dq_failure', ?, ?, ?, ?::JSON)
        ON CONFLICT (event_id) DO NOTHING
        """,
        [event_id, f"dataset:{dataset_id}", batch_id, datetime.now(UTC), json.dumps(payload)],
    )
    append_audit(
        connection,
        AUDIT_ACTOR,
        "dataset_quarantined",
        {"dataset_id": dataset_id, "batch_id": batch_id, "event_id": event_id, **summary},
    )
    return event_id


def record_success_audit(
    connection: duckdb.DuckDBPyConnection,
    *,
    dataset_id: str,
    batch_id: str,
    rows: int,
    summary: dict,
) -> None:
    ensure_agent_tables(connection)
    append_audit(
        connection,
        AUDIT_ACTOR,
        "dataset_gated",
        {"dataset_id": dataset_id, "batch_id": batch_id, "rows": rows, **summary},
    )
