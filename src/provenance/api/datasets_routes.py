"""Dataset and column-mapping endpoints (Phase 10b).

Reads are read-only. Writes go only through provenance.datasets.mapping_store,
which writes through registry.writer(); this module holds no SQL. Every write
needs a reviewer name (`by`); a lock returns 409 via the app's handler, and an
invalid edit returns 400 with the validator's messages.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from provenance.datasets import mapping_store, registry
from provenance.datasets.identifiers import DATASET_ID_PATTERN
from provenance.datasets.mapping_agent import propose_mapping
from provenance.datasets.validator import MappingInvalid, load_context


class ProposeBody(BaseModel):
    by: str = ""
    heuristic_only: bool = False


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


def register_dataset_routes(
    app: FastAPI, database: Path, llm_factory: Callable[[], Any] | None
) -> None:
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

    @app.get("/api/datasets/{dataset_id}")
    def dataset(dataset_id: str) -> dict[str, Any]:
        _check_dataset_id(dataset_id)
        found = registry.get_dataset(database, dataset_id)
        if found is None:
            raise HTTPException(status_code=404, detail="No such dataset.")
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
        )
        mapping_id = mapping_store.record_proposal(database, dataset_id, proposal, created_by=by)
        return _plain(mapping_store.get_mapping(database, mapping_id))

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
