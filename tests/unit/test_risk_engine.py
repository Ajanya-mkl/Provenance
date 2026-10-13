from __future__ import annotations

import copy
import json
from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import yaml

from provenance.risk_engine.scorer import (
    load_rules,
    normalize,
    score_row,
    score_supplier_risk,
)

ROOT = Path(__file__).resolve().parents[2]
RULES_PATH = ROOT / "src" / "provenance" / "risk_engine" / "rules.yaml"
CURRENT_QUARTER = date(2017, 10, 1)


def _risk_row(
    *,
    late_rate: float = 0.65,
    delay_stddev: float = 1.70,
    country_risk: float = 0.50,
    eligible_quarters_to_date: int = 3,
    country_coverage: float = 1.0,
) -> dict[str, Any]:
    return {
        "supplier_key": "cat_test",
        "quarter": CURRENT_QUARTER,
        "late_rate": late_rate,
        "delay_stddev": delay_stddev,
        "country_risk": country_risk,
        "eligible_quarters_to_date": eligible_quarters_to_date,
        "country_coverage": country_coverage,
    }


def _scoring_database(
    db_path: Path,
    mart_rows: list[tuple[Any, ...]],
    prior_score: tuple[Any, ...] | None = None,
) -> None:
    with duckdb.connect(str(db_path)) as connection:
        connection.execute("CREATE SCHEMA gold")
        connection.execute(
            """
            CREATE TABLE gold.mart_supplier_risk (
                supplier_key VARCHAR,
                quarter DATE,
                late_rate DOUBLE,
                delay_stddev DOUBLE,
                country_risk DOUBLE,
                eligible_quarters_to_date BIGINT,
                country_coverage DOUBLE
            )
            """
        )
        connection.executemany(
            "INSERT INTO gold.mart_supplier_risk VALUES (?, ?, ?, ?, ?, ?, ?)",
            mart_rows,
        )
        if prior_score is not None:
            connection.execute(
                """
                CREATE TABLE gold.supplier_risk_score (
                    supplier_key VARCHAR NOT NULL,
                    quarter DATE NOT NULL,
                    score DOUBLE NOT NULL,
                    tier VARCHAR NOT NULL,
                    contributing_factors JSON NOT NULL,
                    confidence VARCHAR NOT NULL,
                    rules_version VARCHAR NOT NULL,
                    PRIMARY KEY (supplier_key, quarter)
                )
                """
            )
            connection.execute(
                "INSERT INTO gold.supplier_risk_score VALUES (?, ?, ?, ?, ?::JSON, ?, ?)",
                prior_score,
            )


def test_contributing_factors_sum_to_score() -> None:
    result = score_row(_risk_row(), load_rules())

    assert sum(
        factor["weighted_contribution"]
        for factor in result["contributing_factors"].values()
    ) == result["score"]


def test_tier_boundary_edge_values() -> None:
    rules = copy.deepcopy(load_rules())
    rules["normalization_bounds"] = {
        name: {"min": 0.0, "max": 1.0} for name in rules["weights"]
    }
    rules["tier_thresholds"] = {"medium_min": 0.25, "high_min": 0.75}

    def tier_for(value: float) -> str:
        row = _risk_row(
            late_rate=value,
            delay_stddev=value,
            country_risk=value,
        )
        return score_row(row, rules)["tier"]

    assert tier_for(0.249999) == "low"
    assert tier_for(0.25) == "medium"
    assert tier_for(0.749999) == "medium"
    assert tier_for(0.75) == "high"


def test_normalization_clips_outside_configured_bounds() -> None:
    assert normalize(-0.1, 0.0, 1.0) == 0.0
    assert normalize(0.5, 0.0, 1.0) == 0.5
    assert normalize(1.1, 0.0, 1.0) == 1.0


def test_insufficient_history_or_country_coverage_is_low_confidence() -> None:
    rules = load_rules()

    short_history = score_row(_risk_row(eligible_quarters_to_date=2), rules)
    poor_coverage = score_row(_risk_row(country_coverage=0.94), rules)

    assert short_history["confidence"] == "low"
    assert poor_coverage["confidence"] == "low"
    assert score_row(_risk_row(), rules)["confidence"] == "full"


