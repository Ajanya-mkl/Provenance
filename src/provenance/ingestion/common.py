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

    database_path = Path(db_path)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    parquet_files = sorted(str(path.resolve()) for path in target_root.rglob("*.parquet"))
    if not parquet_files:
        raise RuntimeError(f"No parquet batches found for bronze source {source}")

    with duckdb.connect(str(database_path)) as connection:
        connection.execute("CREATE SCHEMA IF NOT EXISTS bronze")
        connection.execute(
            f'CREATE OR REPLACE TABLE bronze."{source}" AS '
            "SELECT * FROM read_parquet(?, hive_partitioning=false)",
            [parquet_files],
        )
        row_count = connection.execute(
            f'SELECT COUNT(*) FROM bronze."{source}"'
        ).fetchone()[0]

    LOGGER.info(
        "Wrote bronze.%s batch %s with %d rows to %s",
        source,
        batch_id,
        row_count,
        parquet_path,
    )
    return row_count
