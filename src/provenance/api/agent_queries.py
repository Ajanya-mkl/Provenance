"""Read-only summary of the agent's work for the Overview's Agent card (Phase 10d)."""

from __future__ import annotations

import json
from typing import Any

import duckdb

from provenance.agent.approval import AGENT_ACTOR
from provenance.agent.store import table_exists


def _iso(value: Any) -> Any:
    return value.isoformat() if hasattr(value, "isoformat") else value


def agent_summary(connection: duckdb.DuckDBPyConnection, model: str) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "model": model,
        "investigations": 0,
        "grounded": 0,
        "grounded_rate": None,
        "pending_approvals": 0,
        "last_investigation_at": None,
        "last_agent_action": None,
    }
    if table_exists(connection, "agent", "investigations"):
        total, grounded, last = connection.execute(
            "SELECT count(*), count(*) FILTER (WHERE grounded), max(created_at) "
            "FROM agent.investigations"
        ).fetchone()
        summary.update(
            investigations=total,
            grounded=grounded,
            grounded_rate=round(grounded / total, 4) if total else None,
            last_investigation_at=_iso(last),
        )
    if table_exists(connection, "agent", "actions"):
        summary["pending_approvals"] = connection.execute(
            "SELECT count(*) FROM agent.actions WHERE status = 'proposed'"
        ).fetchone()[0]
    if table_exists(connection, "agent", "audit_log"):
        row = connection.execute(
            "SELECT seq, ts, event, detail FROM agent.audit_log WHERE actor = ? "
            "ORDER BY seq DESC LIMIT 1",
            [AGENT_ACTOR],
        ).fetchone()
        if row:
            detail = json.loads(row[3]) if isinstance(row[3], str) else row[3]
            summary["last_agent_action"] = {
                "seq": row[0],
                "ts": _iso(row[1]),
                "event": row[2],
                "event_id": (detail or {}).get("event_id"),
            }
    return summary
