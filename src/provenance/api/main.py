"""Read-only API for the Provenance console: uvicorn provenance.api.main:app --port 8000.

Every GET opens DuckDB read-only. Approve and reject are the only writes, and
they go through provenance.agent.approval (which writes through store.writer());
this module holds no write SQL. Nothing from .env is returned except the model
name and the approval mode the health check reports.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from provenance.agent import approval
from provenance.agent.eval.eval_runner import RESULTS_DIR
from provenance.agent.llm import DEFAULT_MODEL
from provenance.agent.store import table_exists, verify_audit_log
from provenance.agent.tools.investigation_tools import InvestigationTools, parse_quarter
from provenance.api import queries
from provenance.db import connect_read_only
from provenance.risk_engine.scorer import DEFAULT_DB_PATH

CONSOLE_ORIGIN = "http://localhost:5173"
TIERS = ("low", "medium", "high")
CONFIDENCES = ("full", "low")
AUDIT_LIMIT = 200


class Decision(BaseModel):
    by: str = ""
    reason: str | None = None


def _locked(_: Request, error: duckdb.IOException) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={
            "code": "locked",
            "detail": "The warehouse is locked by another writer (for example a Dagster run). "
            "Nothing was changed; try again when it finishes.",
        },
    )


def create_app(db_path: str | Path | None = None, results_dir: Path = RESULTS_DIR) -> FastAPI:
    database = Path(db_path or os.environ.get("PROVENANCE_DUCKDB_PATH", DEFAULT_DB_PATH))
    app = FastAPI(title="Provenance console API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[CONSOLE_ORIGIN],
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )
    app.add_exception_handler(duckdb.IOException, _locked)

    @contextmanager
    def read() -> Iterator[duckdb.DuckDBPyConnection]:
        with connect_read_only(database) as connection:
            yield connection

    def tool(method: str, *args: Any) -> dict[str, Any]:
        with InvestigationTools(database) as tools:
            result = getattr(tools, method)(*args)
        if "error" in result:
            raise HTTPException(status_code=404, detail=result["error"])
        return result

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        reachable = True
        try:
            with read() as connection:
                connection.execute("SELECT 1").fetchone()
        except duckdb.Error:
            reachable = False
        return {
            "status": "ok" if reachable else "degraded",
            "model": os.environ.get("LLM_MODEL_NAME", "").strip() or DEFAULT_MODEL,
            "approval_required": approval.requires_approval_setting(),
            "db_reachable": reachable,
        }

    @app.get("/api/flags")
    def flags(
        tier: str | None = Query(None),
        quarter: str | None = Query(None),
        confidence: str | None = Query(None),
    ) -> dict[str, Any]:
        if tier is not None and tier not in TIERS:
            raise HTTPException(status_code=400, detail=f"tier must be one of {TIERS}")
        if confidence is not None and confidence not in CONFIDENCES:
            raise HTTPException(status_code=400, detail=f"confidence must be one of {CONFIDENCES}")
        parsed = None
        if quarter is not None:
            parsed = parse_quarter(quarter)
            if parsed is None:
                raise HTTPException(status_code=400, detail="quarter must look like 2017-Q1")
        with read() as connection:
            items = queries.list_flags(connection, tier, parsed, confidence)
        return {"items": items, "count": len(items)}

    @app.get("/api/flags/{event_id}")
    def flag(event_id: str) -> dict[str, Any]:
        result = tool("get_flag", event_id)
        with read() as connection:
            result["supplier_name"] = queries.supplier_name(connection, result["supplier_key"])
        return result

    @app.get("/api/suppliers/{supplier_key}/history")
    def history(supplier_key: str) -> dict[str, Any]:
        return tool("get_supplier_history", supplier_key)

    @app.get("/api/suppliers/{supplier_key}/score/{quarter}")
    def score(supplier_key: str, quarter: str) -> dict[str, Any]:
        return tool("get_score_breakdown", supplier_key, quarter)

    @app.get("/api/suppliers/{supplier_key}/markets/{quarter}")
    def markets(supplier_key: str, quarter: str) -> dict[str, Any]:
        return tool("get_market_mix", supplier_key, quarter)

    @app.get("/api/investigations/{event_id}")
    def investigation(event_id: str) -> dict[str, Any]:
        with read() as connection:
            result = queries.latest_investigation(connection, event_id)
        if result is None:
            raise HTTPException(status_code=404, detail="No investigation for this event yet.")
        return result

    @app.get("/api/approvals")
    def approvals() -> dict[str, Any]:
        actions = approval.list_actions(database, include_decided=True)
        trail = approval.read_audit_log(database, AUDIT_LIMIT)
        items = []
        for action in actions:
            items.append(
                {
                    **{key: _plain(value) for key, value in action.items()},
                    "audit": [
                        _audit_entry(entry)
                        for entry in trail
                        if entry["detail"].get("action_id") == action["action_id"]
                    ],
                }
            )
        pending = sum(1 for item in items if item["status"] == "proposed")
        return {"items": items, "pending": pending}

    def decide(action_id: str, body: Decision, decision: str) -> dict[str, Any]:
        if not body.by.strip():
            raise HTTPException(status_code=400, detail="Reviewer name (by) is required.")
        try:
            if decision == "approve":
                result = approval.approve(database, action_id, body.by, body.reason)
                return {"status": "applied", **result}
            if not (body.reason or "").strip():
                raise HTTPException(status_code=400, detail="A reason is required to reject.")
            record = approval.reject(database, action_id, body.by, body.reason)
            return {"status": "rejected", "action_id": record.action_id}
        except approval.ApprovalError as error:
            message = str(error)
            if message.startswith("No action"):
                raise HTTPException(status_code=404, detail=message) from error
            if "already" in message:
                raise HTTPException(status_code=409, detail=message) from error
            raise HTTPException(status_code=400, detail=message) from error

    @app.post("/api/approvals/{action_id}/approve")
    def approve(action_id: str, body: Decision) -> dict[str, Any]:
        return decide(action_id, body, "approve")

    @app.post("/api/approvals/{action_id}/reject")
    def reject(action_id: str, body: Decision) -> dict[str, Any]:
        return decide(action_id, body, "reject")

    @app.get("/api/audit")
    def audit(limit: int = Query(50, ge=1, le=AUDIT_LIMIT)) -> dict[str, Any]:
        entries = approval.read_audit_log(database, limit)
        with read() as connection:
            if table_exists(connection, "agent", "audit_log"):
                broken = verify_audit_log(connection)
                total = connection.execute("SELECT count(*) FROM agent.audit_log").fetchone()[0]
            else:
                broken, total = [], 0
        return {
            "entries": [_audit_entry(entry) for entry in reversed(entries)],
            "chain": {
                "status": "broken" if broken else "intact",
                "broken_seqs": broken,
                "rows_verified": total,
                "verified_at": datetime.now(UTC).isoformat(timespec="seconds"),
            },
        }

    @app.get("/api/quality")
    def quality() -> dict[str, Any]:
        with read() as connection:
            items = queries.quality_results(connection)
        return {"items": items}

    @app.get("/api/evaluation")
    def evaluation() -> dict[str, Any]:
        path = results_dir / "report.json"
        if not path.exists():
            raise HTTPException(status_code=404, detail="No evaluation report yet.")
        report = json.loads(path.read_text(encoding="utf-8"))
        return {
            "generated_at": report.get("generated_at"),
            "model": report.get("model"),
            "case_count": report.get("case_count"),
            "metrics": {name: value["value"] for name, value in report["metrics"].items()},
        }

    return app


def _plain(value: Any) -> Any:
    return value.isoformat() if hasattr(value, "isoformat") else value


def _audit_entry(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "seq": entry["seq"],
        "ts": _plain(entry["ts"]),
        "actor": entry["actor"],
        "event": entry["event"],
        "detail": entry["detail"],
    }


app = create_app()
