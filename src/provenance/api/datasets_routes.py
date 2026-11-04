"""Dataset, mapping, run and results endpoints (Phases 10b and 10c-2).

Reads are read-only. Writes go only through provenance.datasets: onboarding and
registry.writer for datasets and runs, mapping_store for mappings; this module
holds no SQL. Every decision needs a reviewer name (`by`); a lock returns 409
'locked' via the app's handler, a second writer run 409 'busy', and an invalid
edit 400 with the validator's messages. Scoring never runs in the API process:
POST .../run starts a subprocess (datasets.runs) and GET /api/runs/{id} reports
its stages.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from provenance.api import dataset_queries
from provenance.datasets import mapping_store, registry, runs
from provenance.datasets.identifiers import DATASET_ID_PATTERN
from provenance.datasets.mapping_agent import MODES, propose_mapping
from provenance.datasets.onboard import PROJECT_ROOT, OnboardResult, Paths, onboard
from provenance.datasets.score import ScoringRefused, approved_mapping
from provenance.datasets.validator import MappingInvalid, load_context
from provenance.db import connect_read_only

DEFAULT_UPLOAD_MAX_MB = 50
CHUNK_BYTES = 1024 * 1024


def upload_limit_bytes() -> int:
    """DATASET_UPLOAD_MAX_MB (default 50)."""
    try:
        megabytes = float(os.environ.get("DATASET_UPLOAD_MAX_MB", DEFAULT_UPLOAD_MAX_MB))
    except ValueError:
        megabytes = DEFAULT_UPLOAD_MAX_MB
    return int(megabytes * 1024 * 1024)


class ProposeBody(BaseModel):
    by: str = ""
    heuristic_only: bool = False
    mode: str = "heuristic_first"


class MappingDecision(BaseModel):
    by: str = ""
    reason: str | None = None


class MappingEdit(BaseModel):
    by: str = ""
    set: dict[str, str | None] = Field(default_factory=dict)


def _require_by(by: str) -> str:
    if not by.strip():
        raise HTTPException(status_code=400, detail="Reviewer name (by) is required.")
    return by.strip()


def _check_dataset_id(dataset_id: str) -> None:
    if not DATASET_ID_PATTERN.match(dataset_id):
        raise HTTPException(status_code=404, detail="No such dataset.")


def _plain(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value.isoformat() if hasattr(value, "isoformat") else value


def _decision_error(error: mapping_store.DecisionError) -> HTTPException:
    message = str(error)
    if message.startswith("No mapping"):
        return HTTPException(status_code=404, detail=message)
    if " is " in message and ("not proposed" in message or "only a proposal" in message):
        return HTTPException(status_code=409, detail=message)
    return HTTPException(status_code=400, detail=message)


def _relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)


def onboarding_report(result: OnboardResult, paths: Paths) -> dict[str, Any]:
    """What the console shows after an upload, in plain language."""
    report: dict[str, Any] = {
        "status": result.status,
        "dataset_id": result.dataset_id,
        "message": result.message,
        "problems": result.problems,
        "rows_loaded": result.rows_loaded,
        "profile": None,
        "gate": None,
        "quarantine_path": None,
    }
    if result.profile:
        report["profile"] = {
            "row_count": result.profile.row_count,
            "column_count": result.profile.column_count,
            "encoding": result.profile.encoding,
            "delimiter": result.profile.delimiter,
            "warnings": result.profile.warnings,
        }
    if result.outcome:
        report["gate"] = {
            "passed": result.outcome.passed,
            "hard_failures": len(result.outcome.hard_failures),
            "warnings": len(result.outcome.warnings),
            "rules": [
                {
                    "rule_id": item.rule.rule_id,
                    "column": item.rule.column,
                    "check": item.rule.check,
                    "severity": item.rule.severity,
                    "why": item.rule.why,
                    "success": item.success,
                    "detail": item.detail,
                }
                for item in result.outcome.results
            ],
        }
    if result.status == "quarantined" and result.dataset_id:
        report["quarantine_path"] = _relative(paths.quarantine_root / result.dataset_id)
    return report


def register_dataset_routes(
    app: FastAPI,
    database: Path,
    llm_factory: Callable[[], Any] | None,
    paths: Paths | None = None,
) -> None:
    env_paths = Paths.from_env()
    paths = paths or Paths(database, env_paths.data_root, env_paths.quarantine_root)

    def mapping_of(dataset_id: str, mapping_id: str) -> dict[str, Any]:
        _check_dataset_id(dataset_id)
        try:
            record = mapping_store.get_mapping(database, mapping_id)
        except mapping_store.DecisionError as error:
            raise _decision_error(error) from error
        if record["dataset_id"] != dataset_id:
            raise HTTPException(status_code=404, detail="No such mapping for this dataset.")
        return record

    @app.get("/api/datasets")
    def datasets() -> dict[str, Any]:
        return {"items": _plain(registry.list_datasets(database))}

    @app.post("/api/datasets/upload")
    async def upload(file: UploadFile = File(...), name: str | None = Form(None)) -> dict[str, Any]:
        original = Path(file.filename or "").name
        if not original.lower().endswith(".csv"):
            raise HTTPException(status_code=400, detail="Only .csv files can be uploaded.")
        limit = upload_limit_bytes()
        incoming = paths.data_root / "incoming"
        incoming.mkdir(parents=True, exist_ok=True)
        # A generated name: nothing the user sends becomes part of a path.
        target = incoming / f"{uuid.uuid4().hex}.csv"
        try:
            size = 0
            with target.open("wb") as handle:
                while chunk := await file.read(CHUNK_BYTES):
                    size += len(chunk)
                    if size > limit:
                        raise HTTPException(
                            status_code=413,
                            detail=f"The file is larger than the {limit // (1024 * 1024)} MB "
                            "upload limit; nothing was stored.",
                        )
                    handle.write(chunk)
            if size == 0:
                raise HTTPException(status_code=400, detail="The file is empty.")
            display = (name or "").strip() or Path(original).stem or "Uploaded dataset"
            result = await run_in_threadpool(onboard, target, display, paths=paths, max_bytes=limit)
        finally:
            target.unlink(missing_ok=True)  # bronze or quarantine keeps its own copy
        return _plain(onboarding_report(result, paths))

    @app.get("/api/datasets/{dataset_id}")
    def dataset(dataset_id: str) -> dict[str, Any]:
        _check_dataset_id(dataset_id)
        found = registry.get_dataset(database, dataset_id)
        if found is None:
            raise HTTPException(status_code=404, detail="No such dataset.")
        listed = next(
            (d for d in registry.list_datasets(database) if d["dataset_id"] == dataset_id), {}
        )
        found["display_status"] = listed.get("display_status", found["status"])
        return _plain(found)

    @app.get("/api/datasets/{dataset_id}/mapping")
    def mapping(dataset_id: str) -> dict[str, Any]:
        _check_dataset_id(dataset_id)
        history = mapping_store.list_mappings(database, dataset_id)
        current = next((m for m in history if m["status"] == "approved"), None) or next(
            (m for m in reversed(history) if m["status"] == "proposed"), None
        )
        return _plain({"current": current, "history": history})

    @app.post("/api/datasets/{dataset_id}/mapping/propose")
    def propose(dataset_id: str, body: ProposeBody) -> dict[str, Any]:
        by = _require_by(body.by)
        _check_dataset_id(dataset_id)
        if body.mode not in MODES:
            raise HTTPException(status_code=400, detail=f"mode must be one of {MODES}")
        try:
            context = load_context(database, dataset_id)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except MappingInvalid as error:
            raise HTTPException(status_code=400, detail="; ".join(error.errors)) from error
        proposal = propose_mapping(
            context,
            llm_factory=None if body.heuristic_only else llm_factory,
            heuristic_only=body.heuristic_only,
            mode=body.mode,
        )
        mapping_id = mapping_store.record_proposal(database, dataset_id, proposal, created_by=by)
        record = mapping_store.get_mapping(database, mapping_id)
        return _plain(
            {
                **record,
                "decided_by": proposal.decided_by,
                "escalation_reasons": proposal.escalation_reasons,
            }
        )

    @app.post("/api/datasets/{dataset_id}/mapping/{mapping_id}/approve")
    def approve(dataset_id: str, mapping_id: str, body: MappingDecision) -> dict[str, Any]:
        by = _require_by(body.by)
        mapping_of(dataset_id, mapping_id)
        try:
            return mapping_store.approve(database, mapping_id, by=by, reason=body.reason)
        except mapping_store.DecisionError as error:
            raise _decision_error(error) from error

    @app.post("/api/datasets/{dataset_id}/mapping/{mapping_id}/reject")
    def reject(dataset_id: str, mapping_id: str, body: MappingDecision) -> dict[str, Any]:
        by = _require_by(body.by)
        mapping_of(dataset_id, mapping_id)
        try:
            return mapping_store.reject(database, mapping_id, by=by, reason=body.reason or "")
        except mapping_store.DecisionError as error:
            raise _decision_error(error) from error

    @app.post("/api/datasets/{dataset_id}/mapping/{mapping_id}/edit")
    def edit(dataset_id: str, mapping_id: str, body: MappingEdit) -> dict[str, Any]:
        by = _require_by(body.by)
        mapping_of(dataset_id, mapping_id)
        if not body.set:
            raise HTTPException(status_code=400, detail="Give at least one field to set.")
        sets = {
            path: None if value in (None, "", "null") else value for path, value in body.set.items()
        }
        try:
            new_id, _ = mapping_store.edit(database, mapping_id, sets, by=by)
        except MappingInvalid as error:
            raise HTTPException(status_code=400, detail="; ".join(error.errors)) from error
        except mapping_store.DecisionError as error:
            raise _decision_error(error) from error
        return _plain(mapping_store.get_mapping(database, new_id))

    @app.post("/api/datasets/{dataset_id}/run")
    def run(dataset_id: str) -> Any:
        _check_dataset_id(dataset_id)
        busy = runs.busy_reason(database, paths.data_root)
        if busy:
            return JSONResponse(
                status_code=409,
                content={
                    "code": "busy",
                    "detail": f"Another writer is running ({busy}). Only one run can write "
                    "to the warehouse at a time; try again when it finishes.",
                },
            )
        try:
            approved_mapping(database, dataset_id)
        except ScoringRefused as error:
            status = 404 if str(error).startswith("Unknown dataset") else 400
            raise HTTPException(status_code=status, detail=str(error)) from error
        run_id = runs.start_run(database, paths.data_root, dataset_id)
        return {"run_id": run_id, "dataset_id": dataset_id, "status": "queued"}

    @app.get("/api/runs/{run_id}")
    def run_status(run_id: str) -> dict[str, Any]:
        try:
            state = runs.run_state(database, paths.data_root, run_id)
        except ValueError:
            state = None  # not a run id we generated
        if state is None:
            raise HTTPException(status_code=404, detail="No such run.")
        return _plain(state)

    @app.get("/api/datasets/{dataset_id}/results")
    def results(dataset_id: str) -> dict[str, Any]:
        _check_dataset_id(dataset_id)
        with connect_read_only(database) as connection:
            found = dataset_queries.results(connection, dataset_id)
        if found is None:
            raise HTTPException(status_code=404, detail="This dataset has not been scored yet.")
        return _plain(found)
