"""Human-approval guardrail for agent actions.

The model can only propose. An action takes effect in exactly one place,
apply_action(), and only when the action row is 'approved' by a named person and
the matching approval is in the audit log. AGENT_REQUIRE_APPROVAL=false does not
bypass this; it only produces a warning.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import duckdb

from provenance.agent.memory.investigation_memory import record_human_decision
from provenance.agent.store import ALLOWED_ACTIONS, append_audit, now, table_exists, writer
from provenance.db import connect_read_only
from provenance.settings import TRUE_VALUES

LOGGER = logging.getLogger(__name__)
AGENT_ACTOR = "agent"
MAX_NAME_CHARS = 64
MAX_REASON_CHARS = 500
MIN_ID_PREFIX = 8


class ApprovalError(RuntimeError):
    """A decision or application request that the guardrail refuses."""


class ApprovalRequired(ApprovalError):
    """Raised when something tries to apply an action that is not approved."""


@dataclass(frozen=True)
class ActionRecord:
    action_id: str
    investigation_id: str
    event_id: str
    supplier_key: str | None
    quarter: date | None
    action_type: str
    status: str
    decided_by: str | None
    reason: str | None


def requires_approval_setting() -> bool:
    return os.environ.get("AGENT_REQUIRE_APPROVAL", "true").strip().lower() in TRUE_VALUES


def sanitize_action(proposed: Any) -> tuple[str, bool]:
    """Return (stored action_type, whether the model's value was rejected)."""
    if isinstance(proposed, str) and proposed in ALLOWED_ACTIONS:
        return proposed, False
    return "no_action", True


def propose_action(
    connection: duckdb.DuckDBPyConnection,
    investigation_id: str,
    event_id: str,
    proposed: Any,
) -> str:
    """Store a 'proposed' action for a grounded investigation (writer connection)."""
    action_type, rejected = sanitize_action(proposed)
    if rejected:
        LOGGER.warning("Model proposed a disallowed action; storing no_action instead.")
        append_audit(
            connection,
            AGENT_ACTOR,
            "action_type_rejected",
            {
                "investigation_id": investigation_id,
                "event_id": event_id,
                "proposed": str(proposed)[:MAX_NAME_CHARS],
                "stored": action_type,
            },
        )
    event = None
    if table_exists(connection, "agent", "events"):
        # Supplier and quarter come from the pipeline's event, never from the model.
        event = connection.execute(
            "SELECT json_extract_string(payload, '$.supplier_key'), "
            "json_extract_string(payload, '$.quarter') FROM agent.events WHERE event_id = ?",
            [event_id],
        ).fetchone()
    supplier_key, quarter = event if event else (None, None)
    action_id = str(uuid.uuid4())
    connection.execute(
        """
        INSERT INTO agent.actions
            (action_id, investigation_id, event_id, supplier_key, quarter, action_type,
             status, proposed_at)
        VALUES (?, ?, ?, ?, ?, ?, 'proposed', ?)
        """,
        [action_id, investigation_id, event_id, supplier_key, quarter, action_type, now()],
    )
    append_audit(
        connection,
        AGENT_ACTOR,
        "action_proposed",
        {
            "action_id": action_id,
            "investigation_id": investigation_id,
            "event_id": event_id,
            "action_type": action_type,
        },
    )
    return action_id


def _resolve_action_id(connection: duckdb.DuckDBPyConnection, action_id: str) -> str:
    text = (action_id or "").strip()
    if len(text) < MIN_ID_PREFIX:
        raise ApprovalError(f"Give at least {MIN_ID_PREFIX} characters of the action id.")
    matches = connection.execute(
        "SELECT action_id FROM agent.actions WHERE starts_with(action_id, ?)", [text]
    ).fetchall()
    if not matches:
        raise ApprovalError(f"No action matches {text!r}.")
    if len(matches) > 1:
        raise ApprovalError(f"{text!r} matches {len(matches)} actions; give more characters.")
    return matches[0][0]


def _load(connection: duckdb.DuckDBPyConnection, action_id: str) -> ActionRecord:
    row = connection.execute(
        """
        SELECT action_id, investigation_id, event_id, supplier_key, quarter, action_type,
               status, decided_by, reason
        FROM agent.actions WHERE action_id = ?
        """,
        [action_id],
    ).fetchone()
    if row is None:
        raise ApprovalError(f"No action {action_id!r}.")
    return ActionRecord(*row)


def _clean_name(name: Any) -> str:
    text = str(name or "").strip()
    if not text or len(text) > MAX_NAME_CHARS:
        raise ApprovalError(f"--by must be a name of 1-{MAX_NAME_CHARS} characters.")
    return text


def _clean_reason(reason: Any, required: bool) -> str | None:
    text = str(reason or "").strip()[:MAX_REASON_CHARS]
    if required and not text:
        raise ApprovalError("A reason is required.")
    return text or None


def _decide(
    connection: duckdb.DuckDBPyConnection,
    action_id: str,
    decision: str,
    decided_by: str,
    reason: str | None,
) -> ActionRecord:
    action = _load(connection, _resolve_action_id(connection, action_id))
    if action.status != "proposed":
        raise ApprovalError(f"Action {action.action_id} is already {action.status}.")
    decided_at = now()
    status = "approved" if decision == "approve" else "rejected"
    connection.execute(
        """
        UPDATE agent.actions
        SET status = ?, decided_at = ?, decided_by = ?, reason = ?
        WHERE action_id = ? AND status = 'proposed'
        """,
        [status, decided_at, decided_by, reason, action.action_id],
    )
    record_human_decision(
        connection, action.investigation_id, status, decided_by, reason, decided_at
    )
    append_audit(
        connection,
        decided_by,
        f"action_{status}",
        {"action_id": action.action_id, "event_id": action.event_id, "reason": reason},
    )
    return _load(connection, action.action_id)


def _has_approval_audit(connection: duckdb.DuckDBPyConnection, action: ActionRecord) -> bool:
    return bool(
        connection.execute(
            """
            SELECT 1 FROM agent.audit_log
            WHERE event = 'action_approved'
              AND json_extract_string(detail, '$.action_id') = ?
              AND actor = ?
            """,
            [action.action_id, action.decided_by],
        ).fetchone()
    )


def apply_action(
    connection: duckdb.DuckDBPyConnection, action_id: str, actor: str
) -> dict[str, Any]:
    """The only place an action takes effect. Refusals are audited, never raised here.

    Returns {"applied": bool, ...}; callers raise ApprovalRequired after the
    transaction commits so the refusal's audit row is kept.
    """
    action = _load(connection, action_id)
    approved = (
        action.status == "approved"
        and bool(action.decided_by)
        and _has_approval_audit(connection, action)
    )
    if not approved:
        if not requires_approval_setting():
            LOGGER.warning(
                "AGENT_REQUIRE_APPROVAL is false, but actions are never applied "
                "without an explicit approval; refusing %s.",
                action.action_id,
            )
        append_audit(
            connection,
            actor,
            "apply_refused",
            {"action_id": action.action_id, "status": action.status},
        )
        return {"applied": False, "action_id": action.action_id, "status": action.status}

    applied_at = now()
    effect = None
    if action.action_type == "watchlist":
        connection.execute(
            "INSERT INTO agent.watchlist VALUES (?, ?, ?, ?, ?, ?)",
            [
                action.action_id,
                action.event_id,
                action.supplier_key,
                action.quarter,
                action.decided_by,
                applied_at,
            ],
        )
        effect = "agent.watchlist"
    elif action.action_type == "request_review":
        connection.execute(
            "INSERT INTO agent.review_requests VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                action.action_id,
                action.event_id,
                action.supplier_key,
                action.quarter,
                action.decided_by,
                action.reason,
                applied_at,
            ],
        )
        effect = "agent.review_requests"
    connection.execute(
        "UPDATE agent.actions SET status = 'applied', applied_at = ? "
        "WHERE action_id = ? AND status = 'approved'",
        [applied_at, action.action_id],
    )
    append_audit(
        connection,
        actor,
        "action_applied",
        {"action_id": action.action_id, "action_type": action.action_type, "effect": effect},
    )
    return {"applied": True, "action_id": action.action_id, "effect": effect}


