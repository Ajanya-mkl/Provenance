"""The one writer for onboarded datasets, and their registry.

Every write to `meta.*` and to a dataset's `ds_<id>` schema goes through
`writer()`: one read-write connection, one transaction, like the agent's
store.py. Reads use `connect_read_only` (with lock retry). Values are always
bound parameters; the only identifiers ever placed in SQL are generated schema
names, validated by `identifiers`.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from provenance.datasets.gen_suite import Rule
from provenance.datasets.profile import DatasetProfile
from provenance.db import connect_read_only

# "mapped" (Phase 10b) is reached only through an approved column mapping.
STATUSES = ("uploaded", "profiled", "gated", "quarantined", "failed", "mapped")
MAPPING_SOURCES = ("agent", "heuristic", "human")
MAPPING_STATUSES = ("proposed", "approved", "rejected", "superseded")
FINAL_STATUSES = ("gated", "quarantined", "mapped")
MAX_NAME_CHARS = 100

_DDL = (
    "CREATE SCHEMA IF NOT EXISTS meta",
    f"""
    CREATE TABLE IF NOT EXISTS meta.datasets (
        dataset_id VARCHAR PRIMARY KEY,
        name VARCHAR NOT NULL,
        source_filename VARCHAR NOT NULL,
        file_sha256 VARCHAR NOT NULL UNIQUE,
        status VARCHAR NOT NULL
            CHECK (status IN ({", ".join(f"'{status}'" for status in STATUSES)})),
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL,
        row_count BIGINT,
        column_count INTEGER,
        schema_name VARCHAR NOT NULL,
        encoding VARCHAR,
        delimiter VARCHAR,
        status_reason VARCHAR,
        profile_warnings JSON,
        suite JSON,
        gate_report JSON
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS meta.dataset_columns (
        dataset_id VARCHAR NOT NULL,
        ordinal INTEGER NOT NULL,
        column_name VARCHAR NOT NULL,
        inferred_type VARCHAR NOT NULL,
        type_share DOUBLE,
        null_pct DOUBLE NOT NULL,
        distinct_count BIGINT NOT NULL,
        min_value VARCHAR,
        max_value VARCHAR,
        is_candidate_key BOOLEAN NOT NULL,
        looks_like_date BOOLEAN NOT NULL,
        date_format VARCHAR,
        is_mixed_type BOOLEAN NOT NULL,
        is_mostly_null BOOLEAN NOT NULL,
        PRIMARY KEY (dataset_id, ordinal)
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS meta.mappings (
        mapping_id VARCHAR PRIMARY KEY,
        dataset_id VARCHAR NOT NULL,
        version INTEGER NOT NULL,
        proposal JSON NOT NULL,
        source VARCHAR NOT NULL
            CHECK (source IN ({", ".join(f"'{source}'" for source in MAPPING_SOURCES)})),
        status VARCHAR NOT NULL
            CHECK (status IN ({", ".join(f"'{status}'" for status in MAPPING_STATUSES)})),
        mappable BOOLEAN NOT NULL,
        scoreable BOOLEAN NOT NULL,
        reasons JSON NOT NULL,
        validation JSON NOT NULL,
        created_by VARCHAR NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        prompt_tokens INTEGER,
        completion_tokens INTEGER,
        decided_by VARCHAR,
        reason VARCHAR,
        decided_at TIMESTAMPTZ,
        superseded_by VARCHAR,
        UNIQUE (dataset_id, version)
    )
    """,
    # Scoring rules frozen at a dataset's first scoring (Phase 10c): normalisation
    # bounds, effective weights and which factors were left out. A later scoring
    # reuses the latest version; only an explicit refreeze adds a new one.
    """
    CREATE TABLE IF NOT EXISTS meta.dataset_rules (
        dataset_id VARCHAR NOT NULL,
        version INTEGER NOT NULL,
        rules_version VARCHAR NOT NULL,
        bounds_mode VARCHAR NOT NULL CHECK (bounds_mode IN ('dataset', 'flagship')),
        mapping_id VARCHAR NOT NULL,
        rules JSON NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (dataset_id, version)
    )
    """,
)


def clean_name(name: str) -> str:
    """A display name: stored only as a bound value, but kept printable and short."""
    text = re.sub(r"[\x00-\x1f\x7f]", " ", str(name or "")).strip()
    if not text:
        raise ValueError("A dataset name is required.")
    return text[:MAX_NAME_CHARS]


def ensure_meta(connection: duckdb.DuckDBPyConnection) -> None:
    for statement in _DDL:
        connection.execute(statement)


@contextmanager
def writer(db_path: str | Path) -> Iterator[duckdb.DuckDBPyConnection]:
    """The single read-write connection for dataset onboarding, one transaction."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(db_path)) as connection:
        ensure_meta(connection)
        connection.execute("BEGIN TRANSACTION")
        try:
            yield connection
        except BaseException:
            connection.execute("ROLLBACK")
            raise
        connection.execute("COMMIT")


def _now() -> datetime:
    return datetime.now(UTC)


def _has_registry(connection: duckdb.DuckDBPyConnection) -> bool:
    return bool(
        connection.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'meta' AND table_name = 'datasets'"
        ).fetchone()
    )


def find_by_hash(db_path: str | Path, file_sha256: str) -> dict[str, Any] | None:
    if not Path(db_path).exists():
        return None
    with connect_read_only(db_path) as connection:
        if not _has_registry(connection):
            return None
        row = connection.execute(
            "SELECT dataset_id, name, status, created_at, schema_name "
            "FROM meta.datasets WHERE file_sha256 = ?",
            [file_sha256],
        ).fetchone()
    if row is None:
        return None
    return dict(
        zip(("dataset_id", "name", "status", "created_at", "schema_name"), row, strict=True)
    )


def list_datasets(db_path: str | Path) -> list[dict[str, Any]]:
    if not Path(db_path).exists():
        return []
    with connect_read_only(db_path) as connection:
        if not _has_registry(connection):
            return []
        rows = connection.execute(
            """
            SELECT dataset_id, name, status, row_count, column_count, schema_name,
                   source_filename, created_at
            FROM meta.datasets ORDER BY created_at
            """
        ).fetchall()
    columns = (
        "dataset_id", "name", "status", "row_count", "column_count", "schema_name",
        "source_filename", "created_at",
    )  # fmt: skip
    return [dict(zip(columns, row, strict=True)) for row in rows]


def register(
    connection: duckdb.DuckDBPyConnection,
    *,
    dataset_id: str,
    name: str,
    source_filename: str,
    file_sha256: str,
    schema_name: str,
) -> None:
    """Insert the dataset as 'uploaded', or keep an earlier incomplete registration."""
    now = _now()
    connection.execute(
        """
        INSERT INTO meta.datasets
            (dataset_id, name, source_filename, file_sha256, status, created_at, updated_at,
             schema_name)
        VALUES (?, ?, ?, ?, 'uploaded', ?, ?, ?)
        ON CONFLICT (dataset_id) DO UPDATE
            SET name = excluded.name, updated_at = excluded.updated_at
        """,
        [dataset_id, clean_name(name), source_filename, file_sha256, now, now, schema_name],
    )


def record_profile(
    connection: duckdb.DuckDBPyConnection,
    dataset_id: str,
    profile: DatasetProfile,
    rules: list[Rule],
) -> None:
    # Upsert, not delete-then-insert: DuckDB rejects re-inserting a deleted key in
    # one transaction. Re-profiling the same bytes yields the same columns.
    connection.executemany(
        """
        INSERT OR REPLACE INTO meta.dataset_columns
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                dataset_id,
                column.ordinal,
                column.column_name,
                column.inferred_type,
                column.type_share,
                column.null_pct,
                column.distinct_count,
                column.min_value,
                column.max_value,
                column.is_candidate_key,
                column.looks_like_date,
                column.date_format,
                column.is_mixed_type,
                column.is_mostly_null,
            )
            for column in profile.columns
        ],
    )
    connection.execute(
        """
        UPDATE meta.datasets
        SET status = 'profiled', updated_at = ?, row_count = ?, column_count = ?,
            encoding = ?, delimiter = ?, profile_warnings = ?::JSON, suite = ?::JSON
        WHERE dataset_id = ?
        """,
        [
            _now(),
            profile.row_count,
            profile.column_count,
            profile.encoding,
            profile.delimiter,
            json.dumps(profile.warnings),
            json.dumps([rule.__dict__ for rule in rules], default=str),
            dataset_id,
        ],
    )


def set_status(
    connection: duckdb.DuckDBPyConnection,
    dataset_id: str,
    status: str,
    *,
    reason: str | None = None,
    gate_report: dict[str, Any] | None = None,
) -> None:
    if status not in STATUSES:
        raise ValueError(f"Unknown dataset status {status!r}")
    connection.execute(
        """
        UPDATE meta.datasets
        SET status = ?, updated_at = ?, status_reason = ?,
            gate_report = coalesce(?::JSON, gate_report)
        WHERE dataset_id = ?
        """,
        [
            status,
            _now(),
            reason,
            json.dumps(gate_report, default=str) if gate_report is not None else None,
            dataset_id,
        ],
    )


def get_dataset(db_path: str | Path, dataset_id: str) -> dict[str, Any] | None:
    """One dataset with its profiled columns, generated rules and gate report."""
    if not Path(db_path).exists():
        return None
    with connect_read_only(db_path) as connection:
        if not _has_registry(connection):
            return None
        row = connection.execute(
            """
            SELECT dataset_id, name, source_filename, status, row_count, column_count,
                   schema_name, encoding, delimiter, created_at, updated_at, status_reason,
                   profile_warnings, suite, gate_report
            FROM meta.datasets WHERE dataset_id = ?
            """,
            [dataset_id],
        ).fetchone()
        if row is None:
            return None
        keys = (
            "dataset_id", "name", "source_filename", "status", "row_count", "column_count",
            "schema_name", "encoding", "delimiter", "created_at", "updated_at",
            "status_reason", "profile_warnings", "suite", "gate_report",
        )  # fmt: skip
        dataset = dict(zip(keys, row, strict=True))
        for key in ("profile_warnings", "suite", "gate_report"):
            if isinstance(dataset[key], str):
                dataset[key] = json.loads(dataset[key])
        columns = connection.execute(
            "SELECT * FROM meta.dataset_columns WHERE dataset_id = ? ORDER BY ordinal",
            [dataset_id],
        )
        names = [description[0] for description in columns.description]
        dataset["columns"] = [
            dict(zip(names, values, strict=True)) for values in columns.fetchall()
        ]
    return dataset


def frozen_rules(connection: duckdb.DuckDBPyConnection, dataset_id: str) -> dict[str, Any] | None:
    """The latest frozen rules for a dataset, or None before its first scoring."""
    row = connection.execute(
        """
        SELECT version, rules_version, bounds_mode, mapping_id, rules, created_at
        FROM meta.dataset_rules WHERE dataset_id = ? ORDER BY version DESC LIMIT 1
        """,
        [dataset_id],
    ).fetchone()
    if row is None:
        return None
    version, rules_version, bounds_mode, mapping_id, rules, created_at = row
    return {
        "version": version,
        "rules_version": rules_version,
        "bounds_mode": bounds_mode,
        "mapping_id": mapping_id,
        "rules": json.loads(rules) if isinstance(rules, str) else rules,
        "created_at": created_at,
    }


def freeze_rules(
    connection: duckdb.DuckDBPyConnection,
    dataset_id: str,
    *,
    version: int,
    rules_version: str,
    bounds_mode: str,
    mapping_id: str,
    rules: dict[str, Any],
) -> None:
    """Append a frozen rules version (never updates an earlier one)."""
    connection.execute(
        """
        INSERT INTO meta.dataset_rules
            (dataset_id, version, rules_version, bounds_mode, mapping_id, rules, created_at)
        VALUES (?, ?, ?, ?, ?, ?::JSON, ?)
        """,
        [
            dataset_id,
            version,
            rules_version,
            bounds_mode,
            mapping_id,
            json.dumps(rules, default=str, sort_keys=True),
            _now(),
        ],
    )
