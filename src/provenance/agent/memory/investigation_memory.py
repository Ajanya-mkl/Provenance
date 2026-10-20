"""Investigation memory: recall earlier grounded cases and remember human decisions.

Only investigations that passed grounding (status 'proposed') are ever recalled,
so an ungrounded or failed report can never feed a later run. Recall returns
structured fields, not earlier free-text summaries.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import duckdb

from provenance.agent.store import ALLOWED_ACTIONS, table_exists

KNOWN_FACTORS = ("late_rate", "delay_stddev", "country_risk")
MAX_RECALL = 5
MAX_REASON_CHARS = 200


def recall_past_investigations(
    connection: duckdb.DuckDBPyConnection, supplier_key: str, limit: int
) -> list[dict[str, Any]]:
    """Latest grounded investigation per earlier event for one supplier."""
    if not table_exists(connection, "agent", "investigations"):
        return []
    has_actions = table_exists(connection, "agent", "actions")
    action_join = (
        "LEFT JOIN agent.actions AS actions "
        "ON actions.investigation_id = investigations.investigation_id"
        if has_actions
        else ""
    )
    action_type = "actions.action_type" if has_actions else "NULL"
    # A table created in Phase 7a and opened read-only lacks the decision columns.
    has_decisions = bool(
        connection.execute(
            "SELECT 1 FROM information_schema.columns WHERE table_schema = 'agent' "
            "AND table_name = 'investigations' AND column_name = 'human_decision'"
        ).fetchone()
    )
    decision_columns = (
        "investigations.human_decision, investigations.decision_reason"
        if has_decisions
        else "NULL, NULL"
    )
    rows = connection.execute(
        f"""
        SELECT
            json_extract_string(events.payload, '$.quarter') AS quarter,
            json_extract_string(events.payload, '$.old_tier') AS old_tier,
            json_extract_string(events.payload, '$.new_tier') AS new_tier,
            json_extract(events.payload, '$.score')::DOUBLE AS score,
            json_extract_string(investigations.report, '$.top_factor') AS top_factor,
            coalesce(
                {action_type},
                json_extract_string(investigations.report, '$.proposed_action')
            ) AS proposed_action,
            {decision_columns}
        FROM agent.investigations AS investigations
        INNER JOIN agent.events AS events ON events.event_id = investigations.event_id
        {action_join}
        WHERE investigations.grounded
          AND investigations.status = 'proposed'
          AND json_extract_string(events.payload, '$.supplier_key') = ?
        QUALIFY row_number() OVER (
            PARTITION BY investigations.event_id ORDER BY investigations.created_at DESC
        ) = 1
        ORDER BY quarter DESC
        LIMIT ?
        """,
        [supplier_key, limit],
    ).fetchall()
    return [
        {
            "quarter": quarter,
            "old_tier": old_tier,
            "new_tier": new_tier,
            "score": round(score, 3) if score is not None else None,
            "top_factor": top_factor if top_factor in KNOWN_FACTORS else None,
            "proposed_action": action if action in ALLOWED_ACTIONS else None,
            "human_decision": decision,
            "decision_reason": (reason or "")[:MAX_REASON_CHARS] or None,
        }
        for quarter, old_tier, new_tier, score, top_factor, action, decision, reason in rows
    ]


def record_human_decision(
    connection: duckdb.DuckDBPyConnection,
    investigation_id: str,
    decision: str,
    decided_by: str,
    reason: str | None,
    decided_at: datetime,
) -> None:
    """Write an approve/reject decision onto the investigation (writer connection)."""
    connection.execute(
        """
        UPDATE agent.investigations
        SET human_decision = ?, decision_reason = ?, decided_by = ?, decided_at = ?
        WHERE investigation_id = ?
        """,
        [decision, reason, decided_by, decided_at, investigation_id],
    )
