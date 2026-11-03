"""Read-only queries the console needs that the agent package does not already have.

Everything else (flag detail, supplier history, score breakdown, market mix,
actions, audit log) is served by the existing agent functions; see main.py.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import duckdb

from provenance.agent.grounding import NUMBER_PATTERN, check_grounding
from provenance.agent.store import table_exists
from provenance.agent.tools.investigation_tools import parse_quarter, quarter_label


def _iso(value: Any) -> Any:
    return value.isoformat() if hasattr(value, "isoformat") else value


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def list_flags(
    connection: duckdb.DuckDBPyConnection,
    tier: str | None = None,
    quarter: date | None = None,
    confidence: str | None = None,
) -> list[dict[str, Any]]:
    """Current flags with their event id and the latest investigation and action."""
    has_investigations = table_exists(connection, "agent", "investigations")
    has_actions = table_exists(connection, "agent", "actions")
    investigation_columns = "latest.status, latest.grounded" if has_investigations else "NULL, NULL"
    investigation_join = (
        """
        LEFT JOIN (
            SELECT event_id, status, grounded
            FROM agent.investigations
            QUALIFY row_number() OVER (PARTITION BY event_id ORDER BY created_at DESC) = 1
        ) AS latest ON latest.event_id = events.event_id
        """
        if has_investigations
        else ""
    )
    action_columns = "action.action_type, action.status" if has_actions else "NULL, NULL"
    # Phase 9a: a flag stores its current event id (it may carry a payload hash
    # after a superseding rescore) and is resolved rather than deleted.
    flag_columns = {
        row[0]
        for row in connection.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'gold' AND table_name = 'supplier_risk_flags'"
        ).fetchall()
    }
    constructed_id = (
        "'risk_flag:' || scores.rules_version || ':' "
        "|| strftime(flags.quarter, '%Y-%m-%d') || ':' || flags.supplier_key"
    )
    event_match = (
        f"coalesce(flags.event_id, {constructed_id})"
        if "event_id" in flag_columns
        else constructed_id
    )
    active_only = (
        "AND coalesce(flags.status, 'active') = 'active'" if "status" in flag_columns else ""
    )
    action_join = (
        """
        LEFT JOIN (
            SELECT event_id, action_type, status
            FROM agent.actions
            QUALIFY row_number() OVER (PARTITION BY event_id ORDER BY proposed_at DESC) = 1
        ) AS action ON action.event_id = events.event_id
        """
        if has_actions
        else ""
    )
    rows = connection.execute(
        f"""
        SELECT
            events.event_id, flags.supplier_key, names.supplier_name, flags.quarter,
            flags.old_tier, flags.new_tier, flags.score, flags.confidence,
            flags.top_contributing_factor,
            -- Unresolved only: a resolved event keeps its old label in the column.
            CASE WHEN events.resolved_at IS NOT NULL THEN 'resolved'
                 ELSE events.investigation_status END,
            {investigation_columns}, {action_columns}
        FROM gold.supplier_risk_flags AS flags
        INNER JOIN gold.supplier_risk_score AS scores
            ON scores.supplier_key = flags.supplier_key AND scores.quarter = flags.quarter
        INNER JOIN agent.events AS events
            ON events.event_id = {event_match}
        LEFT JOIN (
            SELECT DISTINCT supplier_key, supplier_name
            FROM silver.dim_supplier_scd2 WHERE is_current
        ) AS names ON names.supplier_key = flags.supplier_key
        {investigation_join}
        {action_join}
        WHERE (? IS NULL OR flags.new_tier = ?)
          AND (?::DATE IS NULL OR flags.quarter = ?::DATE)
          AND (? IS NULL OR flags.confidence = ?)
          {active_only}
        ORDER BY flags.quarter DESC, flags.score DESC
        """,
        [tier, tier, quarter, quarter, confidence, confidence],
    ).fetchall()
    flags = []
    for row in rows:
        (
            event_id, supplier_key, supplier_name, flag_quarter, old_tier, new_tier, score,
            flag_confidence, factor, queue_status, investigation_status, grounded,
            action_type, action_status,
        ) = row  # fmt: skip
        factor = _json(factor) or {}
        flags.append(
            {
                "event_id": event_id,
                "supplier_key": supplier_key,
                "supplier_name": supplier_name,
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
    return flags


def _round(value: Any) -> float | None:
    return round(float(value), 3) if value is not None else None


def supplier_name(connection: duckdb.DuckDBPyConnection, supplier_key: str) -> str | None:
    row = connection.execute(
        "SELECT supplier_name FROM silver.dim_supplier_scd2 WHERE supplier_key = ? "
        "ORDER BY version_number DESC LIMIT 1",
        [supplier_key],
    ).fetchone()
    return row[0] if row else None


def _event_for(
    connection: duckdb.DuckDBPyConnection, supplier_key: str, quarter_text: str
) -> str | None:
    quarter = parse_quarter(quarter_text)
    if quarter is None:
        return None
    row = connection.execute(
        """
        SELECT event_id FROM agent.events
        WHERE event_type = 'risk_flag'
          AND json_extract_string(payload, '$.supplier_key') = ?
          AND json_extract_string(payload, '$.quarter') = ?
        ORDER BY created_at DESC LIMIT 1
        """,
        [supplier_key, quarter.isoformat()],
    ).fetchone()
    return row[0] if row else None


def receipt_spans(text: str, tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Split text into plain and number spans, each number tied to the tool it came from.

    Uses the agent's own grounding check one tool call at a time, so the console
    shows exactly the attribution the grounding gate accepted.
    """
    successful = [call for call in tool_calls if "error" not in call["result"]]
    spans: list[dict[str, Any]] = []
    cursor = 0
    for match in NUMBER_PATTERN.finditer(text):
        start, end = match.span()
        token = match.group(0).rstrip()
        end = start + len(token)
        if start > cursor:
            spans.append({"kind": "text", "text": text[cursor:start]})
        source = next(
            (
                call["name"]
                for call in successful
                if check_grounding({"value": token}, [call]).grounded
            ),
            None,
        )
        spans.append({"kind": "number", "text": token, "tool": source})
        cursor = end
    if cursor < len(text):
        spans.append({"kind": "text", "text": text[cursor:]})
    return spans