def apply(db_path: str | Path, action_id: str, actor: str) -> dict[str, Any]:
    """Apply one action on its own; raises ApprovalRequired unless it is approved."""
    with writer(db_path) as connection:
        result = apply_action(connection, _resolve_action_id(connection, action_id), actor)
    if not result["applied"]:
        raise ApprovalRequired(
            f"Action {result['action_id']} is {result['status']}, not approved; not applied."
        )
    return result


def approve(
    db_path: str | Path, action_id: str, decided_by: str, reason: str | None = None
) -> dict[str, Any]:
    """Record a named approval, then apply the action in the same transaction."""
    name = _clean_name(decided_by)
    with writer(db_path) as connection:
        action = _decide(connection, action_id, "approve", name, _clean_reason(reason, False))
        result = apply_action(connection, action.action_id, name)
    return result


def reject(db_path: str | Path, action_id: str, decided_by: str, reason: str) -> ActionRecord:
    name = _clean_name(decided_by)
    with writer(db_path) as connection:
        return _decide(connection, action_id, "reject", name, _clean_reason(reason, True))


def list_actions(db_path: str | Path, include_decided: bool = False) -> list[dict[str, Any]]:
    with connect_read_only(db_path) as connection:
        if not table_exists(connection, "agent", "actions"):
            return []
        where = "" if include_decided else "WHERE actions.status = 'proposed'"
        rows = connection.execute(
            f"""
            SELECT actions.action_id, actions.event_id, actions.supplier_key, actions.quarter,
                   actions.action_type, actions.status, actions.proposed_at,
                   actions.decided_by, investigations.grounded_ratio
            FROM agent.actions AS actions
            LEFT JOIN agent.investigations AS investigations
                ON investigations.investigation_id = actions.investigation_id
            {where}
            ORDER BY actions.proposed_at
            """
        ).fetchall()
    columns = (
        "action_id",
        "event_id",
        "supplier_key",
        "quarter",
        "action_type",
        "status",
        "proposed_at",
        "decided_by",
        "grounded_ratio",
    )
    return [dict(zip(columns, row, strict=True)) for row in rows]


def read_audit_log(db_path: str | Path, limit: int = 20) -> list[dict[str, Any]]:
    with connect_read_only(db_path) as connection:
        if not table_exists(connection, "agent", "audit_log"):
            return []
        rows = connection.execute(
            "SELECT seq, ts, actor, event, detail FROM agent.audit_log "
            "ORDER BY seq DESC LIMIT ?",
            [limit],
        ).fetchall()
    return [
        {"seq": seq, "ts": ts, "actor": actor, "event": event, "detail": json.loads(detail)}
        for seq, ts, actor, event, detail in reversed(rows)
    ]
