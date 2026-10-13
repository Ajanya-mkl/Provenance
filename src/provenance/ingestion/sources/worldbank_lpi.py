from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.error import URLError

import pandas as pd

from provenance.ingestion import common

API_URL = "https://api.worldbank.org/v2/country/all/indicator/LP.LPI.OVRL.XQ"
PER_PAGE = 1000
SOURCE = "worldbank_lpi"
LOGGER = logging.getLogger(__name__)


def _latest_cached_batch(bronze_root: Path) -> Path | None:
    candidates = list((bronze_root / SOURCE).glob("batch_id=*/part-0.parquet"))
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def load_worldbank_lpi(
    *,
    db_path: str | Path = common.DEFAULT_DB_PATH,
    bronze_root: str | Path = common.DEFAULT_BRONZE_ROOT,
    allow_cached_fallback: bool = True,
    on_cached_fallback: Callable[[str, str], None] | None = None,
) -> int:
    """Fetch every page of raw World Bank LPI observations and persist them to bronze."""
    rows: list[dict[str, Any]] = []
    page = 1
    page_count: int | None = None
    try:
        while page_count is None or page <= page_count:
            response = common.fetch_json(
                f"{API_URL}?format=json&per_page={PER_PAGE}&page={page}"
            )
            if not isinstance(response, list) or len(response) != 2:
                raise ValueError("World Bank response must be a [metadata, records] array")
            metadata, records = response
            if not isinstance(metadata, dict) or not isinstance(records, list):
                raise ValueError(
                    "World Bank response metadata or records have an unexpected shape"
                )
            page_count = int(metadata["pages"])
            rows.extend(records)
            page += 1
    except (URLError, TimeoutError) as error:
        cached_path = _latest_cached_batch(Path(bronze_root))
        if not allow_cached_fallback or cached_path is None:
            raise
        cached_frame = pd.read_parquet(cached_path)
        batch_ids = cached_frame["_batch_id"].dropna().unique()
        if len(batch_ids) != 1:
            raise ValueError(f"Cached World Bank batch is malformed: {cached_path}") from error
        batch_id = str(batch_ids[0])
        metadata_columns = {"_batch_id", "_source", "_source_file", "_loaded_at"}
        frame = cached_frame.drop(
            columns=[name for name in metadata_columns if name in cached_frame.columns]
        )
        LOGGER.warning(
            "World Bank API is unavailable (%s); reusing cached batch %s at %s",
            error,
            batch_id,
            cached_path,
        )
        if on_cached_fallback is not None:
            on_cached_fallback(str(cached_path), str(error))
        return common.write_bronze_dataframe(
            frame,
            source=SOURCE,
            batch_id=batch_id,
            source_file=API_URL,
            db_path=db_path,
            bronze_root=bronze_root,
        )

    batch_id = common.payload_sha256(
        {
            "request": {"url": API_URL, "format": "json", "per_page": PER_PAGE},
            "records": rows,
        }
    )
    return common.write_bronze_dataframe(
        pd.DataFrame.from_records(rows),
        source=SOURCE,
        batch_id=batch_id,
        source_file=API_URL,
        db_path=db_path,
        bronze_root=bronze_root,
    )