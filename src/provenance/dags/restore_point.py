"""All-or-nothing silver and gold builds for DuckDB.

Before the silver dbt build, every dbt-built table in the silver and gold schemas
is copied into a `_restore_point` schema. If the silver or the gold build fails
(a failing dbt test included), those tables are restored in one transaction, so
the warehouse never shows new silver beside old gold. The restore point is
dropped once gold succeeds.

Why not build into a staging schema and swap: dbt-duckdb resolves refs to fixed
schema names and DuckDB has no atomic schema rename, so a swap would need a
second dbt target plus table-by-table renames. Copying ~20 tables (about 200k
rows) takes well under a second and needs no change to the dbt project.

Tables with a PRIMARY KEY or UNIQUE constraint (the scorer's own tables) are not
dbt-built and are left alone; the scorer writes them in its own transaction.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import duckdb
from dagster import Failure, RetryRequested

from provenance.db import is_lock_error

LOGGER = logging.getLogger(__name__)
SCHEMA = "_restore_point"
GUARDED_SCHEMAS = ("silver", "gold")
LOCK_RETRIES = 3
LOCK_RETRY_SECONDS = 5


def _dbt_tables(connection: duckdb.DuckDBPyConnection) -> list[tuple[str, str]]:
    constrained = {
        (schema, table)
        for schema, table in connection.execute(
            """
            SELECT DISTINCT schema_name, table_name FROM duckdb_constraints()
            WHERE constraint_type IN ('PRIMARY KEY', 'UNIQUE')
            """
        ).fetchall()
    }
    rows = connection.execute(
        f"""
        SELECT table_schema, table_name FROM information_schema.tables
        WHERE table_type = 'BASE TABLE'
          AND table_schema IN ({", ".join("?" for _ in GUARDED_SCHEMAS)})
        ORDER BY 1, 2
        """,
        list(GUARDED_SCHEMAS),
    ).fetchall()
    return [(schema, table) for schema, table in rows if (schema, table) not in constrained]


def _copy_name(schema: str, table: str) -> str:
    return f"{schema}__{table}"


def has_restore_point(db_path: str | Path) -> bool:
    with duckdb.connect(str(db_path)) as connection:
        return bool(
            connection.execute(
                "SELECT 1 FROM information_schema.schemata WHERE schema_name = ?", [SCHEMA]
            ).fetchone()
        )


def create_restore_point(db_path: str | Path) -> list[str]:
    """Copy every dbt-built silver/gold table; replaces any older restore point."""
    with duckdb.connect(str(db_path)) as connection:
        connection.execute("BEGIN TRANSACTION")
        connection.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        connection.execute(f"CREATE SCHEMA {SCHEMA}")
        copied = []
        for schema, table in _dbt_tables(connection):
            connection.execute(
                f'CREATE TABLE {SCHEMA}."{_copy_name(schema, table)}" AS '
                f'SELECT * FROM "{schema}"."{table}"'
            )
            copied.append(f"{schema}.{table}")
        connection.execute(
            f"CREATE TABLE {SCHEMA}.manifest AS SELECT ?::JSON AS tables", [json.dumps(copied)]
        )
        connection.execute("COMMIT")
    LOGGER.info("Restore point holds %d silver/gold tables", len(copied))
    return copied


def restore(db_path: str | Path) -> list[str]:
    """Put silver and gold back exactly as the restore point saw them, atomically."""
    with duckdb.connect(str(db_path)) as connection:
        manifest = connection.execute(f"SELECT tables FROM {SCHEMA}.manifest").fetchone()
        saved = json.loads(manifest[0])
        connection.execute("BEGIN TRANSACTION")
        try:
            for schema, table in _dbt_tables(connection):
                if f"{schema}.{table}" not in saved:
                    # Created by the failed build: it did not exist before.
                    connection.execute(f'DROP TABLE "{schema}"."{table}"')
            for name in saved:
                schema, table = name.split(".", 1)
                connection.execute(f'DROP TABLE IF EXISTS "{schema}"."{table}"')
                connection.execute(f'DROP VIEW IF EXISTS "{schema}"."{table}"')
                connection.execute(
                    f'CREATE TABLE "{schema}"."{table}" AS '
                    f'SELECT * FROM {SCHEMA}."{_copy_name(schema, table)}"'
                )
        except BaseException:
            connection.execute("ROLLBACK")
            raise
        connection.execute("COMMIT")
    LOGGER.warning("Restored %d silver/gold tables after a failed build", len(saved))
    return saved


def drop_restore_point(db_path: str | Path) -> None:
    with duckdb.connect(str(db_path)) as connection:
        connection.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")


def _error_text(invocation: Any) -> str:
    parts = []
    for getter in ("get_error",):
        try:
            error = getattr(invocation, getter)()
        except Exception:  # noqa: BLE001 - diagnostics only
            error = None
        if error is not None:
            parts.append(str(error))
    try:
        results = invocation.get_artifact("run_results.json")
        parts += [str(result.get("message", "")) for result in results.get("results", [])]
    except Exception:  # noqa: BLE001 - the artifact may not exist when dbt cannot start
        pass
    return "\n".join(parts)


def guarded_dbt_build(
    context: Any,
    dbt: Any,
    args: list[str],
    *,
    db_path: str | Path,
    phase: str,
) -> Iterator[Any]:
    """Run `dbt build` for one phase ("silver" or "gold") with all-or-nothing semantics.

    Silver starts the restore point; gold drops it on success. Any failure
    restores both layers. A lock conflict changes nothing (dbt could not open
    the file), so it keeps the restore point and asks Dagster to retry.
    """
    if phase not in ("silver", "gold"):
        raise ValueError(f"Unknown dbt phase {phase!r}")
    if phase == "silver" or not has_restore_point(db_path):
        create_restore_point(db_path)

    invocation = dbt.cli(args, context=context, raise_on_error=False)
    yield from invocation.stream()
    if invocation.is_successful():
        if phase == "gold":
            drop_restore_point(db_path)
        return

    error_text = _error_text(invocation)
    if is_lock_error(error_text):
        raise RetryRequested(max_retries=LOCK_RETRIES, seconds_to_wait=LOCK_RETRY_SECONDS)
    restored = restore(db_path)
    drop_restore_point(db_path)
    failing = re.findall(r"(?:Failure|Error) in (?:test|model) (\S+)", error_text)
    raise Failure(
        description=(
            f"dbt {phase} build failed"
            + (f" ({', '.join(sorted(set(failing)))})" if failing else "")
            + f"; restored {len(restored)} silver and gold tables to the previous "
            "consistent state."
        ),
        metadata={"phase": phase, "restored_tables": len(restored)},
    )
