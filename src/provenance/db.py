from __future__ import annotations

from pathlib import Path

import duckdb

SCHEMAS = ("bronze", "silver", "gold", "agent")
DEFAULT_DB_PATH = Path("data") / "provenance.duckdb"


def init_db(db_path: str | Path = DEFAULT_DB_PATH) -> str:
    """Create the DuckDB database file and required schemas if they do not exist."""
    target_path = Path(db_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    with duckdb.connect(str(target_path)) as conn:
        for schema_name in SCHEMAS:
            conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema_name}"')

    return str(target_path)
