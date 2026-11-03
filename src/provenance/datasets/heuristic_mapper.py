"""Deterministic column mapper: synonyms (EN, ES, DE, FR) plus profile types.

It is the fallback for the agent and a baseline in the evaluation. Names are
compared as accent-free word tokens; a role only accepts a column whose profiled
type fits (dates parse at least 95% with their detected format, delays and day
counts are numeric, late flags are boolean-like or 0/1). Polarity comes from the
name: on_time / a_tiempo / puenktlich map with late_when=false.
"""

from __future__ import annotations

from typing import Any

from provenance.datasets.validator import (
    ACTUAL_WORDS,
    FALSY,
    LATE_WORDS,
    ON_TIME_WORDS,
    SCHEDULED_WORDS,
    TRUTHY,
    DatasetContext,
    has_word,
)

ENTITY_WORDS = {
    3: (
        "supplier",
        "vendor",
        "proveedor",
        "lieferant",
        "fournisseur",
        "manufacturer",
        "fabricante",
        "fabricant",
        "hersteller",
        "seller",
        "vendedor",
        "vendeur",
        "anbieter",
    ),
    2: (
        "category",
        "categoria",
        "kategorie",
        "categorie",
        "provider",
        "merchant",
        "brand",
        "marca",
        "marke",
        "marque",
    ),
}
DATE_WORDS = {
    3: (
        "order",
        "ordered",
        "pedido",
        "bestellung",
        "bestelldatum",
        "bestell",
        "commande",
        "purchase",
        "placed",
        "compra",
    ),
    2: (
        "ship",
        "shipped",
        "shipping",
        "shipment",
        "envio",
        "versand",
        "versanddatum",
        "expedition",
        "dispatch",
    ),
    1: ("date", "fecha", "datum", "dt", "day"),
}
LATE_DATE_WORDS = (
    "delivery",
    "delivered",
    "arrival",
    "entrega",
    "lieferdatum",
    "livraison",
    "received",
    "recibido",
)
COUNTRY_WORDS = ("country", "pais", "land", "pays", "nation", "nacion", "staat")
HOUR_WORDS = (
    "hour",
    "hours",
    "hrs",
    "hr",
    "horas",
    "hora",
    "stunden",
    "stunde",
    "heures",
    "heure",
    "h",
)


def _score(name: str, table: dict[int, tuple[str, ...]]) -> int:
    return max((weight for weight, words in table.items() if has_word(name, words)), default=0)


def _fact(column: dict[str, Any], *keys: str) -> list[str]:
    facts = []
    for key in keys:
        value = column.get(key)
        if key == "type_share" and value is not None:
            facts.append(f"type_share_pct={round(100 * value, 2)}")
        elif value is not None:
            facts.append(f"{key}={value}")
    return facts


def _bool_like(context: DatasetContext, name: str) -> bool:
    values = {str(v).lower() for v in context.frame[name].dropna().unique()}
    return 0 < len(values) <= 2 and values <= (TRUTHY | FALSY)


def heuristic_mapping(context: DatasetContext) -> dict[str, Any]:
    return heuristic_assessment(context)[0]


