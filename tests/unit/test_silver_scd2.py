from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
DBT_EXECUTABLE = shutil.which("dbt", path=str(Path(sys.executable).parent)) or "dbt"
TRANSFORM_DIR = ROOT / "src" / "provenance" / "transform"
SCHEMA_PATH = ROOT / "src" / "provenance" / "ingestion" / "schemas" / "dataco_shipments_v1.yaml"
APPROVED_BATCH = "fixture-approved"
QUARANTINED_BATCH = "fixture-quarantined"
EXTRA_COLUMN = "Unexpected Upstream Column"
# Small-fixture settings: 5 shipments make a full-volume quarter, partial
# quarters come only from the data boundary, and volume changes are contextual.
FIXTURE_VARS = {
    "min_quarter_shipments": 5,
    "partial_quarter_min_day_coverage": 0,
    "volume_band_low_max": 8,
    "volume_band_high_min": 1_000_000,
    "band_change_confirming_quarters": 2,
}
QUARTERS = [date(2015, 1, 1), date(2015, 4, 1), date(2015, 7, 1), date(2015, 10, 1)]
PARTIAL_QUARTER = date(2016, 1, 1)  # data stops on 2016-01-15


def _rows(
    category: str,
    quarter_start: date,
    *,
    shipments: int,
    late: int,
    batch_id: str = APPROVED_BATCH,
) -> list[dict[str, Any]]:
    rows = []
    for index in range(shipments):
        ordered = datetime.combine(quarter_start, datetime.min.time()) + timedelta(days=index % 14)
        is_late = index < late
        rows.append(
            {
                "Category Name": category,
                "Late_delivery_risk": int(is_late),
                "Delivery Status": "Late delivery" if is_late else "Shipping on time",
                "Days for shipping (real)": 5 if is_late else 3,
                "Days for shipment (scheduled)": 4,
                "order date (DateOrders)": ordered.strftime("%m/%d/%Y %H:%M"),
                "shipping date (DateOrders)": (ordered + timedelta(days=4)).strftime(
                    "%m/%d/%Y %H:%M"
                ),
                "_batch_id": batch_id,
            }
        )
    return rows


def _fixture_frame() -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for quarter in QUARTERS:
        rows += _rows("Steady", quarter, shipments=10, late=6)
        rows += _rows("Partial", quarter, shipments=10, late=6)
    for quarter in QUARTERS[:2]:
        rows += _rows("Jump", quarter, shipments=10, late=6)
    for quarter in QUARTERS[2:]:
        rows += _rows("Jump", quarter, shipments=10, late=9)
    for quarter in QUARTERS[:2]:
        rows += _rows("ShortFlip", quarter, shipments=10, late=6)
    rows += _rows("ShortFlip", QUARTERS[2], shipments=10, late=9)
    rows += _rows("ShortFlip", QUARTERS[3], shipments=10, late=6)
    rows += _rows("VolumeOnly", QUARTERS[0], shipments=10, late=6)
    rows += _rows("VolumeOnly", QUARTERS[1], shipments=6, late=3)
    for quarter in QUARTERS[2:]:
        rows += _rows("VolumeOnly", quarter, shipments=10, late=6)
    rows += _rows("LowVol", QUARTERS[0], shipments=10, late=6)
    rows += _rows("LowVol", QUARTERS[1], shipments=3, late=3)
    rows += _rows("LowVol", QUARTERS[2], shipments=10, late=6)
    rows += _rows("Steady", PARTIAL_QUARTER, shipments=10, late=6)
    rows += _rows("Partial", PARTIAL_QUARTER, shipments=10, late=10)
    rows += _rows("Quarantined", QUARTERS[0], shipments=10, late=10, batch_id=QUARANTINED_BATCH)

    columns = yaml.safe_load(SCHEMA_PATH.read_text(encoding="utf-8"))["expected_columns"]
    frame = pd.DataFrame(rows)
    for column in columns:
        if column not in frame:
            frame[column] = "sample"
    numeric_defaults = {
        "Benefit per order": 1.0,
        "Sales per customer": 10.0,
        "Category Id": 1,
        "Customer Id": 17,
        "Customer Zipcode": 12345.0,
        "Department Id": 2,
        "Latitude": 0.0,
        "Longitude": 0.0,
        "Order Customer Id": 17,
        "Order Item Cardprod Id": 101,
        "Order Item Discount": 1.0,
        "Order Item Discount Rate": 0.1,
        "Order Item Product Price": 10.0,
        "Order Item Profit Ratio": 0.1,
        "Order Item Quantity": 1,
        "Sales": 10.0,
        "Order Item Total": 9.0,
        "Order Profit Per Order": 1.0,
        "Order Zipcode": None,
        "Product Card Id": 101,
        "Product Category Id": 1,
        "Product Description": None,
        "Product Price": 10.0,
        "Product Status": 0,
    }
    for column, value in numeric_defaults.items():
        frame[column] = value
    frame["Order Country"] = "Francia"
    frame["Order Item Id"] = range(1, len(frame) + 1)
    frame["Order Id"] = range(1, len(frame) + 1)
    frame["_source"] = "dataco_shipments"
    frame["_source_file"] = "fixture.csv"
    frame["_loaded_at"] = pd.Timestamp("2026-01-01T00:00:00Z")
    frame[EXTRA_COLUMN] = "schema evolution"
    return frame[[*columns, EXTRA_COLUMN]]


