from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.error import URLError

import pandas as pd

from provenance.ingestion import common

API_URL = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
SOURCE = "usaspending_contracts"
DEFAULT_CACHE_PATH = Path("data") / "raw" / "usaspending_sample.json"
PAGE_SIZE = 100
FIELDS = [
    "Award ID",
    "Recipient Name",
    "Recipient UEI",
    "recipient_id",
    "Start Date",
    "End Date",
    "Award Amount",
    "Awarding Agency",
    "Awarding Sub Agency",
    "Contract Award Type",
    "NAICS",
    "PSC",
]
LOGGER = logging.getLogger(__name__)


def _last_completed_month(today: date | None = None) -> tuple[str, str]:
    current_date = today or datetime.now(UTC).date()
    end_date = current_date.replace(day=1) - timedelta(days=1)
    start_date = end_date.replace(day=1)
    return start_date.isoformat(), end_date.isoformat()


def _request_payload(page: int, start_date: str, end_date: str) -> dict[str, Any]:
    return {
        "filters": {
            "award_type_codes": ["A", "B", "C", "D"],
            "time_period": [{"start_date": start_date, "end_date": end_date}],
        },
        "fields": FIELDS,
        "limit": PAGE_SIZE,
        "order": "desc",
        "page": page,
        "sort": "Award ID",
        "subawards": False,
    }


def _read_cached_page(cache_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    cached = json.loads(cache_path.read_text(encoding="utf-8"))
    return cached["request"], cached["response"]


def load_usaspending_contracts(
    *,
    db_path: str | Path = common.DEFAULT_DB_PATH,
    bronze_root: str | Path = common.DEFAULT_BRONZE_ROOT,
    cache_path: str | Path = DEFAULT_CACHE_PATH,
    today: date | None = None,
    allow_cached_fallback: bool = True,
    on_cached_fallback: Callable[[str, str], None] | None = None,
) -> int:
    """Fetch and paginate a month's contract awards, falling back to a cached API page."""
    start_date, end_date = _last_completed_month(today)
    cache_file = Path(cache_path)
    first_payload = _request_payload(1, start_date, end_date)

    responses: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    payload = first_payload
    try:
        while True:
            response = common.fetch_json(API_URL, method="POST", payload=payload)
            if not isinstance(response.get("results"), list):
                raise ValueError("USAspending response is missing its results array")
            if not isinstance(response.get("page_metadata"), dict):
                raise ValueError("USAspending response is missing page_metadata")
            if payload["page"] == 1:
                cache_file.parent.mkdir(parents=True, exist_ok=True)
                cache_file.write_text(
                    json.dumps(
                        {"request": first_payload, "response": response},
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
            responses.append(response)
            rows.extend(response["results"])
            metadata = response["page_metadata"]
            if not metadata["hasNext"]:
                break
            payload = _request_payload(metadata["page"] + 1, start_date, end_date)
    except (URLError, TimeoutError) as error:
        if not allow_cached_fallback or not cache_file.exists():
            raise
        LOGGER.warning(
            "USAspending is unavailable (%s); loading the cached successful sample at %s",
            error,
            cache_file,
        )
        cached_request, cached_response = _read_cached_page(cache_file)
        if not isinstance(cached_response.get("results"), list):
            raise ValueError("Cached USAspending response is missing its results array")
        rows = cached_response["results"]
        batch_id = common.payload_sha256(
            {"request": cached_request, "response": cached_response}
        )
        if on_cached_fallback is not None:
            on_cached_fallback(str(cache_file), str(error))
    else:
        batch_id = common.payload_sha256(
            {"request": first_payload, "responses": responses}
        )

    return common.write_bronze_dataframe(
        pd.DataFrame.from_records(rows),
        source=SOURCE,
        batch_id=batch_id,
        db_path=db_path,
        bronze_root=bronze_root,
    )