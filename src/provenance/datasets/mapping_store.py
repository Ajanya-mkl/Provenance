"""Mapping proposals, human edits and decisions (writes via registry.writer).

A proposal is stored as a new version with status 'proposed'; a newer proposal
or a human edit supersedes it (never overwrites). An edit is re-validated
before anything is written. Only an approved mapping moves the dataset to
'mapped', and approval needs a reviewer name and a mappable mapping. Every
propose, edit, approve and reject appends to the hash-chained audit log.
"""

from __future__ import annotations

import copy
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from provenance.agent.store import append_audit, ensure_agent_tables
from provenance.datasets import contract, registry
from provenance.datasets.mapping_agent import MappingProposal, column_view
from provenance.datasets.validator import (
    DatasetContext,
    MappingInvalid,
    ValidationResult,
    load_context,
    validate,
)
from provenance.db import connect_read_only

EDITABLE_PATHS = (
    "entity",
    "country",
    "notes",
    "event_date.col",
    "event_date.format",
    "outcome.kind",
    "outcome.col",
    "outcome.scheduled_col",
    "outcome.actual_col",
    "outcome.unit",
    "outcome.late_when",
)
OUTCOME_KEYS = {
    "late_flag": ("kind", "col", "late_when"),
    "days_pair": ("kind", "scheduled_col", "actual_col", "unit"),
    "delay_value": ("kind", "col", "unit"),
}
MAX_NAME_CHARS = 64
MAX_REASON_CHARS = 500


class DecisionError(ValueError):
    """A decision the workflow refuses (unknown id, wrong status, missing name)."""


def _now() -> datetime:
    return datetime.now(UTC)


def _reviewer(name: Any) -> str:
    text = str(name or "").strip()
    if not text or len(text) > MAX_NAME_CHARS:
        raise DecisionError(f"A reviewer name (by) of 1-{MAX_NAME_CHARS} characters is required.")
    return text


def profile_facts(context: DatasetContext) -> list[dict[str, Any]]:
    """The column views a mapper (agent or human) could cite as evidence."""
    return [
        {"name": "column_profiles", "result": [column_view(context, c) for c in context.columns]}
    ]


def _row(connection: duckdb.DuckDBPyConnection, mapping_id: str) -> dict[str, Any]:
    cursor = connection.execute("SELECT * FROM meta.mappings WHERE mapping_id = ?", [mapping_id])
    row = cursor.fetchone()
    if row is None:
        raise DecisionError(f"No mapping {mapping_id!r}.")
    record = dict(zip([d[0] for d in cursor.description], row, strict=True))
    for key in ("proposal", "reasons", "validation"):
        if isinstance(record[key], str):
            record[key] = json.loads(record[key])
    return record


def _insert(
    connection: duckdb.DuckDBPyConnection,
    *,
    dataset_id: str,
    mapping: dict[str, Any],
    source: str,
    validation: ValidationResult,
    created_by: str,
    extra_validation: dict[str, Any] | None = None,
    tokens: tuple[int, int] = (0, 0),
) -> tuple[str, int]:
    version = connection.execute(
        "SELECT coalesce(max(version), 0) + 1 FROM meta.mappings WHERE dataset_id = ?",
        [dataset_id],
    ).fetchone()[0]
    mapping_id = str(uuid.uuid4())
    connection.execute(
        """
        INSERT INTO meta.mappings
            (mapping_id, dataset_id, version, proposal, source, status, mappable, scoreable,
             reasons, validation, created_by, created_at, prompt_tokens, completion_tokens)
        VALUES (?, ?, ?, ?::JSON, ?, 'proposed', ?, ?, ?::JSON, ?::JSON, ?, ?, ?, ?)
        """,
        [
            mapping_id,
            dataset_id,
            version,
            json.dumps(mapping, default=str),
            source,
            validation.mappable.ok,
            validation.scoreable.ok,
            json.dumps(
                {"mappable": validation.mappable.reason, "scoreable": validation.scoreable.reason}
            ),
            json.dumps({**validation.as_dict(), **(extra_validation or {})}, default=str),
            created_by,
            _now(),
            tokens[0],
            tokens[1],
        ],
    )
    return mapping_id, version


