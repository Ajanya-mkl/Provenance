from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
import pytest
import yaml

from provenance.data_quality.gate import (
    GateResult,
    evaluate_bronze_dataframe,
    validate_bronze_parquet,
)

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "src" / "provenance" / "ingestion" / "schemas"


def _schema_columns(source: str) -> list[str]:
    schema = yaml.safe_load(
        (SCHEMA_DIR / f"{source}_v1.yaml").read_text(encoding="utf-8")
    )
    return schema["expected_columns"]


def _dataframe(source: str, *, row_count: int = 2) -> pd.DataFrame:
    if source == "dataco_shipments":
        values: dict[str, Any] = {
            "Type": "TRANSFER",
            "Days for shipping (real)": 3,
            "Days for shipment (scheduled)": 4,
            "Benefit per order": -2.0,
            "Order Item Profit Ratio": -0.1,
            "Order Profit Per Order": -2.0,
            "Sales per customer": 20.0,
            "Delivery Status": "COMPLETE",
            "Late_delivery_risk": 0,
            "Category Id": 1,
            "Category Name": "Footwear",
            "Customer Id": 17,
            "Order Customer Id": 17,
            "Department Id": 2,
            "Order Item Cardprod Id": 101,
            "Product Card Id": 101,
            "Product Category Id": 1,
            "Order Item Discount": 2.0,
            "Order Item Discount Rate": 0.1,
            "Order Item Product Price": 20.0,
            "Order Item Quantity": 1,
            "Sales": 20.0,
            "Order Item Total": 18.0,
            "Product Price": 20.0,
            "order date (DateOrders)": "1/2/2016 12:00",
            "shipping date (DateOrders)": "1/5/2016 12:00",
            "_batch_id": "unit-batch",
            "_source": source,
            "_source_file": "unit.csv",
            "_loaded_at": pd.Timestamp("2026-01-01T00:00:00Z"),
        }
        rows = []
        for index in range(row_count):
            row = {column: "sample" for column in _schema_columns(source)}
            row.update(values)
            row["Order Item Id"] = 1000 + index
            row["Order Id"] = 100 + index
            row["Customer Id"] = 17
            row["Order Customer Id"] = 17
            row["Order Item Cardprod Id"] = 101
            row["Product Card Id"] = 101
            row["Product Category Id"] = 1
            row["Category Id"] = 1
            row["Department Id"] = 2
            rows.append(row)
        return pd.DataFrame(rows, columns=_schema_columns(source))

    if source == "usaspending_contracts":
        rows = []
        for index in range(row_count):
            row = {column: "sample" for column in _schema_columns(source)}
            row.update(
                {
                    "internal_id": str(index),
                    "Award ID": "SHARED-AWARD-ID",
                    "Recipient Name": "Example Recipient",
                    "Recipient UEI": "UEI-ONE",
                    "recipient_id": "recipient-one",
                    "Start Date": "2024-01-01",
                    "End Date": "2024-12-31",
                    "Award Amount": float(100 + index),
                    "Awarding Agency": "Agency",
                    "Awarding Sub Agency": "Subagency",
                    "Contract Award Type": "A",
                    "NAICS": "111111",
                    "PSC": "PSC",
                    "awarding_agency_id": "agency-id",
                    "agency_slug": "agency",
                    "generated_internal_id": f"unique-{index}",
                    "_batch_id": "unit-batch",
                    "_source": source,
                    "_source_file": None,
                    "_loaded_at": pd.Timestamp("2026-01-01T00:00:00Z"),
                }
            )
            rows.append(row)
        return pd.DataFrame(rows, columns=_schema_columns(source))

    if source == "worldbank_lpi":
        rows = []
        for index in range(row_count):
            row = {column: "sample" for column in _schema_columns(source)}
            row.update(
                {
                    "indicator": {"id": "LP.LPI.OVRL.XQ", "value": "LPI"},
                    "country": {"id": "USA", "value": "United States"},
                    "countryiso3code": "USA",
                    "date": "2024",
                    "value": 3.2 if index < max(1, row_count // 20) else None,
                    "unit": "",
                    "obs_status": "",
                    "decimal": 1,
                    "_batch_id": "unit-batch",
                    "_source": source,
                    "_source_file": "worldbank",
                    "_loaded_at": pd.Timestamp("2026-01-01T00:00:00Z"),
                }
            )
            rows.append(row)
        return pd.DataFrame(rows, columns=_schema_columns(source))
    raise AssertionError(f"Unknown test source: {source}")


def _evaluate(source: str, dataframe: pd.DataFrame) -> GateResult:
    return evaluate_bronze_dataframe(
        dataframe,
        source=source,
        batch_id="unit-batch",
    )


def test_dataco_clean_batch_passes() -> None:
    result = _evaluate("dataco_shipments", _dataframe("dataco_shipments"))

    assert result.passed
    assert result.passed_expectations == 50
    assert result.failed_expectations == 0


@pytest.mark.parametrize(
    ("corruption", "expected_expectation"),
    [
        ("duplicate_key", "expect_column_values_to_be_unique"),
        ("null_key", "expect_column_values_to_not_be_null"),
        ("out_of_range", "expect_column_values_to_be_between"),
        ("bad_flag", "expect_column_values_to_be_in_set"),
    ],
)
def test_dataco_corrupt_values_fail(
    corruption: str,
    expected_expectation: str,
) -> None:
    dataframe = _dataframe("dataco_shipments")
    if corruption == "duplicate_key":
        dataframe.loc[1, "Order Item Id"] = dataframe.loc[0, "Order Item Id"]
    elif corruption == "null_key":
        dataframe.loc[0, "Order Item Id"] = None
    elif corruption == "out_of_range":
        dataframe.loc[0, "Days for shipping (real)"] = 31
    else:
        dataframe.loc[0, "Late_delivery_risk"] = 7

    result = _evaluate("dataco_shipments", dataframe)

    assert not result.passed
    assert any(
        detail["expectation_type"] == expected_expectation
        for detail in result.failed_details
    )


def test_dataco_unparseable_dates_fail() -> None:
    dataframe = _dataframe("dataco_shipments")
    dataframe.loc[0, "order date (DateOrders)"] = "not-a-date"

    result = _evaluate("dataco_shipments", dataframe)

    assert not result.passed
    assert any(
        detail["expectation_type"] == "expect_column_values_to_be_dateutil_parseable"
        for detail in result.failed_details
    )


def test_usaspending_repeated_award_id_is_not_a_uniqueness_failure() -> None:
    result = _evaluate(
        "usaspending_contracts",
        _dataframe("usaspending_contracts", row_count=100),
    )

    assert result.passed
    assert result.failed_expectations == 0


def test_usaspending_two_out_of_range_start_dates_are_within_mostly_threshold() -> None:
    dataframe = _dataframe("usaspending_contracts", row_count=100)
    dataframe.loc[0, ["Start Date", "End Date"]] = ["1980-01-01", "1981-01-01"]
    dataframe.loc[1, ["Start Date", "End Date"]] = ["2050-01-01", "2051-01-01"]

    result = _evaluate("usaspending_contracts", dataframe)

    assert result.passed
    assert result.failed_expectations == 0


def test_usaspending_eight_reversed_dates_pass_with_row_warnings() -> None:
    dataframe = _dataframe("usaspending_contracts", row_count=10_000)
    for index in range(8):
        dataframe.loc[index, ["Start Date", "End Date"]] = [
            "2024-01-02",
            "2024-01-01",
        ]

    result = _evaluate("usaspending_contracts", dataframe)

    assert result.passed
    assert result.failed_expectations == 0
    date_warning = next(
        warning
        for warning in result.warnings
        if warning["expectation_type"]
        == "expect_column_pair_values_a_to_be_greater_than_b"
    )
    assert date_warning["unexpected_count"] == 8
    assert len(date_warning["details"]) == 8
    assert date_warning["details"][0] == {
        "generated_internal_id": "unique-0",
        "Start Date": "2024-01-02",
        "End Date": "2024-01-01",
    }
    assert result.result_json["warnings"] == result.warnings


def test_usaspending_five_percent_reversed_dates_fail_and_quarantine(
    tmp_path: Path,
) -> None:
    batch_id = "test-reversed-dates-batch"
    batch_folder = (
        tmp_path / "bronze" / "usaspending_contracts" / f"batch_id={batch_id}"
    )
    batch_folder.mkdir(parents=True)
    dataframe = _dataframe("usaspending_contracts", row_count=100)
    for index in range(5):
        dataframe.loc[index, ["Start Date", "End Date"]] = [
            "2024-01-02",
            "2024-01-01",
        ]
    parquet_path = batch_folder / "part-0.parquet"
    dataframe.to_parquet(parquet_path, index=False)
    quarantine_root = tmp_path / "quarantine"

    result = validate_bronze_parquet(
        parquet_path,
        source="usaspending_contracts",
        batch_id=batch_id,
        db_path=tmp_path / "provenance.duckdb",
        quarantine_root=quarantine_root,
    )

    quarantined_batch = (
        quarantine_root / "usaspending_contracts" / f"batch_id={batch_id}"
    )
    assert not result.passed
    assert result.warnings[0]["unexpected_count"] == 5
    assert (quarantined_batch / parquet_path.name).is_file()
    saved_result = json.loads(
        (quarantined_batch / "validation_result.json").read_text(encoding="utf-8")
    )
    assert saved_result["warnings"][0]["unexpected_count"] == 5
    with duckdb.connect(str(tmp_path / "provenance.duckdb"), read_only=True) as connection:
        dq_row = connection.execute(
            """
            SELECT status, warnings
            FROM bronze.dq_results
            WHERE source = ? AND batch_id = ?
            """,
            ["usaspending_contracts", batch_id],
        ).fetchone()
    assert dq_row is not None
    assert dq_row[0] == "quarantined"
    assert json.loads(dq_row[1])[0]["unexpected_count"] == 5


def test_worldbank_null_scores_alone_do_not_fail() -> None:
    result = _evaluate("worldbank_lpi", _dataframe("worldbank_lpi", row_count=100))

    assert result.passed
    assert result.failed_expectations == 0
    assert result.warnings == []


def test_failure_quarantines_parquet_and_records_dq_and_agent_rows(
    tmp_path: Path,
) -> None:
    batch_id = "test-quarantine-batch"
    batch_folder = (
        tmp_path / "bronze" / "dataco_shipments" / f"batch_id={batch_id}"
    )
    batch_folder.mkdir(parents=True)
    dataframe = _dataframe("dataco_shipments")
    dataframe.loc[0, "Late_delivery_risk"] = 9
    parquet_path = batch_folder / "part-0.parquet"
    dataframe.to_parquet(parquet_path, index=False)
    quarantine_root = tmp_path / "quarantine"

    result = validate_bronze_parquet(
        parquet_path,
        source="dataco_shipments",
        batch_id=batch_id,
        db_path=tmp_path / "provenance.duckdb",
        quarantine_root=quarantine_root,
    )

    quarantined_batch = (
        quarantine_root / "dataco_shipments" / f"batch_id={batch_id}"
    )
    validation_json = quarantined_batch / "validation_result.json"
    assert not result.passed
    assert (quarantined_batch / parquet_path.name).is_file()
    assert validation_json.is_file()
    saved_result = json.loads(validation_json.read_text(encoding="utf-8"))
    assert saved_result["batch_id"] == batch_id
    assert saved_result["failed_expectations"] == result.failed_expectations

    with duckdb.connect(str(tmp_path / "provenance.duckdb"), read_only=True) as connection:
        dq_row = connection.execute(
            """
            SELECT status, passed_expectations, failed_expectations, result
            FROM bronze.dq_results
            WHERE source = ? AND batch_id = ?
            """,
            ["dataco_shipments", batch_id],
        ).fetchone()
        event_row = connection.execute(
            """
            SELECT event_type, source, batch_id, payload
            FROM agent.events
            WHERE event_id = ?
            """,
            [f"dq_failure:dataco_shipments:{batch_id}"],
        ).fetchone()

    assert dq_row is not None
    assert dq_row[0] == "quarantined"
    assert dq_row[1] == result.passed_expectations
    assert dq_row[2] == result.failed_expectations
    assert json.loads(dq_row[3])["passed"] is False
    assert event_row[:3] == ("dq_failure", "dataco_shipments", batch_id)
    payload = json.loads(event_row[3])
    assert set(payload) == {
        "source",
        "batch_id",
        "status",
        "passed_expectations",
        "failed_expectations",
        "failed_details",
        "result_path",
    }
    assert payload["status"] == "quarantined"
    assert payload["failed_expectations"] == result.failed_expectations
    assert Path(payload["result_path"]) == validation_json.resolve()
    assert payload["failed_details"][0]["expectation_type"] == (
        "expect_column_values_to_be_in_set"
    )
    assert "batch_id" not in payload["failed_details"][0]["kwargs"]


def _write_batch(
    tmp_path: Path,
    source: str,
    batch_id: str,
    dataframe: pd.DataFrame,
) -> Path:
    batch_folder = tmp_path / "bronze" / source / f"batch_id={batch_id}"
    batch_folder.mkdir(parents=True)
    parquet_path = batch_folder / "part-0.parquet"
    dataframe.to_parquet(parquet_path, index=False)
    return parquet_path


def test_later_passing_batch_resolves_open_failures_but_not_demo_events(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "provenance.duckdb"
    quarantine_root = tmp_path / "quarantine"
    with duckdb.connect(str(db_path)) as connection:
        connection.execute("CREATE SCHEMA agent")
        connection.execute(
            """
            CREATE TABLE agent.events (
                event_id VARCHAR PRIMARY KEY,
                event_type VARCHAR NOT NULL,
                source VARCHAR NOT NULL,
                batch_id VARCHAR NOT NULL,
                created_at TIMESTAMPTZ NOT NULL,
                payload JSON NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO agent.events VALUES (
                'dq_failure:dataco_shipments:demo', 'dq_failure',
                'dataco_shipments', 'demo', TIMESTAMPTZ '2026-01-01 00:00:00+00',
                '{"demo": true}'
            )
            """
        )

    corrupt = _dataframe("dataco_shipments")
    corrupt.loc[0, "Late_delivery_risk"] = 9
    for source, batch_id, dataframe in [
        ("dataco_shipments", "bad-batch", corrupt),
        ("usaspending_contracts", "other-source", None),
    ]:
        if dataframe is None:
            dataframe = _dataframe(source, row_count=100)
            dataframe.loc[0, "Award Amount"] = None
            dataframe.loc[0, "generated_internal_id"] = None
        validate_bronze_parquet(
            _write_batch(tmp_path, source, batch_id, dataframe),
            source=source,
            batch_id=batch_id,
            db_path=db_path,
            quarantine_root=quarantine_root,
        )

    clean = validate_bronze_parquet(
        _write_batch(
            tmp_path, "dataco_shipments", "good-batch", _dataframe("dataco_shipments")
        ),
        source="dataco_shipments",
        batch_id="good-batch",
        db_path=db_path,
        quarantine_root=quarantine_root,
    )

    with duckdb.connect(str(db_path), read_only=True) as connection:
        rows = {
            row[0]: row[1:]
            for row in connection.execute(
                "SELECT batch_id, resolved_at, resolution FROM agent.events"
            ).fetchall()
        }
    assert clean.passed
    assert rows["bad-batch"][0] is not None
    assert rows["bad-batch"][1] == "superseded: batch good-batch passed as validated"
    assert rows["demo"] == (None, None)
    assert rows["other-source"] == (None, None)


def test_dagster_gate_failure_skips_later_source_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from provenance.dags import ingestion_assets
    from provenance.dags.definitions import defs

    failed_result = GateResult(
        passed=False,
        batch_id="synthetic-failure",
        source="dataco_shipments",
        passed_expectations=26,
        failed_expectations=1,
        failed_details=[],
        result_json={"passed": False},
    )

    def fail_dataco_gate(source: str, **kwargs: Any) -> list[GateResult]:
        assert source == "dataco_shipments"
        return [failed_result]

    monkeypatch.setattr(
        ingestion_assets,
        "validate_source_batches",
        fail_dataco_gate,
    )
    job = defs.resolve_job_def("bronze_quality_gates")
    result = job.execute_in_process(raise_on_error=False)
    event_types = {
        event.step_key: event.event_type_value
        for event in result.all_events
        if event.step_key is not None
    }

    assert not result.success
    assert event_types["gate_dataco_shipments"] == "STEP_FAILURE"
    assert "gate_usaspending_contracts" not in event_types
    assert "gate_worldbank_lpi" not in event_types