def test_scoring_reads_changed_weights_from_rules_file(tmp_path: Path) -> None:
    rules = load_rules()
    rules["weights"] = {
        "late_rate": 0.60,
        "delay_stddev": 0.20,
        "country_risk": 0.20,
    }
    changed_rules_path = tmp_path / "rules.yaml"
    changed_rules_path.write_text(yaml.safe_dump(rules), encoding="utf-8")

    unchanged = score_row(
        _risk_row(late_rate=0.65, delay_stddev=1.30, country_risk=0.20),
        load_rules(),
    )
    changed = score_row(
        _risk_row(late_rate=0.65, delay_stddev=1.30, country_risk=0.20),
        load_rules(changed_rules_path),
    )

    assert unchanged["score"] != changed["score"]
    assert unchanged["tier"] != changed["tier"]


def test_risk_flag_is_emitted_once_and_rerun_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "risk.duckdb"
    _scoring_database(
        db_path,
        [("cat_test", CURRENT_QUARTER, 0.65, 1.70, 0.50, 3, 1.0)],
        (
            "cat_test",
            CURRENT_QUARTER,
            0.1,
            "low",
            "{}",
            "full",
            "0.9",
        ),
    )

    first = score_supplier_risk(db_path, RULES_PATH)
    second = score_supplier_risk(db_path, RULES_PATH)

    assert first.flags_emitted == 1
    assert first.events_emitted == 1
    assert second.flags_emitted == 0
    assert second.events_emitted == 0
    with duckdb.connect(str(db_path), read_only=True) as connection:
        flag_count = connection.execute(
            "SELECT count(*) FROM gold.supplier_risk_flags"
        ).fetchone()[0]
        assert flag_count == 1
        assert connection.execute(
            "SELECT count(*) FROM agent.events WHERE event_type = 'risk_flag'"
        ).fetchone()[0] == 1
        event = connection.execute(
            "SELECT payload FROM agent.events WHERE event_type = 'risk_flag'"
        ).fetchone()[0]
    assert json.loads(event)["new_tier"] == "high"


def test_rising_tier_emits_a_flag(tmp_path: Path) -> None:
    db_path = tmp_path / "risk.duckdb"
    _scoring_database(
        db_path,
        [("cat_test", CURRENT_QUARTER, 0.575, 1.50, 0.35, 3, 1.0)],
        (
            "cat_test",
            CURRENT_QUARTER,
            0.1,
            "low",
            "{}",
            "full",
            "0.9",
        ),
    )

    result = score_supplier_risk(db_path, RULES_PATH)

    assert result.flags_emitted == 1
    with duckdb.connect(str(db_path), read_only=True) as connection:
        old_tier, new_tier = connection.execute(
            "SELECT old_tier, new_tier FROM gold.supplier_risk_flags"
        ).fetchone()
    assert (old_tier, new_tier) == ("low", "medium")


def test_dagster_phase5_job_chains_gold_mart_to_scorer() -> None:
    from dagster import AssetKey

    from provenance.dags.definitions import defs

    asset_graph = defs.resolve_asset_graph()
    score_asset = asset_graph.get(AssetKey("supplier_risk_scoring"))
    assert score_asset.parent_keys == {AssetKey(["gold", "mart_supplier_risk"])}

    job = defs.resolve_job_def("phase5_risk_pipeline")
    selected_keys = job.asset_layer.selected_asset_keys
    assert {
        AssetKey("gate_dataco_shipments"),
        AssetKey("gate_usaspending_contracts"),
        AssetKey("gate_worldbank_lpi"),
        AssetKey("dim_supplier_scd2"),
        AssetKey(["gold", "mart_supplier_risk"]),
        AssetKey(["gold", "mart_shipment_delay"]),
        AssetKey("supplier_risk_scoring"),
    } <= selected_keys