def _supersede(
    connection: duckdb.DuckDBPyConnection, dataset_id: str, status: str, by_id: str
) -> list[str]:
    ids = [
        row[0]
        for row in connection.execute(
            "SELECT mapping_id FROM meta.mappings WHERE dataset_id = ? AND status = ? "
            "AND mapping_id <> ?",
            [dataset_id, status, by_id],
        ).fetchall()
    ]
    for mapping_id in ids:
        connection.execute(
            "UPDATE meta.mappings SET status = 'superseded', superseded_by = ? "
            "WHERE mapping_id = ?",
            [by_id, mapping_id],
        )
    return ids


def record_proposal(
    db_path: str | Path, dataset_id: str, proposal: MappingProposal, *, created_by: str
) -> str:
    """Store a new proposed version; earlier open proposals become superseded."""
    with registry.writer(db_path) as connection:
        ensure_agent_tables(connection)
        mapping_id, version = _insert(
            connection,
            dataset_id=dataset_id,
            mapping=proposal.mapping,
            source=proposal.source,
            validation=proposal.validation,
            created_by=created_by,
            extra_validation={
                "fallback_reason": proposal.fallback_reason,
                "agent_attempt_errors": [attempt["errors"] for attempt in proposal.attempts],
            },
            tokens=(proposal.prompt_tokens, proposal.completion_tokens),
        )
        superseded = _supersede(connection, dataset_id, "proposed", mapping_id)
        append_audit(
            connection,
            created_by,
            "mapping_proposed",
            {
                "dataset_id": dataset_id,
                "mapping_id": mapping_id,
                "version": version,
                "source": proposal.source,
                "mappable": proposal.validation.mappable.ok,
                "scoreable": proposal.validation.scoreable.ok,
                "fallback_reason": proposal.fallback_reason,
                "superseded": superseded,
            },
        )
    return mapping_id


def parse_sets(pairs: list[str]) -> dict[str, Any]:
    """'outcome.late_when=false' style edits; 'null' clears an optional field."""
    sets: dict[str, Any] = {}
    for pair in pairs:
        if "=" not in pair:
            raise MappingInvalid([f"An edit must look like field=value, not {pair!r}."])
        path, value = (part.strip() for part in pair.split("=", 1))
        sets[path] = None if value.lower() in ("null", "none", "") else value
    return sets


def apply_edits(mapping: dict[str, Any], sets: dict[str, Any]) -> dict[str, Any]:
    edited = copy.deepcopy(mapping)
    for path, value in sets.items():
        if path not in EDITABLE_PATHS:
            raise MappingInvalid(
                [f"{path!r} cannot be edited; editable fields: {', '.join(EDITABLE_PATHS)}."]
            )
        root, _, leaf = path.partition(".")
        if leaf:
            if not isinstance(edited.get(root), dict):
                edited[root] = {}
            edited[root][leaf] = value
        else:
            edited[root] = value
        # A field the human changed no longer has the mapper's evidence behind it.
        if isinstance(edited.get("evidence"), dict):
            edited["evidence"].pop(root, None)
    outcome = edited.get("outcome")
    if isinstance(outcome, dict) and outcome.get("kind") in OUTCOME_KEYS:
        edited["outcome"] = {k: v for k, v in outcome.items() if k in OUTCOME_KEYS[outcome["kind"]]}
    return edited


def edit(
    db_path: str | Path, mapping_id: str, sets: dict[str, Any], *, by: str
) -> tuple[str, ValidationResult]:
    """A human edit: re-validated, stored as a new version, the old one superseded."""
    reviewer = _reviewer(by)
    with connect_read_only(db_path) as connection:
        old = _row(connection, mapping_id)
    if old["status"] != "proposed":
        raise DecisionError(
            f"Mapping {mapping_id} is {old['status']}; only a proposal can be edited."
        )
    context = load_context(db_path, old["dataset_id"])
    edited = apply_edits(old["proposal"], sets)
    edited["notes"] = f"edited by {reviewer}: " + ", ".join(f"{k}={v}" for k, v in sets.items())
    validation = validate(edited, context, extra_facts=profile_facts(context))
    if not validation.ok:
        raise MappingInvalid(validation.errors)
    with registry.writer(db_path) as connection:
        ensure_agent_tables(connection)
        new_id, version = _insert(
            connection,
            dataset_id=old["dataset_id"],
            mapping=validation.mapping,
            source="human",
            validation=validation,
            created_by=reviewer,
        )
        connection.execute(
            "UPDATE meta.mappings SET status = 'superseded', superseded_by = ? "
            "WHERE mapping_id = ? AND status = 'proposed'",
            [new_id, mapping_id],
        )
        append_audit(
            connection,
            reviewer,
            "mapping_edited",
            {
                "dataset_id": old["dataset_id"],
                "mapping_id": new_id,
                "version": version,
                "supersedes": mapping_id,
                "changes": sets,
            },
        )
    return new_id, validation