def latest_investigation(
    connection: duckdb.DuckDBPyConnection, event_id: str
) -> dict[str, Any] | None:
    if not table_exists(connection, "agent", "investigations"):
        return None
    has_decisions = bool(
        connection.execute(
            "SELECT 1 FROM information_schema.columns WHERE table_schema = 'agent' "
            "AND table_name = 'investigations' AND column_name = 'human_decision'"
        ).fetchone()
    )
    decision_columns = (
        "human_decision, decided_by, decision_reason, decided_at"
        if has_decisions
        else "NULL, NULL, NULL, NULL"
    )
    row = connection.execute(
        f"""
        SELECT investigation_id, report, status, grounded, grounded_ratio, ungrounded_numbers,
               tool_calls, steps, prompt_tokens, completion_tokens, total_tokens, model,
               created_at, {decision_columns},
               count(*) OVER () AS runs
        FROM agent.investigations
        WHERE event_id = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        [event_id],
    ).fetchone()
    if row is None:
        return None
    (
        investigation_id, report, status, grounded, grounded_ratio, ungrounded, tool_calls,
        steps, prompt_tokens, completion_tokens, total_tokens, model, created_at,
        human_decision, decided_by, decision_reason, decided_at, runs,
    ) = row  # fmt: skip
    report = _json(report)
    tool_calls = _json(tool_calls) or []
    action = None
    if table_exists(connection, "agent", "actions"):
        action_row = connection.execute(
            "SELECT action_id, action_type, status FROM agent.actions "
            "WHERE investigation_id = ?",
            [investigation_id],
        ).fetchone()
        if action_row:
            action = dict(zip(("action_id", "action_type", "status"), action_row, strict=True))

    recalled = []
    for call in tool_calls:
        if call["name"] != "recall_past_investigations" or "error" in call["result"]:
            continue
        supplier_key = call["result"].get("supplier_key")
        for past in call["result"].get("past_investigations", []):
            recalled.append(
                {**past, "event_id": _event_for(connection, supplier_key, past["quarter"])}
            )

    evidence = []
    if report:
        for item in report.get("evidence", []):
            value = str(item.get("value"))
            evidence.append(
                {
                    "tool_name": item.get("tool_name"),
                    "value": value,
                    "spans": receipt_spans(
                        value,
                        [call for call in tool_calls if call["name"] == item.get("tool_name")],
                    ),
                }
            )
    return {
        "event_id": event_id,
        "investigation_id": investigation_id,
        "status": status,
        "grounded": bool(grounded),
        "grounded_ratio": grounded_ratio,
        "ungrounded_numbers": _json(ungrounded) or [],
        "steps": steps,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "model": model,
        "created_at": _iso(created_at),
        "runs": runs,
        "human_decision": human_decision,
        "decided_by": decided_by,
        "decision_reason": decision_reason,
        "decided_at": _iso(decided_at),
        "report": (
            {
                "summary": report.get("summary"),
                "summary_spans": receipt_spans(str(report.get("summary", "")), tool_calls),
                "top_factor": report.get("top_factor"),
                "evidence": evidence,
                "proposed_action": report.get("proposed_action"),
                "confidence": report.get("confidence"),
            }
            if report
            else None
        ),
        "tool_calls": [
            {"step": call["step"], "name": call["name"], "ok": "error" not in call["result"]}
            for call in tool_calls
        ],
        "recalled": recalled,
        "action": action,
    }


def quality_results(connection: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    """Quality-gate rows, trimmed: never the raw result blob or warning text."""
    if not table_exists(connection, "bronze", "dq_results"):
        return []
    rows = connection.execute(
        """
        SELECT source, batch_id, status, passed_expectations, failed_expectations,
               coalesce(json_array_length(warnings), 0), checked_at
        FROM bronze.dq_results
        ORDER BY checked_at DESC
        """
    ).fetchall()
    return [
        {
            "source": source,
            "batch_id": batch_id,
            "status": status,
            "passed": status in ("validated", "passed", "passed_with_warnings"),
            "passed_expectations": passed,
            "failed_expectations": failed,
            "warning_count": warnings,
            "checked_at": _iso(checked_at),
        }
        for source, batch_id, status, passed, failed, warnings, checked_at in rows
    ]
