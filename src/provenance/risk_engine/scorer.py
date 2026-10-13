from __future__ import annotations

import json
import math
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import yaml

from provenance.data_quality.gate import ensure_agent_events_table

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "provenance.duckdb"
DEFAULT_RULES_PATH = Path(__file__).with_name("rules.yaml")


@dataclass(frozen=True)
class ScoringSummary:
    quarter: date
    scores_written: int
    flags_emitted: int
    events_emitted: int


def load_rules(path: str | Path = DEFAULT_RULES_PATH) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as rules_file:
        rules = yaml.safe_load(rules_file)
    if not isinstance(rules, dict):
        raise ValueError("Risk rules must be a YAML mapping.")

    weights = rules.get("weights")
    bounds = rules.get("normalization_bounds")
    if not isinstance(weights, dict) or not weights:
        raise ValueError("Risk rules must define at least one input weight.")
    if not isinstance(bounds, dict) or set(bounds) != set(weights):
        raise ValueError("Each weighted input must have exactly one normalization bound.")
    numeric_weights = [float(weight) for weight in weights.values()]
    if any(not math.isfinite(weight) or weight < 0 for weight in numeric_weights):
        raise ValueError("Risk input weights must be finite and non-negative.")
    if not math.isclose(sum(numeric_weights), 1.0):
        raise ValueError("Risk input weights must sum to 1.")

    for name, limits in bounds.items():
        if not isinstance(limits, dict) or not {"min", "max"} <= limits.keys():
            raise ValueError(f"Normalization bounds for {name} need min and max.")
        minimum = float(limits["min"])
        maximum = float(limits["max"])
        if not math.isfinite(minimum) or not math.isfinite(maximum) or minimum >= maximum:
            raise ValueError(f"Normalization minimum must be below maximum for {name}.")

    thresholds = rules.get("tier_thresholds", {})
    medium_min = float(thresholds["medium_min"])
    high_min = float(thresholds["high_min"])
    if not 0 <= medium_min < high_min <= 1:
        raise ValueError("Tier thresholds must satisfy 0 <= medium_min < high_min <= 1.")

    tier_order = rules.get("tier_order")
    if not isinstance(tier_order, list) or len(tier_order) != 3:
        raise ValueError("Risk rules must define an ordered list of three tiers.")
    if set(tier_order) != {"low", "medium", "high"}:
        raise ValueError("Risk tiers must be low, medium, and high.")

    confidence = rules.get("confidence", {})
    if int(confidence["minimum_eligible_quarters"]) < 1:
        raise ValueError("minimum_eligible_quarters must be positive.")
    if not 0 <= float(confidence["minimum_country_coverage"]) <= 1:
        raise ValueError("minimum_country_coverage must be between 0 and 1.")
    if not rules.get("rules_version"):
        raise ValueError("Risk rules must define rules_version.")
    return rules


def normalize(value: float, minimum: float, maximum: float) -> float:
    raw_value = float(value)
    if not math.isfinite(raw_value):
        raise ValueError("Risk inputs must be finite numbers.")
    return min(1.0, max(0.0, (raw_value - minimum) / (maximum - minimum)))


def score_row(
    row: Mapping[str, Any],
    rules: Mapping[str, Any],
) -> dict[str, Any]:
    factors: dict[str, dict[str, float]] = {}
    for name, weight_value in rules["weights"].items():
        weight = float(weight_value)
        limits = rules["normalization_bounds"][name]
        raw_value = float(row[name])
        normalized_value = normalize(raw_value, float(limits["min"]), float(limits["max"]))
        factors[name] = {
            "raw_value": raw_value,
            "normalized_value": normalized_value,
            "weight": weight,
            "weighted_contribution": normalized_value * weight,
        }

    score = sum(factor["weighted_contribution"] for factor in factors.values())
    thresholds = rules["tier_thresholds"]
    if score < float(thresholds["medium_min"]):
        tier = rules["tier_order"][0]
    elif score < float(thresholds["high_min"]):
        tier = rules["tier_order"][1]
    else:
        tier = rules["tier_order"][2]

    confidence_rules = rules["confidence"]
    low_confidence = (
        int(row["eligible_quarters_to_date"])
        < int(confidence_rules["minimum_eligible_quarters"])
        or float(row["country_coverage"])
        < float(confidence_rules["minimum_country_coverage"])
    )
    return {
        "supplier_key": str(row["supplier_key"]),
        "quarter": row["quarter"],
        "score": score,
        "tier": tier,
        "contributing_factors": factors,
        "confidence": "low" if low_confidence else "full",
        "rules_version": str(rules["rules_version"]),
    }