def approve(db_path: str | Path, mapping_id: str, *, by: str, reason: str | None = None) -> dict:
    reviewer = _reviewer(by)
    with registry.writer(db_path) as connection:
        ensure_agent_tables(connection)
        record = _row(connection, mapping_id)
        if record["status"] != "proposed":
            raise DecisionError(f"Mapping {mapping_id} is {record['status']}, not proposed.")
        if not record["mappable"]:
            raise DecisionError(
                f"Mapping {mapping_id} is not mappable ({record['reasons']['mappable']}); "
                "edit it first."
            )
        connection.execute(
            "UPDATE meta.mappings SET status = 'approved', decided_by = ?, reason = ?, "
            "decided_at = ? WHERE mapping_id = ?",
            [reviewer, (reason or "")[:MAX_REASON_CHARS] or None, _now(), mapping_id],
        )
        previous = _supersede(connection, record["dataset_id"], "approved", mapping_id)
        registry.set_status(connection, record["dataset_id"], "mapped")
        append_audit(
            connection,
            reviewer,
            "mapping_approved",
            {
                "dataset_id": record["dataset_id"],
                "mapping_id": mapping_id,
                "version": record["version"],
                "scoreable": record["scoreable"],
                "reason": reason,
                "superseded": previous,
            },
        )
    return {"mapping_id": mapping_id, "status": "approved", "dataset_status": "mapped"}


def reject(db_path: str | Path, mapping_id: str, *, by: str, reason: str) -> dict:
    reviewer = _reviewer(by)
    text = str(reason or "").strip()[:MAX_REASON_CHARS]
    if not text:
        raise DecisionError("A reason is required to reject a mapping.")
    with registry.writer(db_path) as connection:
        ensure_agent_tables(connection)
        record = _row(connection, mapping_id)
        if record["status"] != "proposed":
            raise DecisionError(f"Mapping {mapping_id} is {record['status']}, not proposed.")
        connection.execute(
            "UPDATE meta.mappings SET status = 'rejected', decided_by = ?, reason = ?, "
            "decided_at = ? WHERE mapping_id = ?",
            [reviewer, text, _now(), mapping_id],
        )
        append_audit(
            connection,
            reviewer,
            "mapping_rejected",
            {"dataset_id": record["dataset_id"], "mapping_id": mapping_id, "reason": text},
        )
    return {"mapping_id": mapping_id, "status": "rejected"}


def list_mappings(db_path: str | Path, dataset_id: str) -> list[dict[str, Any]]:
    if not Path(db_path).exists():
        return []
    with connect_read_only(db_path) as connection:
        exists = connection.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'meta' AND table_name = 'mappings'"
        ).fetchone()
        if not exists:
            return []
        ids = connection.execute(
            "SELECT mapping_id FROM meta.mappings WHERE dataset_id = ? ORDER BY version",
            [dataset_id],
        ).fetchall()
        return [_row(connection, row[0]) for row in ids]


def get_mapping(db_path: str | Path, mapping_id: str) -> dict[str, Any]:
    with connect_read_only(db_path) as connection:
        return _row(connection, mapping_id)


def required_fields_summary(mapping: dict[str, Any]) -> dict[str, str | None]:
    """field -> column(s), for display."""
    outcome = mapping.get("outcome") or {}
    kind = outcome.get("kind")
    cols = [outcome.get(key) for key in contract.OUTCOME_COLUMNS.get(kind, ())]
    detail = outcome.get("late_when") or outcome.get("unit")
    return {
        "entity": mapping.get("entity"),
        "event_date": (mapping.get("event_date") or {}).get("col"),
        "date_format": (mapping.get("event_date") or {}).get("format"),
        "outcome": f"{kind}: {' / '.join(str(c) for c in cols)} ({detail})" if kind else None,
        "country": mapping.get("country"),
    }
