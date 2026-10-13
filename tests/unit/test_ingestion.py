from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from urllib.error import URLError

import duckdb
import pandas as pd
import pytest

from provenance.dags.definitions import defs
from provenance.ingestion import common
from provenance.ingestion.sources import (
    dataco_shipments,
    usaspending_contracts,
    worldbank_lpi,
)


def _read_table(db_path: Path, table: str) -> pd.DataFrame:
    with duckdb.connect(str(db_path)) as connection:
        return connection.execute(f"SELECT * FROM bronze.{table}").fetchdf()


def test_dagster_ingestion_job_serializes_duckdb_writes() -> None:
    job = defs.resolve_job_def("ingest_all_sources")
    parent_keys = {
        node.key.to_user_string(): {key.to_user_string() for key in node.parent_keys}
        for node in job.asset_layer.asset_graph.asset_nodes
    }

    assert job.executor_def.name == "in_process"
    assert parent_keys["dataco_shipments"] == set()
    assert parent_keys["usaspending_contracts"] == {"dataco_shipments"}
    assert parent_keys["worldbank_lpi"] == {"usaspending_contracts"}


def test_bronze_writer_adds_lineage_and_replaces_same_batch(tmp_path: Path) -> None:
    database = tmp_path / "provenance.duckdb"
    bronze = tmp_path / "bronze"
    frame = pd.DataFrame({"raw value": ["one", "two"]})
    batch_id = common.payload_sha256({"request": {"page": 1}, "response": ["one", "two"]})

    first_count = common.write_bronze_dataframe(
        frame,
        source="dataco_shipments",
        batch_id=batch_id,
        source_file="source.csv",
        db_path=database,
        bronze_root=bronze,
    )
    second_count = common.write_bronze_dataframe(
        frame,
        source="dataco_shipments",
        batch_id=batch_id,
        source_file="source.csv",
        db_path=database,
        bronze_root=bronze,
    )

    table = _read_table(database, "dataco_shipments")
    parquet_path = bronze / "dataco_shipments" / f"batch_id={batch_id}" / "part-0.parquet"
    assert first_count == second_count == 2
    assert len(list((bronze / "dataco_shipments").glob("batch_id=*/part-0.parquet"))) == 1
    assert list(table.columns) == [
        "raw value",
        "_batch_id",
        "_source",
        "_source_file",
        "_loaded_at",
    ]
    assert "batch_id" not in table.columns
    assert table["raw value"].tolist() == ["one", "two"]
    assert table["_batch_id"].tolist() == [batch_id, batch_id]
    assert table["_source"].tolist() == ["dataco_shipments", "dataco_shipments"]
    assert table["_source_file"].tolist() == ["source.csv", "source.csv"]
    assert table["_loaded_at"].dt.tz is not None
    assert parquet_path.is_file()


def test_dataco_loader_reads_latin1_and_hashes_file(tmp_path: Path) -> None:
    csv_path = tmp_path / "data.csv"
    csv_path.write_bytes("Customer City,Order Item Id\nMontréal,7\n".encode("latin-1"))
    database = tmp_path / "provenance.duckdb"
    bronze = tmp_path / "bronze"

    count = dataco_shipments.load_dataco_shipments(
        csv_path=csv_path,
        db_path=database,
        bronze_root=bronze,
    )

    table = _read_table(database, "dataco_shipments")
    assert count == 1
    assert table["Customer City"].tolist() == ["Montréal"]
    assert table["Order Item Id"].tolist() == [7]
    assert table["_batch_id"].tolist() == [common.file_sha256(csv_path)]


