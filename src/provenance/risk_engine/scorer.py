from __future__ import annotations

import hashlib
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
class FlagTarget:
    """Where scores, flags and flag events of one scoring path live.

    The defaults are the flagship (DataCo): gold tables keyed by supplier_key and
    event ids 'risk_flag:<rules>:<quarter>:<supplier>' from source 'risk_engine'.
    An onboarded dataset uses its own ds_<id> tables, ids prefixed 'ds_<id>:' and
    its own source, so neither path can supersede or resolve the other's events.
    Table names are code-generated (never user input).
    """

    scores_table: str = "gold.supplier_risk_score"
    flags_table: str = "gold.supplier_risk_flags"
    key_column: str = "supplier_key"
    event_prefix: str = ""
    source: str = "risk_engine"
    dataset_id: str | None = None


FLAGSHIP = FlagTarget()


@dataclass(frozen=True)
class ScoringSummary:
    quarter: date
    scores_written: int
    flags_emitted: int
    events_emitted: int
    events_superseded: int = 0
    flags_resolved: int = 0


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

    as_of_quarter = rules.get("as_of_quarter", "latest_complete")
    if not isinstance(as_of_quarter, str) or not as_of_quarter:
        raise ValueError("as_of_quarter must be latest_complete or an ISO quarter-start date.")
    if as_of_quarter != "latest_complete":
        try:
            parsed_quarter = date.fromisoformat(as_of_quarter)
        except ValueError as error:
            raise ValueError(
                "as_of_quarter must be latest_complete or an ISO quarter-start date."
            ) from error
        if parsed_quarter.day != 1 or parsed_quarter.month not in {1, 4, 7, 10}:
            raise ValueError("An explicit as_of_quarter must be the first day of a quarter.")

    trailing_window = int(rules["country_risk_trailing_eligible_quarters"])
    if trailing_window < 1:
        raise ValueError("country_risk_trailing_eligible_quarters must be positive.")

    hysteresis = rules.get("flag_hysteresis")
    if not isinstance(hysteresis, dict):
        raise ValueError("Risk rules must define flag_hysteresis.")
    margin = float(hysteresis["score_increase_margin"])
    confirmations = int(hysteresis["confirming_eligible_quarters"])
    if not 0 <= margin <= 1:
        raise ValueError("score_increase_margin must be between 0 and 1.")
    if confirmations < 2:
        raise ValueError("confirming_eligible_quarters must be at least 2.")

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
    low_confidence = int(row["eligible_quarters_to_date"]) < int(
        confidence_rules["minimum_eligible_quarters"]
    ) or float(row["country_coverage"]) < float(confidence_rules["minimum_country_coverage"])
    return {
        "supplier_key": str(row["supplier_key"]),
        "quarter": row["quarter"],
        "score": score,
        "tier": tier,
        "contributing_factors": factors,
        "confidence": "low" if low_confidence else "full",
        "rules_version": str(rules["rules_version"]),
        "eligible_quarters_to_date": int(row["eligible_quarters_to_date"]),
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
    # Phase 9a: a flag keeps its row when it stops qualifying; it is resolved instead.
    for column, definition in (
        ("event_id", "VARCHAR"),
        ("status", "VARCHAR DEFAULT 'active'"),
        ("resolved_at", "TIMESTAMPTZ"),
        ("resolution", "VARCHAR"),
    ):
        connection.execute(
            f"ALTER TABLE gold.supplier_risk_flags ADD COLUMN IF NOT EXISTS {column} {definition}"
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
    target: FlagTarget = FLAGSHIP,
) -> None:
    connection.executemany(
        f"""
        INSERT INTO {target.scores_table} (
            {target.key_column}, quarter, score, tier, contributing_factors,
            confidence, rules_version
        )
        VALUES (?, ?, ?, ?, ?::JSON, ?, ?)
        ON CONFLICT ({target.key_column}, quarter) DO UPDATE SET
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


TOP_FACTOR_FALLBACK = "largest_contributor_fallback"


def select_top_factor(
    changes: Mapping[str, float],
    factors: Mapping[str, Mapping[str, Any]],
    direction: int,
) -> tuple[str, bool]:
    """Name the factor that drove a tier change, and whether a fallback was needed.

    For a rise (direction 1) only factors whose weighted contribution increased
    can be named; for a fall (direction -1) only those that decreased. The largest
    such change wins. If none moved in that direction, the largest current
    contributor is named and the fallback is reported.
    """
    moved = {name: change for name, change in changes.items() if change * direction > 0}
    if moved:
        return max(moved, key=lambda name: abs(moved[name])), False
    return (
        max(factors, key=lambda name: float(factors[name]["weighted_contribution"])),
        True,
    )


def _historical_tier_flags(
    scores: Sequence[Mapping[str, Any]],
    rules: Mapping[str, Any],
) -> list[dict[str, Any]]:
    ranks = {tier: rank for rank, tier in enumerate(rules["tier_order"])}
    flags: list[dict[str, Any]] = []
    grouped_scores: dict[str, list[Mapping[str, Any]]] = {}
    for score in scores:
        grouped_scores.setdefault(str(score["supplier_key"]), []).append(score)

    hysteresis = rules["flag_hysteresis"]
    score_margin = float(hysteresis["score_increase_margin"])
    confirming_quarters = int(hysteresis["confirming_eligible_quarters"])
    for supplier_scores in grouped_scores.values():
        ordered_scores = sorted(supplier_scores, key=lambda score: score["quarter"])
        for index in range(1, len(ordered_scores)):
            previous = ordered_scores[index - 1]
            score = ordered_scores[index]
            old_tier = str(previous["tier"])
            new_tier = str(score["tier"])
            if ranks[new_tier] <= ranks[old_tier]:
                continue

            score_increase = float(score["score"]) - float(previous["score"])
            transition_score = score
            confirmed_index = index
            if score_increase <= score_margin:
                confirmed_index = index + confirming_quarters - 1
                if confirmed_index >= len(ordered_scores):
                    continue
                if any(
                    ordered_scores[held_index]["tier"] != new_tier
                    for held_index in range(index, confirmed_index + 1)
                ):
                    continue
                score = ordered_scores[confirmed_index]

            contributing_changes = {
                name: float(factor["weighted_contribution"])
                - float(previous["contributing_factors"][name]["weighted_contribution"])
                for name, factor in transition_score["contributing_factors"].items()
            }
            # Flags are raised only for tier rises, so the direction is upward.
            top_factor_name, fallback = select_top_factor(
                contributing_changes, transition_score["contributing_factors"], direction=1
            )
            top_factor = {
                **transition_score["contributing_factors"][top_factor_name],
                "weighted_contribution_change": contributing_changes[top_factor_name],
                "observed_quarter": transition_score["quarter"].isoformat(),
            }
            if fallback:
                top_factor["selection"] = TOP_FACTOR_FALLBACK
            flags.append(
                {
                    **score,
                    "old_tier": old_tier,
                    "top_factor": {"name": top_factor_name, **top_factor},
                    "prior_eligible_quarters": (int(score["eligible_quarters_to_date"]) - 1),
                }
            )
    return flags


def _supersede_previous_flags(
    connection: duckdb.DuckDBPyConnection,
    rules_version: str,
    target: FlagTarget = FLAGSHIP,
) -> None:
    active_prefix = f"{target.event_prefix}risk_flag:{rules_version}:"
    active_flag_keys = {
        (supplier_key, quarter)
        for supplier_key, quarter in connection.execute(
            """
            SELECT
                json_extract_string(payload, '$.supplier_key'),
                try_cast(json_extract_string(payload, '$.quarter') AS DATE)
            FROM agent.events
            WHERE event_type = 'risk_flag'
              AND source = ?
              AND event_id LIKE ?
            """,
            [target.source, f"{active_prefix}%"],
        ).fetchall()
    }
    obsolete_events = connection.execute(
        """
        SELECT
            event_id,
            json_extract_string(payload, '$.supplier_key') AS supplier_key,
            try_cast(json_extract_string(payload, '$.quarter') AS DATE) AS quarter
        FROM agent.events
        WHERE event_type = 'risk_flag'
          AND source = ?
          AND starts_with(event_id, ?)
          AND event_id NOT LIKE ?
        """,
        [target.source, target.event_prefix, f"{active_prefix}%"],
    ).fetchall()
    for event_id, supplier_key, quarter in obsolete_events:
        connection.execute(
            """
            UPDATE agent.events
            SET resolved_at = current_timestamp,
                resolution = 'superseded by stabilised scoring'
            WHERE event_id = ?
              AND resolution IS DISTINCT FROM 'superseded by stabilised scoring'
            """,
            [event_id],
        )
        if (
            supplier_key is not None
            and quarter is not None
            and (supplier_key, quarter) not in active_flag_keys
        ):
            connection.execute(
                f"""
                DELETE FROM {target.flags_table}
                WHERE {target.key_column} = ? AND quarter = ?
                """,
                [supplier_key, quarter],
            )


PAYLOAD_HASH_CHARS = 10


PAYLOAD_HASH_DECIMALS = 10


def _round_floats(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, PAYLOAD_HASH_DECIMALS)
    if isinstance(value, Mapping):
        return {key: _round_floats(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_round_floats(item) for item in value]
    return value


def payload_hash(payload: Mapping[str, Any]) -> str:
    """Short, stable hash of an event payload (key order does not matter).

    Floats are rounded to 10 decimal places for the hash only (stored payloads
    keep full precision): rebuilding the gold mart sums floats in a different
    order each time, which moves country_risk, and so scores, by ~1e-16.
    """
    canonical = json.dumps(
        _round_floats(payload), sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:PAYLOAD_HASH_CHARS]


def _event_payload(flag: Mapping[str, Any], target: FlagTarget = FLAGSHIP) -> dict[str, Any]:
    if target.dataset_id is not None:
        return {"dataset_id": target.dataset_id, **_event_payload(flag)}
    return {
        "supplier_key": flag["supplier_key"],
        "quarter": flag["quarter"].isoformat(),
        "old_tier": flag["old_tier"],
        "new_tier": flag["tier"],
        "score": flag["score"],
        "top_contributing_factor": flag["top_factor"],
        "confidence": flag["confidence"],
        "prior_eligible_quarters": flag["prior_eligible_quarters"],
    }


def _active_flag_events(
    connection: duckdb.DuckDBPyConnection, base_id: str, source: str = FLAGSHIP.source
) -> list[tuple[str, Any]]:
    return connection.execute(
        """
        SELECT event_id, payload
        FROM agent.events
        WHERE event_type = 'risk_flag'
          AND source = ?
          AND (event_id = ? OR starts_with(event_id, ?))
          AND resolved_at IS NULL
        ORDER BY created_at
        """,
        [source, base_id, f"{base_id}:p"],
    ).fetchall()


def _stored_hash(payload: Any) -> str:
    return payload_hash(json.loads(payload) if isinstance(payload, str) else payload)


def _write_flags_and_events(
    connection: duckdb.DuckDBPyConnection,
    flags: Sequence[Mapping[str, Any]],
    target: FlagTarget = FLAGSHIP,
) -> tuple[int, int, int]:
    """Upsert current flags and keep flag events append-only.

    An event whose payload is unchanged keeps its id and is not touched. When a
    payload would change, the old event is kept and marked superseded (its
    investigations stay bound to the numbers they saw) and a new event is created
    whose id carries the payload hash.
    """
    flags_added = 0
    events_added = 0
    events_superseded = 0
    for flag in flags:
        event = _event_payload(flag, target)
        digest = payload_hash(event)
        base_id = (
            f"{target.event_prefix}risk_flag:{flag['rules_version']}:"
            f"{flag['quarter']}:{flag['supplier_key']}"
        )

        active = _active_flag_events(connection, base_id, target.source)
        current_id = next(
            (event_id for event_id, payload in active if _stored_hash(payload) == digest),
            None,
        )
        if current_id is None:
            hashed_id = f"{base_id}:p{digest}"
            known = {
                row[0]
                for row in connection.execute(
                    "SELECT event_id FROM agent.events WHERE event_id IN (?, ?)",
                    [base_id, hashed_id],
                ).fetchall()
            }
            current_id = base_id if base_id not in known else hashed_id
            for old_id, _payload in active:
                connection.execute(
                    """
                    UPDATE agent.events
                    SET resolved_at = current_timestamp, resolution = ?
                    WHERE event_id = ?
                    """,
                    [f"superseded: payload changed; replaced by {current_id}", old_id],
                )
                events_superseded += 1
            if current_id in known:
                # This exact payload was seen before: reopen that event, never rewrite it.
                connection.execute(
                    "UPDATE agent.events SET resolved_at = NULL, resolution = NULL "
                    "WHERE event_id = ?",
                    [current_id],
                )
            else:
                connection.execute(
                    """
                    INSERT INTO agent.events (
                        event_id, event_type, source, batch_id, created_at, payload
                    )
                    VALUES (?, 'risk_flag', ?, ?, current_timestamp, ?::JSON)
                    """,
                    [
                        current_id,
                        target.source,
                        flag["quarter"].isoformat(),
                        json.dumps(event, sort_keys=True),
                    ],
                )
                events_added += 1

        flag_values = [
            flag["old_tier"],
            flag["tier"],
            flag["score"],
            json.dumps(flag["top_factor"], sort_keys=True),
            flag["confidence"],
            current_id,
        ]
        existing_flag = connection.execute(
            f"SELECT 1 FROM {target.flags_table} WHERE {target.key_column} = ? AND quarter = ?",
            [flag["supplier_key"], flag["quarter"]],
        ).fetchone()
        if existing_flag:
            connection.execute(
                f"""
                UPDATE {target.flags_table}
                SET old_tier = ?, new_tier = ?, score = ?,
                    top_contributing_factor = ?::JSON, confidence = ?, event_id = ?,
                    status = 'active', resolved_at = NULL, resolution = NULL
                WHERE {target.key_column} = ? AND quarter = ?
                """,
                [*flag_values, flag["supplier_key"], flag["quarter"]],
            )
        else:
            connection.execute(
                f"""
                INSERT INTO {target.flags_table} (
                    {target.key_column}, quarter, old_tier, new_tier, score,
                    top_contributing_factor, confidence, event_id, status
                )
                VALUES (?, ?, ?, ?, ?, ?::JSON, ?, ?, 'active')
                """,
                [flag["supplier_key"], flag["quarter"], *flag_values],
            )
            flags_added += 1
    return flags_added, events_added, events_superseded


def _resolve_unqualified_flags(
    connection: duckdb.DuckDBPyConnection,
    flags: Sequence[Mapping[str, Any]],
    rules_version: str,
    target: FlagTarget = FLAGSHIP,
) -> int:
    """Resolve, never delete, flags of this rules version that no longer qualify."""
    current = {(flag["supplier_key"], flag["quarter"]) for flag in flags}
    reason = f"resolved: no longer qualifies under rules {rules_version}"
    stale_events = connection.execute(
        """
        SELECT
            event_id,
            json_extract_string(payload, '$.supplier_key'),
            try_cast(json_extract_string(payload, '$.quarter') AS DATE)
        FROM agent.events
        WHERE event_type = 'risk_flag'
          AND source = ?
          AND starts_with(event_id, ?)
          AND resolved_at IS NULL
        """,
        [target.source, f"{target.event_prefix}risk_flag:{rules_version}:"],
    ).fetchall()
    resolved = 0
    for event_id, supplier_key, quarter in stale_events:
        if (supplier_key, quarter) in current:
            continue
        connection.execute(
            "UPDATE agent.events SET resolved_at = current_timestamp, resolution = ? "
            "WHERE event_id = ?",
            [reason, event_id],
        )
        connection.execute(
            f"""
            UPDATE {target.flags_table}
            SET status = 'resolved', resolved_at = current_timestamp, resolution = ?
            WHERE {target.key_column} = ? AND quarter = ?
              AND coalesce(status, 'active') = 'active'
            """,
            [reason, supplier_key, quarter],
        )
        resolved += 1
    return resolved


def current_quarter_for(scores: Sequence[Mapping[str, Any]], rules: Mapping[str, Any]) -> date:
    as_of_quarter = rules.get("as_of_quarter", "latest_complete")
    current_quarter = (
        max(score["quarter"] for score in scores)
        if as_of_quarter == "latest_complete"
        else date.fromisoformat(str(as_of_quarter))
    )
    if not any(score["quarter"] == current_quarter for score in scores):
        raise ValueError(
            f"as_of_quarter {current_quarter.isoformat()} has no eligible supplier-quarter rows."
        )
    return current_quarter


def write_scores_and_flags(
    connection: duckdb.DuckDBPyConnection,
    scores: Sequence[Mapping[str, Any]],
    rules: Mapping[str, Any],
    target: FlagTarget,
    excluded_factors: Mapping[str, str] | None = None,
) -> tuple[int, int, int, int]:
    """Supersede, flag (hysteresis), upsert scores, write events, resolve.

    The caller owns the transaction. A factor in `excluded_factors` (name ->
    reason) was left out of the score; it is recorded in each score's
    contributing_factors after flagging, with weight 0, so it can never be named
    as a flag's top factor. Returns (flags added, events added, events
    superseded, flags resolved).
    """
    rules_version = str(rules["rules_version"])
    _supersede_previous_flags(connection, rules_version, target)
    flags = _historical_tier_flags(scores, rules)
    for score in scores:
        for name, reason in (excluded_factors or {}).items():
            score["contributing_factors"][name] = {
                "excluded": True,
                "reason": reason,
                "weight": 0.0,
                "weighted_contribution": 0.0,
            }
    _insert_or_update_scores(connection, scores, target)
    flags_added, events_added, superseded = _write_flags_and_events(connection, flags, target)
    resolved = _resolve_unqualified_flags(connection, flags, rules_version, target)
    return flags_added, events_added, superseded, resolved


def score_supplier_risk(
    db_path: str | Path | None = None,
    rules_path: str | Path = DEFAULT_RULES_PATH,
) -> ScoringSummary:
    rules = load_rules(rules_path)
    target_db = Path(db_path or os.environ.get("PROVENANCE_DUCKDB_PATH") or DEFAULT_DB_PATH)
    with duckdb.connect(str(target_db)) as connection:
        _create_score_tables(connection)
        ensure_agent_events_table(connection)
        source_rows = _mart_rows(connection)
        if not source_rows:
            raise ValueError("gold.mart_supplier_risk has no eligible supplier-quarter rows.")
        scores = score_rows(source_rows, rules)
        as_of_quarter = rules.get("as_of_quarter", "latest_complete")
        current_quarter = (
            max(score["quarter"] for score in scores)
            if as_of_quarter == "latest_complete"
            else date.fromisoformat(str(as_of_quarter))
        )
        if not any(score["quarter"] == current_quarter for score in scores):
            raise ValueError(
                f"as_of_quarter {current_quarter.isoformat()} has no eligible "
                "supplier-quarter rows."
            )
        # One transaction: a failure part-way leaves the previous scores and flags.
        connection.execute("BEGIN TRANSACTION")
        try:
            _supersede_previous_flags(connection, str(rules["rules_version"]))
            flags = _historical_tier_flags(scores, rules)
            _insert_or_update_scores(connection, scores)
            flags_added, events_added, superseded = _write_flags_and_events(connection, flags)
            resolved = _resolve_unqualified_flags(connection, flags, str(rules["rules_version"]))
        except BaseException:
            connection.execute("ROLLBACK")
            raise
        connection.execute("COMMIT")

    return ScoringSummary(
        quarter=current_quarter,
        scores_written=len(scores),
        flags_emitted=flags_added,
        events_emitted=events_added,
        events_superseded=superseded,
        flags_resolved=resolved,
    )