def score_rows(
    rows: Sequence[Mapping[str, Any]],
    rules: Mapping[str, Any],
) -> list[dict[str, Any]]:
    return [score_row(row, rules) for row in rows]


def _create_score_tables(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute("CREATE SCHEMA IF NOT EXISTS gold")
    connection.execute("CREATE SCHEMA IF NOT EXISTS agent")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS gold.supplier_risk_score (
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
        """
        CREATE TABLE IF NOT EXISTS gold.supplier_risk_flags (
            supplier_key VARCHAR NOT NULL,
            quarter DATE NOT NULL,
            old_tier VARCHAR,
            new_tier VARCHAR NOT NULL,
            score DOUBLE NOT NULL,
            top_contributing_factor JSON NOT NULL,
            confidence VARCHAR NOT NULL,
            PRIMARY KEY (supplier_key, quarter)
        )
        """
    )


def _mart_rows(connection: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    cursor = connection.execute(
        """
        SELECT
            supplier_key,
            quarter,
            late_rate,
            delay_stddev,
            country_risk,
            eligible_quarters_to_date,
            country_coverage
        FROM gold.mart_supplier_risk
        ORDER BY quarter, supplier_key
        """
    )
    columns = [description[0] for description in cursor.description]
    return [dict(zip(columns, values, strict=True)) for values in cursor.fetchall()]


def _insert_or_update_scores(
    connection: duckdb.DuckDBPyConnection,
    scores: Sequence[Mapping[str, Any]],
) -> None:
    connection.executemany(
        """
        INSERT INTO gold.supplier_risk_score (
            supplier_key, quarter, score, tier, contributing_factors,
            confidence, rules_version
        )
        VALUES (?, ?, ?, ?, ?::JSON, ?, ?)
        ON CONFLICT (supplier_key, quarter) DO UPDATE SET
            score = excluded.score,
            tier = excluded.tier,
            contributing_factors = excluded.contributing_factors,
            confidence = excluded.confidence,
            rules_version = excluded.rules_version
        """,
        [
            (
                score["supplier_key"],
                score["quarter"],
                score["score"],
                score["tier"],
                json.dumps(score["contributing_factors"], sort_keys=True),
                score["confidence"],
                score["rules_version"],
            )
            for score in scores
        ],
    )


def _latest_previous_tiers(
    connection: duckdb.DuckDBPyConnection,
    scores: Sequence[Mapping[str, Any]],
    current_quarter: date,
) -> dict[str, str]:
    stored_current = {
        supplier_key: tier
        for supplier_key, tier in connection.execute(
            """
            SELECT supplier_key, tier
            FROM gold.supplier_risk_score
            WHERE quarter = ?
            """,
            [current_quarter],
        ).fetchall()
    }
    if stored_current:
        return stored_current

    stored_prior = connection.execute(
        """
        SELECT supplier_key, tier
        FROM gold.supplier_risk_score
        WHERE quarter = (
            SELECT max(quarter)
            FROM gold.supplier_risk_score
            WHERE quarter < ?
        )
        """,
        [current_quarter],
    ).fetchall()
    if stored_prior:
        return dict(stored_prior)

    prior_quarters = [score["quarter"] for score in scores if score["quarter"] < current_quarter]
    if not prior_quarters:
        return {}
    previous_quarter = max(prior_quarters)
    return {
        score["supplier_key"]: score["tier"]
        for score in scores
        if score["quarter"] == previous_quarter
    }


def _new_tier_flags(
    scores: Sequence[Mapping[str, Any]],
    rules: Mapping[str, Any],
    old_tiers: Mapping[str, str],
    current_quarter: date,
) -> list[dict[str, Any]]:
    ranks = {tier: rank for rank, tier in enumerate(rules["tier_order"])}
    flags = []
    for score in scores:
        if score["quarter"] != current_quarter:
            continue
        old_tier = old_tiers.get(score["supplier_key"])
        enters_high = score["tier"] == rules["tier_order"][-1] and old_tier != score["tier"]
        rises = (
            old_tier in ranks
            and ranks[score["tier"]] > ranks[old_tier]
        )
        if enters_high or rises:
            top_factor_name, top_factor = max(
                score["contributing_factors"].items(),
                key=lambda item: item[1]["weighted_contribution"],
            )
            flags.append(
                {
                    **score,
                    "old_tier": old_tier,
                    "top_factor": {
                        "name": top_factor_name,
                        **top_factor,
                    },
                }
            )
    return flags


def _write_flags_and_events(
    connection: duckdb.DuckDBPyConnection,
    flags: Sequence[Mapping[str, Any]],
) -> tuple[int, int]:
    flags_added = 0
    events_added = 0
    for flag in flags:
        existing_flag = connection.execute(
            """
            SELECT 1
            FROM gold.supplier_risk_flags
            WHERE supplier_key = ? AND quarter = ?
            """,
            [flag["supplier_key"], flag["quarter"]],
        ).fetchone()
        if existing_flag:
            continue

        connection.execute(
            """
            INSERT INTO gold.supplier_risk_flags (
                supplier_key, quarter, old_tier, new_tier, score,
                top_contributing_factor, confidence
            )
            VALUES (?, ?, ?, ?, ?, ?::JSON, ?)
            """,
            [
                flag["supplier_key"],
                flag["quarter"],
                flag["old_tier"],
                flag["tier"],
                flag["score"],
                json.dumps(flag["top_factor"], sort_keys=True),
                flag["confidence"],
            ],
        )
        flags_added += 1

        event_id = f"risk_flag:{flag['quarter']}:{flag['supplier_key']}"
        event = {
            "supplier_key": flag["supplier_key"],
            "quarter": flag["quarter"].isoformat(),
            "old_tier": flag["old_tier"],
            "new_tier": flag["tier"],
            "score": flag["score"],
            "top_contributing_factor": flag["top_factor"],
            "confidence": flag["confidence"],
        }
        result = connection.execute(
            """
            INSERT INTO agent.events (
                event_id, event_type, source, batch_id, created_at, payload
            )
            VALUES (?, 'risk_flag', 'risk_engine', ?, current_timestamp, ?::JSON)
            ON CONFLICT (event_id) DO NOTHING
            RETURNING event_id
            """,
            [
                event_id,
                flag["quarter"].isoformat(),
                json.dumps(event, sort_keys=True),
            ],
        ).fetchone()
        events_added += int(result is not None)
    return flags_added, events_added


def score_supplier_risk(
    db_path: str | Path | None = None,
    rules_path: str | Path = DEFAULT_RULES_PATH,
) -> ScoringSummary:
    rules = load_rules(rules_path)
    target_db = Path(
        db_path
        or os.environ.get("PROVENANCE_DUCKDB_PATH")
        or DEFAULT_DB_PATH
    )
    with duckdb.connect(str(target_db)) as connection:
        _create_score_tables(connection)
        ensure_agent_events_table(connection)
        source_rows = _mart_rows(connection)
        if not source_rows:
            raise ValueError("gold.mart_supplier_risk has no eligible supplier-quarter rows.")
        scores = score_rows(source_rows, rules)
        current_quarter = max(score["quarter"] for score in scores)
        old_tiers = _latest_previous_tiers(connection, scores, current_quarter)
        flags = _new_tier_flags(scores, rules, old_tiers, current_quarter)
        _insert_or_update_scores(connection, scores)
        flags_added, events_added = _write_flags_and_events(connection, flags)

    return ScoringSummary(
        quarter=current_quarter,
        scores_written=len(scores),
        flags_emitted=flags_added,
        events_emitted=events_added,
    )