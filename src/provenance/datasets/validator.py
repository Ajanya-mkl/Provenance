"""Deterministic validation of a column mapping (no LLM).

Checks, against the dataset's profile and its bronze values (read-only):
- the mapping has the contract's shape and every required field;
- every column exists, and no column is used for two roles;
- types fit roles: entity is text or whole numbers, the date format parses at
  least 95% of the date column, outcome columns are boolean-like or numeric,
  country is text;
- a late flag's polarity agrees with the column name (on_time means
  late_when=false) and, when the dataset also has a delay or a scheduled/actual
  pair, with the values themselves;
- every number quoted in `evidence` appears in the profile facts (the same
  grounding check the investigation agent uses).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from provenance.agent.grounding import check_grounding
from provenance.datasets import contract, registry
from provenance.datasets.identifiers import quote_column, quote_schema
from provenance.datasets.profile import normalized
from provenance.db import connect_read_only

DATE_PARSE_MIN = 0.95
TRUTHY = frozenset(
    {"true", "yes", "t", "y", "1", "si", "sí", "ja", "oui", "verdadero", "vrai", "wahr"}
)
FALSY = frozenset({"false", "no", "f", "n", "0", "nein", "non", "falso", "faux", "falsch"})
ON_TIME_WORDS = (
    "on_time",
    "ontime",
    "a_tiempo",
    "puntual",
    "punctual",
    "puenktlich",
    "punktlich",
    "rechtzeitig",
    "a_l_heure",
    "in_time",
    "a_temps",
)
LATE_WORDS = (
    "late",
    "delayed",
    "delay",
    "retraso",
    "retrasado",
    "atrasado",
    "tarde",
    "tardio",
    "verspatet",
    "verspaetet",
    "verzug",
    "verzoegerung",
    "verzogerung",
    "retard",
)
SCHEDULED_WORDS = (
    "scheduled",
    "planned",
    "plan",
    "expected",
    "promised",
    "estimated",
    "programado",
    "previsto",
    "geplant",
    "prevu",
    "sched",
)
ACTUAL_WORDS = ("actual", "real", "delivered", "effective", "tatsachlich", "reel", "realized")
POLARITY_MARGIN = 0.05
MIN_GROUP_ROWS = 5


class MappingInvalid(ValueError):
    """A mapping the validator refused; `errors` lists why."""

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def name_tokens(name: str) -> str:
    """Lowercase ASCII with words joined by '_' (accents removed)."""
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    return "_" + re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") + "_"


def has_word(name: str, words: tuple[str, ...]) -> bool:
    """Whole-word match on the token form: 'is_late' has 'late', 'lateral' does not."""
    tokens = name_tokens(name)
    return any(f"_{word}_" in tokens for word in words)


@dataclass
class DatasetContext:
    dataset_id: str
    name: str
    status: str
    row_count: int
    columns: dict[str, dict[str, Any]]
    frame: pd.DataFrame

    def facts(self) -> dict[str, Any]:
        """Every number the mapper may quote: the profile, as the tools show it."""
        facts: dict[str, Any] = {"row_count": self.row_count, "column_count": len(self.columns)}
        for name, column in self.columns.items():
            share = column.get("type_share")
            facts[name] = {
                key: column.get(key)
                for key in ("null_pct", "distinct_count", "min_value", "max_value")
                if column.get(key) is not None
                and (key not in ("min_value", "max_value") or column["inferred_type"] != "string")
            }
            if share is not None:
                facts[name]["type_share"] = share
                facts[name]["type_share_pct"] = round(100 * share, 2)
        return facts


def load_context(db_path: str | Path, dataset_id: str) -> DatasetContext:
    dataset = registry.get_dataset(db_path, dataset_id)
    if dataset is None:
        raise LookupError(f"Unknown dataset {dataset_id!r}")
    if dataset["status"] not in ("gated", "mapped"):
        raise MappingInvalid(
            [f"Dataset {dataset_id} is {dataset['status']}; only a gated dataset can be mapped."]
        )
    columns = {column["column_name"]: column for column in dataset["columns"]}
    names = list(columns)
    select = ", ".join(quote_column(name, names) for name in names)
    with connect_read_only(db_path) as connection:
        raw = connection.execute(
            f"SELECT {select} FROM {quote_schema(dataset['schema_name'])}.bronze_raw"
        ).fetchdf()
    raw.columns = names
    frame = pd.DataFrame({name: normalized(raw[name]) for name in names})
    return DatasetContext(
        dataset_id=dataset_id,
        name=dataset["name"],
        status=dataset["status"],
        row_count=int(dataset["row_count"] or len(frame)),
        columns=columns,
        frame=frame,
    )


@dataclass
class ValidationResult:
    mapping: dict[str, Any]
    errors: list[str] = field(default_factory=list)
    unmapped: list[str] = field(default_factory=list)  # required fields left null on purpose
    mappable: contract.Verdict = contract.Verdict(False, "not validated")
    scoreable: contract.Verdict = contract.Verdict(False, "not mappable")

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": self.errors,
            "unmapped": self.unmapped,
            "mappable": {"ok": self.mappable.ok, "reason": self.mappable.reason},
            "scoreable": {"ok": self.scoreable.ok, "reason": self.scoreable.reason},
        }


def _bool_like(values: pd.Series) -> bool:
    distinct = {str(value).lower() for value in values.dropna().unique()}
    return 0 < len(distinct) <= 2 and distinct <= (TRUTHY | FALSY)


def _reference_delay(context: DatasetContext, skip: set[str]) -> pd.Series | None:
    """A numeric lateness signal elsewhere in the dataset, to check polarity against."""
    numeric = [
        name
        for name, column in context.columns.items()
        if column["inferred_type"] in ("int", "float") and name not in skip
    ]
    for name in numeric:
        if has_word(name, LATE_WORDS):
            return pd.to_numeric(context.frame[name], errors="coerce")
    scheduled = next((n for n in numeric if has_word(n, SCHEDULED_WORDS)), None)
    actual = next((n for n in numeric if has_word(n, ACTUAL_WORDS)), None)
    if scheduled and actual:
        return pd.to_numeric(context.frame[actual], errors="coerce") - pd.to_numeric(
            context.frame[scheduled], errors="coerce"
        )
    return None


def _check_polarity(context: DatasetContext, column: str, late_when: str, errors: list[str]):
    if has_word(column, ON_TIME_WORDS) and late_when == "true":
        errors.append(
            f"Polarity: {column!r} names on-time shipments, so late_when must be 'false'."
        )
        return
    if (
        has_word(column, LATE_WORDS)
        and not has_word(column, ON_TIME_WORDS)
        and late_when == "false"
    ):
        errors.append(f"Polarity: {column!r} names late shipments, so late_when must be 'true'.")
        return
    delay = _reference_delay(context, skip={column})
    if delay is None:
        return
    flag = context.frame[column].str.lower()
    truthy = flag.isin(TRUTHY)
    late = truthy if late_when == "true" else flag.isin(FALSY)
    known = flag.notna() & delay.notna()
    delayed, punctual = late[known & (delay > 0)], late[known & (delay <= 0)]
    if len(delayed) >= MIN_GROUP_ROWS and len(punctual) >= MIN_GROUP_ROWS:
        rate_delayed, rate_punctual = float(delayed.mean()), float(punctual.mean())
        if rate_delayed + POLARITY_MARGIN < rate_punctual:
            errors.append(
                f"Polarity: with late_when={late_when!r}, {round(100 * rate_delayed, 1)}% of "
                f"delayed shipments count as late but {round(100 * rate_punctual, 1)}% of "
                "punctual ones do; the values say the polarity is the other way round."
            )


def _quarter_counts(context: DatasetContext, mapping: Mapping[str, Any]) -> dict:
    entity = context.frame[mapping["entity"]]
    dates = pd.to_datetime(
        context.frame[mapping["event_date"]["col"]],
        format=mapping["event_date"]["format"],
        errors="coerce",
    )
    keep = entity.notna() & dates.notna()
    quarters = dates[keep].dt.to_period("Q").astype(str)
    counts = pd.DataFrame({"entity": entity[keep], "quarter": quarters}).value_counts()
    return {(str(entity), str(quarter)): int(rows) for (entity, quarter), rows in counts.items()}


def validate(
    raw_mapping: Mapping[str, Any],
    context: DatasetContext,
    extra_facts: list[dict[str, Any]] | None = None,
) -> ValidationResult:
    """Validate a mapping; `extra_facts` are tool results the mapper saw (grounding)."""
    if not isinstance(raw_mapping, Mapping):
        result = ValidationResult(mapping={}, errors=["The mapping must be a JSON object."])
        result.mappable = contract.Verdict(False, result.errors[0])
        return result
    mapping = contract.normalize(raw_mapping)
    # late_when may be given as the column's own value ("no" means late when false).
    outcome_in = mapping.get("outcome")
    if isinstance(outcome_in, dict) and isinstance(outcome_in.get("late_when"), str):
        if outcome_in["late_when"] in TRUTHY:
            outcome_in["late_when"] = "true"
        elif outcome_in["late_when"] in FALSY:
            outcome_in["late_when"] = "false"
    if isinstance(mapping.get("confidence"), str):
        try:
            mapping["confidence"] = float(mapping["confidence"])
        except ValueError:
            pass
    result = ValidationResult(mapping=mapping)
    errors = result.errors
    columns = context.columns

    # Shape and required fields. A required field set to null is a valid answer
    # ("no column fits"); it makes the mapping not mappable rather than invalid.
    unmapped = result.unmapped
    if mapping.get("entity") is None:
        unmapped.append("entity")
    elif not isinstance(mapping["entity"], str) or not mapping["entity"]:
        errors.append("Required field 'entity' must be a column name or null.")
    event_date = mapping.get("event_date")
    if event_date is None or (isinstance(event_date, Mapping) and event_date.get("col") is None):
        unmapped.append("event_date")
        event_date = None
    elif not isinstance(event_date, Mapping) or not isinstance(event_date.get("col"), str):
        errors.append("Required field 'event_date' needs {'col': column, 'format': strftime}.")
        event_date = None
    elif not isinstance(event_date.get("format"), str) or "%" not in event_date["format"]:
        errors.append(
            f"event_date.format must be a strftime format, not {event_date.get('format')!r}."
        )
    outcome = mapping.get("outcome")
    kind = outcome.get("kind") if isinstance(outcome, Mapping) else None
    if outcome is None or (isinstance(outcome, Mapping) and kind is None and not outcome):
        unmapped.append("outcome")
        outcome = None
    elif kind not in contract.OUTCOME_KINDS:
        errors.append(
            f"Required field 'outcome' needs kind one of {list(contract.OUTCOME_KINDS)}, "
            f"not {kind!r}."
        )
        outcome = None
    else:
        missing = [
            key for key in contract.OUTCOME_COLUMNS[kind] if not isinstance(outcome.get(key), str)
        ]
        if missing:
            errors.append(f"outcome kind {kind!r} needs column(s) {missing}.")
        if kind == "late_flag" and outcome.get("late_when") not in contract.POLARITIES:
            errors.append("outcome.late_when must be 'true' or 'false'.")
        if kind in ("days_pair", "delay_value") and outcome.get("unit") not in contract.UNITS:
            errors.append(f"outcome.unit must be one of {list(contract.UNITS)}.")
    country = mapping.get("country")
    if country is not None and not isinstance(country, str):
        errors.append("country must be a column name or null.")
    confidence = mapping.get("confidence")
    if confidence is not None and not (
        isinstance(confidence, int | float) and 0 <= float(confidence) <= 1
    ):
        errors.append("confidence must be a number from 0 to 1.")

    # Columns exist and are used once.
    used = contract.used_columns(mapping)
    for role, column in used:
        if column not in columns:
            errors.append(f"{role}: column {column!r} does not exist in this dataset.")
    seen: dict[str, str] = {}
    for role, column in used:
        if column in seen:
            errors.append(f"Column {column!r} is used twice ({seen[column]} and {role}).")
        seen.setdefault(column, role)

    def kind_of(column: str) -> str | None:
        return columns[column]["inferred_type"] if column in columns else None

    # Types fit roles.
    entity = mapping.get("entity")
    if isinstance(entity, str) and entity in columns and kind_of(entity) not in ("string", "int"):
        errors.append(
            f"entity: {entity!r} holds {kind_of(entity)} values; an entity is a name or code."
        )
    if event_date and event_date["col"] in columns and isinstance(event_date.get("format"), str):
        values = context.frame[event_date["col"]].dropna()
        share = (
            float(
                pd.to_datetime(values, format=event_date["format"], errors="coerce").notna().mean()
            )
            if len(values)
            else 0.0
        )
        if share < DATE_PARSE_MIN:
            errors.append(
                f"event_date: format {event_date['format']!r} parses only "
                f"{round(100 * share, 1)}% of {event_date['col']!r} (needs 95%)."
            )
    if outcome:
        if kind == "late_flag" and outcome.get("col") in columns:
            col = outcome["col"]
            if not _bool_like(context.frame[col]):
                errors.append(f"outcome: {col!r} is not boolean-like (yes/no, true/false, 0/1).")
            elif outcome.get("late_when") in contract.POLARITIES:
                _check_polarity(context, col, outcome["late_when"], errors)
        for key in ("scheduled_col", "actual_col") if kind == "days_pair" else ("col",):
            col = outcome.get(key)
            if kind != "late_flag" and col in columns and kind_of(col) not in ("int", "float"):
                errors.append(f"outcome.{key}: {col!r} holds {kind_of(col)} values, not numbers.")
    if isinstance(country, str) and country in columns and kind_of(country) != "string":
        errors.append(f"country: {country!r} holds {kind_of(country)} values, not names.")

    # Evidence numbers must come from the profile.
    evidence = mapping.get("evidence") or {}
    if not isinstance(evidence, Mapping) or not all(
        isinstance(items, list) for items in evidence.values()
    ):
        errors.append("evidence must map each field to a list of facts.")
    else:
        sources = [{"name": "profile", "result": context.facts()}, *(extra_facts or [])]
        grounding = check_grounding({"facts": evidence}, sources)
        if grounding.ungrounded_numbers:
            errors.append(
                f"evidence quotes numbers the profile does not contain: "
                f"{', '.join(grounding.ungrounded_numbers)}."
            )

    if errors:
        result.mappable = contract.Verdict(False, errors[0])
        result.scoreable = contract.Verdict(False, "not mappable")
        return result
    if unmapped:
        notes = str(mapping.get("notes") or "").strip()[:200]
        result.mappable = contract.Verdict(
            False,
            f"no column fits the required field(s) {', '.join(unmapped)}"
            + (f": {notes}" if notes else ""),
        )
        result.scoreable = contract.Verdict(False, "not mappable")
        return result
    result.mappable = contract.Verdict(
        True,
        f"entity {entity!r}, event_date {event_date['col']!r} and a {kind} outcome all map to "
        "valid columns",
    )
    result.scoreable = contract.scoreable_verdict(_quarter_counts(context, mapping))
    return result
