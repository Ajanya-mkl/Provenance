from __future__ import annotations

import hashlib
import json
import logging
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

DEFAULT_DB_PATH = Path("data") / "provenance.duckdb"
DEFAULT_BRONZE_ROOT = Path("data") / "bronze"
LOGGER = logging.getLogger(__name__)
VALID_SOURCES = {"dataco_shipments", "usaspending_contracts", "worldbank_lpi"}
# Gate statuses that silver may read (same list as the dbt var).
APPROVED_STATUSES = ("validated", "passed_with_warnings")


def file_sha256(path: str | Path) -> str:
    """Return the SHA-256 digest of a file's exact contents."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as source_file:
        for chunk in iter(lambda: source_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def payload_sha256(payload: Any) -> str:
    """Hash an API request and response using a stable JSON representation."""
    serialized = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def fetch_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    timeout: int = 60,
) -> Any:
    """Fetch a JSON API response using the standard library."""
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def write_bronze_dataframe(
    frame: pd.DataFrame,
    *,
    source: str,
    batch_id: str,
    source_file: str | Path | None = None,
    db_path: str | Path = DEFAULT_DB_PATH,
    bronze_root: str | Path = DEFAULT_BRONZE_ROOT,
) -> int:
    """Add lineage metadata, replace the batch parquet, and refresh its DuckDB table."""
    if source not in VALID_SOURCES:
        raise ValueError(f"Unsupported bronze source: {source}")

    if batch_statuses(db_path, source).get(batch_id) == "quarantined":
        # The same content failed the gate before; never put it back into bronze.
        LOGGER.warning(
            "Skipping bronze.%s batch %s: it was quarantined by an earlier gate run",
            source,
            batch_id,
        )
        return rebuild_bronze_table(source, db_path=db_path, bronze_root=bronze_root)

    target_root = Path(bronze_root) / source
    batch_dir = target_root / f"batch_id={batch_id}"
    batch_dir.mkdir(parents=True, exist_ok=True)
    parquet_path = batch_dir / "part-0.parquet"

    output = frame.copy()
    output["_batch_id"] = batch_id
    output["_source"] = source
    output["_source_file"] = str(source_file) if source_file is not None else None
    output["_loaded_at"] = datetime.now(UTC)
    output.to_parquet(parquet_path, index=False)

    row_count = rebuild_bronze_table(source, db_path=db_path, bronze_root=bronze_root)

    LOGGER.info(
        "Wrote bronze.%s batch %s with %d rows to %s",
        source,
        batch_id,
        row_count,
        parquet_path,
    )
    return row_count


def batch_statuses(db_path: str | Path, source: str) -> dict[str, str]:
    """Gate status per batch id for one source ({} before the gate has ever run)."""
    database = Path(db_path)
    if not database.exists():
        return {}
    with duckdb.connect(str(database), read_only=True) as connection:
        exists = connection.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'bronze' AND table_name = 'dq_results'"
        ).fetchone()
        if not exists:
            return {}
        rows = connection.execute(
            "SELECT batch_id, status FROM bronze.dq_results WHERE source = ?", [source]
        ).fetchall()
    return {batch_id: status for batch_id, status in rows}


def rebuild_bronze_table(
    source: str,
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
    bronze_root: str | Path = DEFAULT_BRONZE_ROOT,
) -> int:
    """Rebuild bronze.<source> from approved batches plus batches awaiting the gate.

    A batch the gate rejected is never read (it is also moved out of bronze).
    After every gate run nothing is pending, so the table then holds approved
    batches only.
    """
    statuses = batch_statuses(db_path, source)
    target_root = Path(bronze_root) / source
    parquet_files = sorted(
        str(path.resolve())
        for path in target_root.rglob("*.parquet")
        if statuses.get(path.parent.name.removeprefix("batch_id="), "pending")
        in (*APPROVED_STATUSES, "pending")
    )
    database_path = Path(db_path)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(database_path)) as connection:
        connection.execute("CREATE SCHEMA IF NOT EXISTS bronze")
        if not parquet_files:
            LOGGER.warning("No approved or pending bronze batches for %s", source)
            connection.execute(f'DROP TABLE IF EXISTS bronze."{source}"')
            return 0
        connection.execute(
            f'CREATE OR REPLACE TABLE bronze."{source}" AS '
            "SELECT * FROM read_parquet(?, hive_partitioning=false, union_by_name=true)",
            [parquet_files],
        )
        return connection.execute(f'SELECT COUNT(*) FROM bronze."{source}"').fetchone()[0]
