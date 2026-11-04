"""The standard shipment contract a dataset is mapped onto.

Required: entity (who is scored: supplier, vendor or category), event_date (when
the shipment happened) and an outcome (whether it was late). Optional: country,
and cancelled ({col, value}: cancelled rows count as shipments but are left out of
late rate and delay, as the flagship does with DataCo's 'Shipping canceled').

An outcome takes one of three shapes:
- late_flag:   one boolean-like column; late_when says which value means late,
               so `on_time` maps with late_when="false"
- days_pair:   scheduled_col and actual_col, in days or hours (late when
               actual > scheduled)
- delay_value: one numeric delay column, in days or hours (late when > 0)

A mapping is plain JSON:
    {"entity": col, "event_date": {"col": col, "format": strftime},
     "outcome": {"kind": ..., ...}, "country": col | null,
     "evidence": {field: [profile facts]}, "confidence": 0-1, "notes": text}

Two verdicts come from it: mappable (every required field mapped and valid) and
scoreable (mappable, at least 3 eligible quarters, and most entities with about
50 rows per quarter), each with a plain-language reason.
"""

from __future__ import annotations

import copy
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

REQUIRED_FIELDS = ("entity", "event_date", "outcome")
OPTIONAL_FIELDS = ("country", "cancelled")
OUTCOME_KINDS = ("late_flag", "days_pair", "delay_value")
UNITS = ("days", "hours")
POLARITIES = ("true", "false")
OUTCOME_COLUMNS = {
    "late_flag": ("col",),
    "days_pair": ("scheduled_col", "actual_col"),
    "delay_value": ("col",),
}
MIN_ELIGIBLE_QUARTERS = 3
MIN_ROWS_PER_ENTITY_QUARTER = 50
MOST_ENTITIES = 0.5

CONTRACT_DESCRIPTION: dict[str, Any] = {
    "required": {
        "entity": "the column naming who is scored: supplier, vendor or product category",
        "event_date": (
            "the date the shipment was ordered or created, with its strftime format; "
            "when both exist, prefer the order or creation date over ship or delivery dates"
        ),
        "outcome": "how lateness is recorded, as exactly one of the outcome kinds",
    },
    "optional": {
        "country": "destination or origin country column, or null",
        "cancelled": (
            "null, or {'col': column, 'value': text}: rows whose column equals the value "
            "were cancelled; they count as shipments but not toward lateness or delay"
        ),
    },
    "outcome_kinds": {
        "late_flag": {
            "fields": {"col": "boolean-like column", "late_when": "'true' or 'false'"},
            "meaning": "late_when is the value that means late; on_time maps with 'false'",
        },
        "days_pair": {
            "fields": {
                "scheduled_col": "numeric planned duration",
                "actual_col": "numeric actual duration",
                "unit": "'days' or 'hours'",
            },
            "meaning": "late when actual is greater than scheduled",
        },
        "delay_value": {
            "fields": {"col": "numeric delay", "unit": "'days' or 'hours'"},
            "meaning": "late when the delay is greater than 0",
        },
    },
    "mapping_json": {
        "entity": "column name",
        "event_date": {"col": "column name", "format": "strftime, e.g. %Y-%m-%d"},
        "outcome": {"kind": "late_flag | days_pair | delay_value", "...": "kind fields"},
        "country": "column name or null",
        "evidence": {"field": ["facts copied from the tools, e.g. 'distinct_count=12'"]},
        "confidence": "0 to 1",
        "notes": "short explanation; say which required field cannot be mapped, if any",
    },
}


@dataclass(frozen=True)
class Verdict:
    ok: bool
    reason: str


def normalize(mapping: Mapping[str, Any]) -> dict[str, Any]:
    """A copy with canonical shapes: polarity and units as lowercase strings."""
    result = copy.deepcopy(dict(mapping))
    outcome = result.get("outcome")
    if isinstance(outcome, dict):
        if isinstance(outcome.get("late_when"), bool):
            outcome["late_when"] = "true" if outcome["late_when"] else "false"
        for key in ("late_when", "unit", "kind"):
            if isinstance(outcome.get(key), str):
                outcome[key] = outcome[key].strip().lower()
    if result.get("country") in ("", "null", "none"):
        result["country"] = None
    result.setdefault("country", None)
    cancelled = result.get("cancelled")
    if cancelled in ("", "null", "none") or (
        isinstance(cancelled, dict) and cancelled.get("col") in (None, "", "null")
    ):
        result["cancelled"] = None
    result.setdefault("cancelled", None)
    result.setdefault("evidence", {})
    result.setdefault("notes", "")
    return result


def used_columns(mapping: Mapping[str, Any]) -> list[tuple[str, str]]:
    """(role, column) for every column a mapping references."""
    used = []
    if isinstance(mapping.get("entity"), str):
        used.append(("entity", mapping["entity"]))
    event_date = mapping.get("event_date")
    if isinstance(event_date, Mapping) and isinstance(event_date.get("col"), str):
        used.append(("event_date", event_date["col"]))
    outcome = mapping.get("outcome")
    if isinstance(outcome, Mapping):
        for key in OUTCOME_COLUMNS.get(outcome.get("kind"), ()):
            if isinstance(outcome.get(key), str):
                used.append((f"outcome.{key}", outcome[key]))
    if isinstance(mapping.get("country"), str):
        used.append(("country", mapping["country"]))
    cancelled = mapping.get("cancelled")
    if isinstance(cancelled, Mapping) and isinstance(cancelled.get("col"), str):
        used.append(("cancelled", cancelled["col"]))
    return used


def scoreable_verdict(counts: Mapping[tuple[str, str], int]) -> Verdict:
    """Scoreable from row counts per (entity, quarter).

    A quarter is eligible when at least one entity has 50 or more rows in it;
    at least 3 are needed, and more than half of the entities must reach 50
    rows in their typical (median) active quarter.
    """
    if not counts:
        return Verdict(False, "no rows with a valid entity and date to count")
    per_quarter: dict[str, list[int]] = defaultdict(list)
    per_entity: dict[str, list[int]] = defaultdict(list)
    for (entity, quarter), rows in counts.items():
        per_quarter[quarter].append(rows)
        per_entity[entity].append(rows)
    eligible = sorted(
        quarter for quarter, rows in per_quarter.items() if max(rows) >= MIN_ROWS_PER_ENTITY_QUARTER
    )
    enough = [
        entity
        for entity, rows in per_entity.items()
        if sorted(rows)[len(rows) // 2] >= MIN_ROWS_PER_ENTITY_QUARTER
    ]
    share = len(enough) / len(per_entity)
    if len(eligible) < MIN_ELIGIBLE_QUARTERS:
        return Verdict(
            False,
            f"only {len(eligible)} quarter(s) have an entity with at least "
            f"{MIN_ROWS_PER_ENTITY_QUARTER} rows; scoring needs {MIN_ELIGIBLE_QUARTERS}",
        )
    if share <= MOST_ENTITIES:
        return Verdict(
            False,
            f"only {len(enough)} of {len(per_entity)} entities have about "
            f"{MIN_ROWS_PER_ENTITY_QUARTER} rows per quarter; most need to",
        )
    return Verdict(
        True,
        f"{len(eligible)} eligible quarters and {len(enough)} of {len(per_entity)} entities "
        f"with at least {MIN_ROWS_PER_ENTITY_QUARTER} rows per quarter",
    )
