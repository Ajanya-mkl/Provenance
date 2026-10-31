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


# DuckDB fails instantly when another process holds the file, in both
# directions, so readers retry briefly before reporting the warehouse as busy.
READ_RETRY_DELAYS = (0.25, 0.5)
LOCK_ERROR_MARKERS = ("could not set lock", "already open", "conflicting lock", "io error")


def is_lock_error(error: BaseException | str) -> bool:
    text = str(error).lower()
    return any(marker in text for marker in LOCK_ERROR_MARKERS)


def connect_read_only(
    db_path: str | Path,
    *,
    delays: tuple[float, ...] = READ_RETRY_DELAYS,
    sleep=None,
) -> duckdb.DuckDBPyConnection:
    """Open the warehouse read-only, retrying a lock conflict up to len(delays) times."""
    import time

    pause = sleep or time.sleep
    for attempt in range(len(delays) + 1):
        try:
            return duckdb.connect(str(db_path), read_only=True)
        except duckdb.IOException:
            if attempt == len(delays):
                raise
            pause(delays[attempt])
    raise AssertionError("unreachable")
