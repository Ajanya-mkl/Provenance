"""Read-only queries over an onboarded dataset's ds_<id> tables (Phase 10c-2).

DataCo keeps its own queries in api/queries.py, untouched. These mirror their
shapes (a flag here looks like a DataCo flag to the console) and add the
dataset results: row counts, rejects, country match, the frozen rules and every
factor's status. Schema names come only from validated dataset ids.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import duckdb

from provenance.agent.store import table_exists
from provenance.agent.tools.investigation_tools import quarter_label
from provenance.datasets import registry
from provenance.datasets.identifiers import quote_schema, schema_for
from provenance.datasets.score import factor_report
from provenance.risk_engine.scorer import load_rules


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _round(value: Any, digits: int = 3) -> float | None:
    return round(float(value), digits) if value is not None else None


def has_scores(connection: duckdb.DuckDBPyConnection, dataset_id: str) -> bool:
    return table_exists(connection, schema_for(dataset_id), "risk_score")


def entity_name(connection: duckdb.DuckDBPyConnection, dataset_id: str, key: str) -> str | None:
    schema = schema_for(dataset_id)
    if not table_exists(connection, schema, "mart_risk"):
        return None
    row = connection.execute(
        f"SELECT any_value(entity_name) FROM {quote_schema(schema)}.mart_risk WHERE entity_key = ?",
        [key],
    ).fetchone()
    return row[0] if row else None


def list_dataset_flags(
    connection: duckdb.DuckDBPyConnection,
    dataset_id: str,
    tier: str | None = None,
    quarter: date | None = None,
    confidence: str | None = None,
) -> list[dict[str, Any]]:
    """Active flags of one dataset, in the same shape as the DataCo flag list."""
    if not has_scores(connection, dataset_id):
        return []
    schema = quote_schema(schema_for(dataset_id))
    has_investigations = table_exists(connection, "agent", "investigations")
    has_actions = table_exists(connection, "agent", "actions")
    investigation = (
        """LEFT JOIN (
            SELECT event_id, status, grounded FROM agent.investigations
            QUALIFY row_number() OVER (PARTITION BY event_id ORDER BY created_at DESC) = 1
        ) AS latest ON latest.event_id = events.event_id"""
        if has_investigations
        else ""
    )
    action = (
        """LEFT JOIN (
            SELECT event_id, action_type, status FROM agent.actions
            QUALIFY row_number() OVER (PARTITION BY event_id ORDER BY proposed_at DESC) = 1
        ) AS action ON action.event_id = events.event_id"""
        if has_actions
        else ""
    )
    rows = connection.execute(
        f"""
        SELECT events.event_id, flags.entity_key, names.entity_name, flags.quarter,
               flags.old_tier, flags.new_tier, flags.score, flags.confidence,
               flags.top_contributing_factor,
               CASE WHEN events.resolved_at IS NOT NULL THEN 'resolved'
                    ELSE events.investigation_status END,
               {"latest.status, latest.grounded" if has_investigations else "NULL, NULL"},
               {"action.action_type, action.status" if has_actions else "NULL, NULL"}
        FROM {schema}.risk_flags AS flags
        INNER JOIN agent.events AS events ON events.event_id = flags.event_id
        LEFT JOIN (
            SELECT entity_key, any_value(entity_name) AS entity_name
            FROM {schema}.mart_risk GROUP BY entity_key
        ) AS names ON names.entity_key = flags.entity_key
        {investigation}
        {action}
        WHERE flags.status = 'active'
          AND (CAST(? AS VARCHAR) IS NULL OR flags.new_tier = ?)
          AND (?::DATE IS NULL OR flags.quarter = ?::DATE)
          AND (CAST(? AS VARCHAR) IS NULL OR flags.confidence = ?)
        ORDER BY flags.quarter DESC, flags.score DESC
        """,
        [tier, tier, quarter, quarter, confidence, confidence],
    ).fetchall()
    items = []
    for (
        event_id, key, name, flag_quarter, old_tier, new_tier, score, flag_confidence, factor,
        queue_status, investigation_status, grounded, action_type, action_status,
    ) in rows:  # fmt: skip
        factor = _json(factor) or {}
        items.append(
            {
                "event_id": event_id,
                "dataset_id": dataset_id,
                "supplier_key": key,
                "supplier_name": name,
                "quarter": quarter_label(flag_quarter),
                "quarter_start": flag_quarter.isoformat(),
                "old_tier": old_tier,
                "new_tier": new_tier,
                "score": round(score, 3),
                "confidence": flag_confidence,
                "top_factor": {
                    "name": factor.get("name"),
                    "weighted_contribution": _round(factor.get("weighted_contribution")),
                    "weighted_contribution_change": _round(
                        factor.get("weighted_contribution_change")
                    ),
                },
                "queue_status": queue_status,
                "investigation": (
                    {"status": investigation_status, "grounded": bool(grounded)}
                    if investigation_status
                    else None
                ),
                "action": (
                    {"action_type": action_type, "status": action_status} if action_type else None
                ),
            }
        )
    return items


def results(connection: duckdb.DuckDBPyConnection, dataset_id: str) -> dict[str, Any] | None:
    """Everything the Results screen shows, or None before the first scoring."""
    frozen = (
        registry.frozen_rules(connection, dataset_id)
        if table_exists(connection, "meta", "dataset_rules")
        else None
    )
    if frozen is None or not has_scores(connection, dataset_id):
        return None
    schema = quote_schema(schema_for(dataset_id))
    run = registry.latest_run(connection, dataset_id, succeeded=True)
    summary = (run or {}).get("summary") or {}
    silver = summary.get("silver") or {}
    rows_out = connection.execute(f"SELECT count(*) FROM {schema}.silver_shipments").fetchone()[0]
    rejects = connection.execute(
        f"SELECT reason, count(*) FROM {schema}.silver_rejects GROUP BY 1 ORDER BY 2 DESC, 1"
    ).fetchall()
    rows_rejected = sum(count for _, count in rejects)
    scores = connection.execute(
        f"""
        SELECT score.entity_key, names.entity_name, score.quarter, score.score, score.tier,
               score.confidence
        FROM {schema}.risk_score AS score
        LEFT JOIN (
            SELECT entity_key, any_value(entity_name) AS entity_name
            FROM {schema}.mart_risk GROUP BY entity_key
        ) AS names ON names.entity_key = score.entity_key
        ORDER BY score.entity_key, score.quarter
        """
    ).fetchall()
    rules = frozen["rules"]
    flags = list_dataset_flags(connection, dataset_id)
    quarters = sorted({row[2] for row in scores})
    latest = quarters[-1] if quarters else None
    return {
        "dataset_id": dataset_id,
        "rules_version": frozen["rules_version"],
        "bounds_mode": frozen["bounds_mode"],
        "frozen_at": frozen["created_at"].isoformat() if frozen.get("created_at") else None,
        "comparability_note": "Scores are comparable within this dataset only.",
        "last_run": (
            {
                "run_id": run["run_id"],
                "trigger": run["trigger"],
                "finished_at": run["finished_at"].isoformat() if run["finished_at"] else None,
            }
            if run
            else None
        ),
        "counts": {
            "rows_in": silver.get("rows_in", rows_out + rows_rejected),
            "rows_out": rows_out,
            "rows_rejected": rows_rejected,
            "entities": len({row[0] for row in scores}),
            "quarters": len(quarters),
            "scores": len(scores),
            "active_flags": len(flags),
            "high_in_latest": sum(1 for row in scores if row[2] == latest and row[4] == "high"),
            "latest_quarter": quarter_label(latest) if latest else None,
        },
        "rejects": [{"reason": reason, "rows": count} for reason, count in rejects],
        "country": {
            "used": silver.get("country_used"),
            "reason": silver.get("country_reason"),
            **(silver.get("country") or {}),
        }
        if silver
        else None,
        "factors": factor_report(rules, load_rules()),
        "bounds": {
            name: {key: round(float(value), 4) for key, value in limits.items()}
            for name, limits in rules["normalization_bounds"].items()
        },
        "scores": [
            {
                "entity_key": key,
                "entity_name": name,
                "quarter": quarter_label(score_quarter),
                "quarter_start": score_quarter.isoformat(),
                "score": round(score, 4),
                "tier": tier,
                "confidence": confidence,
            }
            for key, name, score_quarter, score, tier, confidence in scores
        ],
        "flags": flags,
    }