def heuristic_assessment(context: DatasetContext) -> tuple[dict[str, Any], list[str]]:
    """The heuristic mapping plus the places where two candidates were equally good."""
    columns = context.columns
    close: list[str] = []
    order = list(columns)
    used: set[str] = set()
    evidence: dict[str, list[str]] = {}
    confidences: list[float] = []
    notes: list[str] = []

    def numeric(name: str) -> bool:
        return columns[name]["inferred_type"] in ("int", "float") and not _bool_like(context, name)

    # event_date: a real date column, order dates first, delivery dates last.
    dates = [
        name
        for name in order
        if columns[name]["looks_like_date"]
        and columns[name]["date_format"]
        and (columns[name]["type_share"] or 0) >= 0.95
    ]
    event_date = None
    if dates:
        ranked = sorted(
            dates,
            key=lambda n: (
                -(_score(n, DATE_WORDS) - 2 * has_word(n, LATE_DATE_WORDS)),
                order.index(n),
            ),
        )
        best = ranked[0]
        rank = lambda n: _score(n, DATE_WORDS) - 2 * has_word(n, LATE_DATE_WORDS)  # noqa: E731
        if len(ranked) > 1 and rank(ranked[1]) == rank(best):
            close.append(f"event_date: {best!r} and {ranked[1]!r} rank equally")
        event_date = {"col": best, "format": columns[best]["date_format"]}
        used.add(best)
        evidence["event_date"] = [
            f"date_format={columns[best]['date_format']}",
            *_fact(columns[best], "type_share", "null_pct"),
        ]
        confidences.append(0.9 if _score(best, DATE_WORDS) >= 2 else 0.6)
    else:
        notes.append("no column parses as dates in at least 95% of rows")

    # entity: text or whole-number labels with a supplier-like name.
    entity_candidates = [
        name
        for name in order
        if name not in used
        and columns[name]["inferred_type"] in ("string", "int")
        and _score(name, ENTITY_WORDS) > 0
    ]
    entity = None
    if entity_candidates:
        entity = max(entity_candidates, key=lambda n: (_score(n, ENTITY_WORDS), -order.index(n)))
        rivals = [
            n
            for n in entity_candidates
            if n != entity and _score(n, ENTITY_WORDS) == _score(entity, ENTITY_WORDS)
        ]
        if rivals:
            close.append(f"entity: {entity!r} and {rivals[0]!r} rank equally")
        used.add(entity)
        evidence["entity"] = [f"inferred_type={columns[entity]['inferred_type']}",
                              *_fact(columns[entity], "distinct_count", "null_pct")]  # fmt: skip
        confidences.append(0.9 if _score(entity, ENTITY_WORDS) == 3 else 0.7)
    else:
        notes.append("no column is named like a supplier, vendor or category")

    # outcome: scheduled/actual pair, then a delay value, then a late flag.
    outcome = None
    free = [name for name in order if name not in used]
    scheduled = next((n for n in free if numeric(n) and has_word(n, SCHEDULED_WORDS)), None)
    actual = next(
        (n for n in free if numeric(n) and has_word(n, ACTUAL_WORDS) and n != scheduled), None
    )
    delay = next(
        (
            n
            for n in free
            if numeric(n) and has_word(n, LATE_WORDS) and not has_word(n, ON_TIME_WORDS)
        ),
        None,
    )
    flag = next(
        (
            n
            for n in free
            if _bool_like(context, n) and (has_word(n, ON_TIME_WORDS) or has_word(n, LATE_WORDS))
        ),
        None,
    )
    delays = [
        n for n in free if numeric(n) and has_word(n, LATE_WORDS) and not has_word(n, ON_TIME_WORDS)
    ]
    if not (scheduled and actual) and len(delays) > 1:
        close.append(f"outcome: {delays[0]!r} and {delays[1]!r} both look like delays")
    if scheduled and actual:
        unit = (
            "hours" if has_word(scheduled, HOUR_WORDS) or has_word(actual, HOUR_WORDS) else "days"
        )
        outcome = {
            "kind": "days_pair",
            "scheduled_col": scheduled,
            "actual_col": actual,
            "unit": unit,
        }
        used.update({scheduled, actual})
        evidence["outcome"] = [
            f"{scheduled}: inferred_type={columns[scheduled]['inferred_type']}",
            f"{actual}: inferred_type={columns[actual]['inferred_type']}",
        ]
        confidences.append(0.9)
    elif delay:
        unit = "hours" if has_word(delay, HOUR_WORDS) else "days"
        outcome = {"kind": "delay_value", "col": delay, "unit": unit}
        used.add(delay)
        evidence["outcome"] = [f"inferred_type={columns[delay]['inferred_type']}",
                               *_fact(columns[delay], "min_value", "max_value")]  # fmt: skip
        confidences.append(0.85)
    elif flag:
        late_when = "false" if has_word(flag, ON_TIME_WORDS) else "true"
        outcome = {"kind": "late_flag", "col": flag, "late_when": late_when}
        used.add(flag)
        evidence["outcome"] = [f"distinct_count={columns[flag]['distinct_count']}"]
        confidences.append(0.85)
    else:
        notes.append(
            "no column records lateness (a late/on-time flag, a delay, or scheduled and "
            "actual durations)"
        )

    # country (optional).
    country = next(
        (
            n
            for n in order
            if n not in used
            and columns[n]["inferred_type"] == "string"
            and has_word(n, COUNTRY_WORDS)
        ),
        None,
    )
    if country:
        evidence["country"] = _fact(columns[country], "distinct_count")

    required_found = sum(value is not None for value in (entity, event_date, outcome))
    confidence = round(sum(confidences) / 3, 2) if required_found else 0.0
    mapping = {
        "entity": entity,
        "event_date": event_date,
        "outcome": outcome,
        "country": country,
        "evidence": evidence,
        "confidence": confidence,
        "notes": "; ".join(notes) if notes else "matched by column names and profiled types",
    }
    return mapping, close