def test_usaspending_loader_paginates_and_caches_first_response(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[dict[str, object]] = []
    responses = [
        {
            "results": [{"Award ID": "A1", "Recipient Name": "Vendor 1"}],
            "page_metadata": {"page": 1, "hasNext": True},
        },
        {
            "results": [{"Award ID": "A2", "Recipient Name": "Vendor 2"}],
            "page_metadata": {"page": 2, "hasNext": False},
        },
    ]

    def fake_fetch_json(url: str, *, method: str, payload: dict[str, object]) -> dict:
        calls.append({"url": url, "method": method, "payload": payload})
        return responses[len(calls) - 1]

    monkeypatch.setattr(common, "fetch_json", fake_fetch_json)
    database = tmp_path / "provenance.duckdb"
    bronze = tmp_path / "bronze"
    cache_path = tmp_path / "raw" / "usaspending_sample.json"

    count = usaspending_contracts.load_usaspending_contracts(
        db_path=database,
        bronze_root=bronze,
        cache_path=cache_path,
        today=date(2026, 10, 10),
    )

    table = _read_table(database, "usaspending_contracts")
    first_payload = calls[0]["payload"]
    assert count == 2
    assert len(calls) == 2
    assert calls[0]["method"] == calls[1]["method"] == "POST"
    assert first_payload["filters"]["time_period"] == [
        {"start_date": "2026-09-01", "end_date": "2026-09-30"}
    ]
    assert first_payload["filters"]["award_type_codes"] == ["A", "B", "C", "D"]
    assert calls[1]["payload"]["page"] == 2
    assert table["Award ID"].tolist() == ["A1", "A2"]
    assert json.loads(cache_path.read_text(encoding="utf-8"))["response"] == responses[0]


def test_usaspending_uses_cached_response_when_unreachable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cached_response = {
        "results": [{"Award ID": "cached", "Recipient Name": "Cached Vendor"}],
        "page_metadata": {"page": 1, "hasNext": False},
    }
    request = {"filters": {"award_type_codes": ["A"]}}
    cache_path = tmp_path / "raw" / "usaspending_sample.json"
    cache_path.parent.mkdir(parents=True)
    cache_path.write_text(
        json.dumps({"request": request, "response": cached_response}),
        encoding="utf-8",
    )

    def unavailable(*args: object, **kwargs: object) -> None:
        raise URLError("offline")

    monkeypatch.setattr(common, "fetch_json", unavailable)
    database = tmp_path / "provenance.duckdb"
    fallbacks: list[tuple[str, str]] = []
    count = usaspending_contracts.load_usaspending_contracts(
        db_path=database,
        bronze_root=tmp_path / "bronze",
        cache_path=cache_path,
        today=date(2026, 10, 10),
        on_cached_fallback=lambda source, reason: fallbacks.append((source, reason)),
    )

    assert count == 1
    assert _read_table(database, "usaspending_contracts")["Award ID"].tolist() == ["cached"]
    assert fallbacks == [(str(cache_path), "<urlopen error offline>")]


def test_worldbank_loader_reads_json_array_and_paginates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    responses = [
        [
            {"page": 1, "pages": 2},
            [
                {
                    "indicator": {"id": "LP.LPI.OVRL.XQ", "value": "LPI"},
                    "country": {"id": "ABW", "value": "Aruba"},
                    "countryiso3code": "ABW",
                    "date": "2023",
                    "value": 3.7,
                }
            ],
        ],
        [
            {"page": 2, "pages": 2},
            [
                {
                    "indicator": {"id": "LP.LPI.OVRL.XQ", "value": "LPI"},
                    "country": {"id": "AFG", "value": "Afghanistan"},
                    "countryiso3code": "AFG",
                    "date": "2023",
                    "value": 1.9,
                }
            ],
        ],
    ]
    urls: list[str] = []

    def fake_fetch_json(url: str, **kwargs: object) -> list[object]:
        urls.append(url)
        return responses[len(urls) - 1]

    monkeypatch.setattr(common, "fetch_json", fake_fetch_json)
    database = tmp_path / "provenance.duckdb"
    count = worldbank_lpi.load_worldbank_lpi(
        db_path=database,
        bronze_root=tmp_path / "bronze",
    )

    table = _read_table(database, "worldbank_lpi")
    assert count == 2
    assert len(urls) == 2
    assert urls[0].endswith("?format=json&per_page=1000&page=1")
    assert urls[1].endswith("?format=json&per_page=1000&page=2")
    assert table["countryiso3code"].tolist() == ["ABW", "AFG"]
    assert table["_source_file"].tolist() == [worldbank_lpi.API_URL] * 2


def test_worldbank_reuses_latest_bronze_batch_after_api_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    bronze = tmp_path / "bronze"
    database = tmp_path / "provenance.duckdb"
    sample = pd.DataFrame(
        [
            {
                "countryiso3code": "ABW",
                "date": "2023",
                "value": 3.7,
            }
        ]
    )
    common.write_bronze_dataframe(
        sample,
        source="worldbank_lpi",
        batch_id="cached-lpi",
        source_file=worldbank_lpi.API_URL,
        db_path=database,
        bronze_root=bronze,
    )
    def unavailable(*_args: object, **_kwargs: object) -> None:
        raise URLError("offline")

    monkeypatch.setattr(common, "fetch_json", unavailable)
    fallbacks: list[tuple[str, str]] = []

    count = worldbank_lpi.load_worldbank_lpi(
        db_path=database,
        bronze_root=bronze,
        on_cached_fallback=lambda source, reason: fallbacks.append((source, reason)),
    )

    assert count == 1
    assert _read_table(database, "worldbank_lpi")["countryiso3code"].tolist() == ["ABW"]
    assert fallbacks == [
        (
            str(bronze / "worldbank_lpi" / "batch_id=cached-lpi" / "part-0.parquet"),
            "<urlopen error offline>",
        )
    ]
