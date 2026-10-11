from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from provenance.ingestion import common

API_URL = "https://api.worldbank.org/v2/country/all/indicator/LP.LPI.OVRL.XQ"
PER_PAGE = 1000
SOURCE = "worldbank_lpi"


def load_worldbank_lpi(
    *,
    db_path: str | Path = common.DEFAULT_DB_PATH,
    bronze_root: str | Path = common.DEFAULT_BRONZE_ROOT,
) -> int:
    """Fetch every page of raw World Bank LPI observations and persist them to bronze."""
    rows: list[dict[str, Any]] = []
    page = 1
    page_count: int | None = None
    while page_count is None or page <= page_count:
        response = common.fetch_json(
            f"{API_URL}?format=json&per_page={PER_PAGE}&page={page}"
        )
        if not isinstance(response, list) or len(response) != 2:
            raise ValueError("World Bank response must be a [metadata, records] array")
        metadata, records = response
        if not isinstance(metadata, dict) or not isinstance(records, list):
            raise ValueError("World Bank response metadata or records have an unexpected shape")
        page_count = int(metadata["pages"])
        rows.extend(records)
        page += 1

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