@pytest.fixture(scope="module")
def silver_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    workdir = tmp_path_factory.mktemp("silver")
    db_path = workdir / "fixture.duckdb"
    bronze = _fixture_frame()
    with duckdb.connect(str(db_path)) as connection:
        connection.execute("CREATE SCHEMA bronze")
        connection.register("bronze_frame", bronze)
        connection.execute("CREATE TABLE bronze.dataco_shipments AS SELECT * FROM bronze_frame")
        connection.execute(
            "CREATE TABLE bronze.dq_results (batch_id VARCHAR, source VARCHAR, status VARCHAR)"
        )
        connection.execute(
            "INSERT INTO bronze.dq_results VALUES (?, 'dataco_shipments', 'validated'), "
            "(?, 'dataco_shipments', 'quarantined')",
            [APPROVED_BATCH, QUARANTINED_BATCH],
        )

    # A subprocess, as in Dagster, so dbt-duckdb's connection closes on exit.
    result = subprocess.run(
        [
            str(DBT_EXECUTABLE),
            "build",
            "--project-dir", str(TRANSFORM_DIR),
            "--profiles-dir", str(TRANSFORM_DIR),
            "--target-path", str(workdir / "target"),
            "--log-path", str(workdir / "logs"),
            "--select", "+dim_supplier_scd2", "+fact_shipments",
            "--indirect-selection", "cautious",
            "--vars", json.dumps(FIXTURE_VARS),
        ],
        env={**os.environ, "PROVENANCE_DUCKDB_PATH": str(db_path)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout[-4000:]
    return db_path


def _query(db_path: Path, sql: str, params: list[Any] | None = None) -> list[tuple]:
    with duckdb.connect(str(db_path), read_only=True) as connection:
        return connection.execute(sql, params or []).fetchall()


def _history(db_path: Path, supplier_key: str) -> list[tuple]:
    return _query(
        db_path,
        """
        SELECT delivery_band, effective_from, effective_to, is_current, version_number
        FROM silver.dim_supplier_scd2
        WHERE supplier_key = ?
        ORDER BY version_number
        """,
        [supplier_key],
    )


def test_extra_upstream_column_does_not_break_silver(silver_db: Path) -> None:
    columns = {
        row[0]
        for row in _query(
            silver_db,
            "SELECT column_name FROM information_schema.columns WHERE table_schema = 'silver'",
        )
    }
    assert _query(silver_db, "SELECT count(*) FROM silver.fact_shipments")[0][0] > 0
    assert EXTRA_COLUMN not in columns
    assert not any("unexpected" in column.lower() for column in columns)


def test_change_confirmed_by_two_eligible_quarters_uses_first_quarter(silver_db: Path) -> None:
    assert _history(silver_db, "cat_jump") == [
        ("medium", date(2015, 1, 1), date(2015, 7, 1), False, 1),
        ("high", date(2015, 7, 1), None, True, 2),
    ]


def test_single_quarter_band_flip_does_not_create_version(silver_db: Path) -> None:
    assert _history(silver_db, "cat_shortflip") == [
        ("medium", date(2015, 1, 1), None, True, 1),
    ]


def test_volume_only_change_does_not_create_version(silver_db: Path) -> None:
    assert _history(silver_db, "cat_volumeonly") == [
        ("medium", date(2015, 1, 1), None, True, 1),
    ]
    assert _query(
        silver_db,
        """
            SELECT
                quarter_start,
                shipment_count,
                CASE
                    WHEN shipment_count < 8 THEN 'low'
                    WHEN shipment_count >= 1_000_000 THEN 'high'
                    ELSE 'medium'
                END AS volume_band
            FROM silver.int_supplier_quarter_metrics
            WHERE supplier_key = 'cat_volumeonly'
                AND quarter_start IN (DATE '2015-01-01', DATE '2015-04-01')
            ORDER BY quarter_start
        """,
        ) == [
            (date(2015, 1, 1), 10, "medium"),
            (date(2015, 4, 1), 6, "low"),
        ]
    assert _query(
        silver_db,
        """
        SELECT row_hash = md5('medium')
        FROM silver.dim_supplier_scd2
        WHERE supplier_key = 'cat_volumeonly'
        """,
    ) == [(True,)]


def test_steady_supplier_keeps_one_version(silver_db: Path) -> None:
    assert _history(silver_db, "cat_steady") == [
        ("medium", date(2015, 1, 1), None, True, 1),
    ]


def test_low_volume_quarter_does_not_create_version(silver_db: Path) -> None:
    flags = _query(
        silver_db,
        """
        SELECT order_quarter, low_volume, late_rate
        FROM silver.int_supplier_quarter_metrics
        WHERE supplier_key = 'cat_lowvol'
        ORDER BY quarter_start
        """,
    )
    assert flags[1] == ("2015-Q2", True, 1.0)
    assert _history(silver_db, "cat_lowvol") == [
        ("medium", date(2015, 1, 1), None, True, 1),
    ]


def test_partial_quarter_is_detected_and_does_not_create_version(silver_db: Path) -> None:
    partial = _query(
        silver_db,
        """
        SELECT DISTINCT order_quarter
        FROM silver.int_supplier_quarter_metrics
        WHERE partial_quarter
        """,
    )
    assert partial == [("2016-Q1",)]
    assert _history(silver_db, "cat_partial") == [
        ("medium", date(2015, 1, 1), None, True, 1),
    ]


def test_quarantined_batch_never_reaches_silver(silver_db: Path) -> None:
    assert _query(
        silver_db,
        "SELECT count(*) FROM silver.fact_shipments WHERE supplier_key = 'cat_quarantined'",
    ) == [(0,)]
