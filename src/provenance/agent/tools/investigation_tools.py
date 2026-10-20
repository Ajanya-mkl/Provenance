"""Read-only investigation tools for the agent.

Every tool runs fixed, parameterised SQL on a read-only DuckDB connection; the
model only supplies arguments. Results are small, rounded, supplier-category
aggregates. Bad or unknown arguments return {"error": ...} instead of raising.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import duckdb

from provenance.agent.memory.investigation_memory import MAX_RECALL, recall_past_investigations

DECIMALS = 3
EVENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.:\-]{1,128}$")
SUPPLIER_KEY_PATTERN = re.compile(r"^[a-z0-9_]{1,64}$")
ISO_QUARTER_PATTERN = re.compile(r"^(\d{4})-(\d{2})-01$")
LABEL_QUARTER_PATTERN = re.compile(r"^(\d{4})-?Q([1-4])$", re.IGNORECASE)
MAX_HISTORY_ROWS = 5
MAX_DESTINATIONS = 3


def _round(value: Any) -> Any:
    return round(float(value), DECIMALS) if value is not None else None


def _error(message: str) -> dict[str, str]:
    return {"error": message}


def parse_quarter(value: Any) -> date | None:
    """Accept 2017-01-01, 2017-Q1 or 2017Q1; return the quarter start or None."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if match := LABEL_QUARTER_PATTERN.match(text):
        return date(int(match[1]), 3 * int(match[2]) - 2, 1)
    if match := ISO_QUARTER_PATTERN.match(text):
        month = int(match[2])
        if month in {1, 4, 7, 10}:
            return date(int(match[1]), month, 1)
    return None


def quarter_label(quarter: date) -> str:
    return f"{quarter.year}-Q{(quarter.month - 1) // 3 + 1}"


def _previous_quarter(quarter: date) -> date:
    return (
        date(quarter.year - 1, 10, 1)
        if quarter.month == 1
        else date(quarter.year, quarter.month - 3, 1)
    )


class InvestigationTools:
    """The read-only tools, bound to one read-only connection."""

    def __init__(self, db_path: str | Path) -> None:
        self._connection = duckdb.connect(str(db_path), read_only=True)
        self._handlers: dict[str, Callable[..., dict[str, Any]]] = {
            "get_flag": self.get_flag,
            "get_supplier_history": self.get_supplier_history,
            "get_score_breakdown": self.get_score_breakdown,
            "get_country_risk": self.get_country_risk,
            "get_market_mix": self.get_market_mix,
            "recall_past_investigations": self.recall_past_investigations,
        }

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> InvestigationTools:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @property
    def names(self) -> list[str]:
        return list(self._handlers)

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        handler = self._handlers.get(name)
        if handler is None:
            return _error(f"unknown tool {name!r}")
        expected = TOOL_ARGUMENTS[name]
        optional = OPTIONAL_ARGUMENTS.get(name, ())
        if not isinstance(arguments, dict) or not (
            set(expected) <= set(arguments) <= set(expected) | set(optional)
        ):
            return _error(f"{name} takes exactly these arguments: {', '.join(expected)}")
        try:
            return handler(**arguments)
        except duckdb.Error as error:
            return _error(f"query failed: {type(error).__name__}")

    # -- validation -------------------------------------------------------

    def _check_supplier(self, supplier_key: Any) -> str | None:
        if not isinstance(supplier_key, str) or not SUPPLIER_KEY_PATTERN.match(supplier_key):
            return "supplier_key must be a lowercase key such as cat_golf_balls"
        found = self._connection.execute(
            "SELECT 1 FROM silver.dim_supplier_scd2 WHERE supplier_key = ? LIMIT 1",
            [supplier_key],
        ).fetchone()
        return None if found else f"unknown supplier {supplier_key!r}"

    def _check_supplier_quarter(
        self, supplier_key: Any, quarter: Any
    ) -> tuple[date | None, str | None]:
        if problem := self._check_supplier(supplier_key):
            return None, problem
        parsed = parse_quarter(quarter)
        if parsed is None:
            return None, "quarter must look like 2017-Q1 or 2017-01-01"
        scored = self._connection.execute(
            "SELECT 1 FROM gold.supplier_risk_score WHERE supplier_key = ? AND quarter = ?",
            [supplier_key, parsed],
        ).fetchone()
        if not scored:
            return None, f"no score for {supplier_key} in {quarter_label(parsed)}"
        return parsed, None

    # -- tools ------------------------------------------------------------

    def get_flag(self, event_id: Any) -> dict[str, Any]:
        if not isinstance(event_id, str) or not EVENT_ID_PATTERN.match(event_id):
            return _error("event_id has an invalid format")
        row = self._connection.execute(
            """
            SELECT event_type, investigation_status, payload
            FROM agent.events
            WHERE event_id = ?
            """,
            [event_id],
        ).fetchone()
        if row is None:
            return _error(f"unknown event {event_id!r}")
        event_type, status, payload = row
        if event_type != "risk_flag":
            return _error(f"event {event_id!r} is a {event_type}, not a risk_flag")
        payload = json.loads(payload) if isinstance(payload, str) else payload
        factor = payload.get("top_contributing_factor") or {}
        quarter = parse_quarter(str(payload.get("quarter")))
        return {
            "event_id": event_id,
            "investigation_status": status,
            "supplier_key": payload.get("supplier_key"),
            "quarter": quarter_label(quarter) if quarter else payload.get("quarter"),
            "old_tier": payload.get("old_tier"),
            "new_tier": payload.get("new_tier"),
            "score": _round(payload.get("score")),
            "confidence": payload.get("confidence"),
            "top_factor": {
                "name": factor.get("name"),
                "raw_value": _round(factor.get("raw_value")),
                "normalized_value": _round(factor.get("normalized_value")),
                "weight": _round(factor.get("weight")),
                "weighted_contribution": _round(factor.get("weighted_contribution")),
                "weighted_contribution_change": _round(factor.get("weighted_contribution_change")),
            },
        }

    def get_supplier_history(self, supplier_key: Any) -> dict[str, Any]:
        if problem := self._check_supplier(supplier_key):
            return _error(problem)
        rows = self._connection.execute(
            """
            SELECT supplier_name, version_number, delivery_band, volume_band,
                   effective_from, effective_to, is_current
            FROM silver.dim_supplier_scd2
            WHERE supplier_key = ?
            ORDER BY version_number DESC
            LIMIT ?
            """,
            [supplier_key, MAX_HISTORY_ROWS],
        ).fetchall()
        return {
            "supplier_key": supplier_key,
            "supplier_name": rows[0][0],
            "versions": [
                {
                    "version": row[1],
                    "delivery_band": row[2],
                    "volume_band": row[3],
                    "effective_from": row[4].isoformat(),
                    "effective_to": row[5].isoformat() if row[5] else None,
                    "is_current": bool(row[6]),
                }
                for row in rows
            ],
        }

    def get_score_breakdown(self, supplier_key: Any, quarter: Any) -> dict[str, Any]:
        parsed, problem = self._check_supplier_quarter(supplier_key, quarter)
        if problem:
            return _error(problem)
        score, tier, factors, confidence, rules_version = self._connection.execute(
            """
            SELECT score, tier, contributing_factors, confidence, rules_version
            FROM gold.supplier_risk_score
            WHERE supplier_key = ? AND quarter = ?
            """,
            [supplier_key, parsed],
        ).fetchone()
        previous = self._connection.execute(
            """
            SELECT quarter, score, tier, contributing_factors
            FROM gold.supplier_risk_score
            WHERE supplier_key = ? AND quarter < ?
            ORDER BY quarter DESC
            LIMIT 1
            """,
            [supplier_key, parsed],
        ).fetchone()
        metrics = self._connection.execute(
            """
            SELECT shipment_count, late_rate, avg_delay_days, delay_stddev,
                   eligible_quarters_to_date, country_coverage
            FROM gold.mart_supplier_risk
            WHERE supplier_key = ? AND quarter = ?
            """,
            [supplier_key, parsed],
        ).fetchone()

        factors = json.loads(factors) if isinstance(factors, str) else factors
        previous_factors = {}
        if previous:
            previous_factors = (
                json.loads(previous[3]) if isinstance(previous[3], str) else previous[3]
            )
        breakdown = []
        for name, factor in sorted(
            factors.items(), key=lambda item: -float(item[1]["weighted_contribution"])
        ):
            entry = {
                "factor": name,
                "raw_value": _round(factor["raw_value"]),
                "normalized_value": _round(factor["normalized_value"]),
                "weight": _round(factor["weight"]),
                "weighted_contribution": _round(factor["weighted_contribution"]),
            }
            if name in previous_factors:
                entry["previous_raw_value"] = _round(previous_factors[name]["raw_value"])
            breakdown.append(entry)

        result: dict[str, Any] = {
            "supplier_key": supplier_key,
            "quarter": quarter_label(parsed),
            "score": _round(score),
            "tier": tier,
            "confidence": confidence,
            "rules_version": rules_version,
            "factors": breakdown,
            "previous_scored_quarter": None,
        }
        if previous:
            result["previous_scored_quarter"] = {
                "quarter": quarter_label(previous[0]),
                "score": _round(previous[1]),
                "tier": previous[2],
            }
        if metrics:
            result["quarter_metrics"] = {
                "shipment_count": int(metrics[0]),
                "late_rate": _round(metrics[1]),
                "avg_delay_days": _round(metrics[2]),
                "delay_stddev": _round(metrics[3]),
                "eligible_quarters_to_date": int(metrics[4]),
                "country_coverage": _round(metrics[5]),
            }
        return result

    def get_country_risk(self, supplier_key: Any, quarter: Any) -> dict[str, Any]:
        parsed, problem = self._check_supplier_quarter(supplier_key, quarter)
        if problem:
            return _error(problem)
        # Same formula as gold.mart_supplier_risk: latest LPI at or before the
        # quarter's year, median LPI for destinations without one.
        destinations = self._connection.execute(
            """
            WITH lpi AS (
                SELECT country_iso3, lpi_score
                FROM silver.stg_worldbank_lpi
                WHERE lpi_year <= year(?::DATE)
                QUALIFY row_number() OVER (
                    PARTITION BY country_iso3 ORDER BY lpi_year DESC
                ) = 1
            ),
            median_lpi AS (SELECT median(lpi_score) AS median_score FROM lpi),
            destinations AS (
                SELECT order_country_iso3 AS iso3, count(*) AS shipments
                FROM silver.fact_shipments
                WHERE supplier_key = ?
                  AND order_date >= ?::DATE
                  AND order_date < ?::DATE + INTERVAL 3 MONTH
                GROUP BY order_country_iso3
            )
            SELECT
                destinations.iso3,
                destinations.shipments,
                lpi.lpi_score,
                (5 - coalesce(lpi.lpi_score, median_lpi.median_score)) / 4 AS risk
            FROM destinations
            LEFT JOIN lpi ON lpi.country_iso3 = destinations.iso3
            CROSS JOIN median_lpi
            ORDER BY destinations.shipments DESC, destinations.iso3
            """,
            [parsed, supplier_key, parsed, parsed],
        ).fetchall()
        trailing = self._connection.execute(
            """
            SELECT country_risk, country_coverage
            FROM gold.mart_supplier_risk
            WHERE supplier_key = ? AND quarter = ?
            """,
            [supplier_key, parsed],
        ).fetchone()
        total = sum(int(row[1]) for row in destinations)
        if total == 0:
            return _error(f"no shipments for {supplier_key} in {quarter_label(parsed)}")
        quarter_risk = sum(int(row[1]) * float(row[3]) for row in destinations) / total
        return {
            "supplier_key": supplier_key,
            "quarter": quarter_label(parsed),
            "quarter_country_risk": _round(quarter_risk),
            "trailing_country_risk": _round(trailing[0]) if trailing else None,
            "country_coverage": _round(trailing[1]) if trailing else None,
            "destination_count": len(destinations),
            "top_destinations": [
                {
                    "country_iso3": iso3,
                    "shipment_share": _round(int(shipments) / total),
                    "lpi_score": _round(lpi_score),
                    "country_risk": _round(risk),
                }
                for iso3, shipments, lpi_score, risk in destinations[:MAX_DESTINATIONS]
            ],
        }

    def get_market_mix(self, supplier_key: Any, quarter: Any) -> dict[str, Any]:
        parsed, problem = self._check_supplier_quarter(supplier_key, quarter)
        if problem:
            return _error(problem)
        previous = _previous_quarter(parsed)
        rows = self._connection.execute(
            """
            SELECT
                market,
                count(*) FILTER (WHERE order_date >= ?::DATE) AS current_shipments,
                count(*) FILTER (WHERE order_date < ?::DATE) AS previous_shipments
            FROM silver.fact_shipments
            WHERE supplier_key = ?
              AND order_date >= ?::DATE
              AND order_date < ?::DATE + INTERVAL 3 MONTH
            GROUP BY market
            ORDER BY current_shipments DESC, market
            """,
            [parsed, parsed, supplier_key, previous, parsed],
        ).fetchall()
        current_total = sum(int(row[1]) for row in rows)
        previous_total = sum(int(row[2]) for row in rows)
        if current_total == 0:
            return _error(f"no shipments for {supplier_key} in {quarter_label(parsed)}")
        markets = []
        for market, current_count, previous_count in rows:
            share = int(current_count) / current_total
            entry: dict[str, Any] = {"market": market, "share": _round(share)}
            if previous_total:
                prior = int(previous_count) / previous_total
                entry["previous_share"] = _round(prior)
                entry["share_change"] = _round(share - prior)
            markets.append(entry)
        return {
            "supplier_key": supplier_key,
            "quarter": quarter_label(parsed),
            "previous_quarter": quarter_label(previous),
            "markets": markets,
        }

    def recall_past_investigations(self, supplier_key: Any, limit: Any = 3) -> dict[str, Any]:
        if problem := self._check_supplier(supplier_key):
            return _error(problem)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_RECALL:
            return _error(f"limit must be an integer from 1 to {MAX_RECALL}")
        cases = recall_past_investigations(self._connection, supplier_key, limit)
        for case in cases:
            parsed = parse_quarter(case["quarter"])
            case["quarter"] = quarter_label(parsed) if parsed else case["quarter"]
        return {"supplier_key": supplier_key, "count": len(cases), "past_investigations": cases}


TOOL_ARGUMENTS: dict[str, tuple[str, ...]] = {
    "get_flag": ("event_id",),
    "get_supplier_history": ("supplier_key",),
    "get_score_breakdown": ("supplier_key", "quarter"),
    "get_country_risk": ("supplier_key", "quarter"),
    "get_market_mix": ("supplier_key", "quarter"),
    "recall_past_investigations": ("supplier_key",),
}
OPTIONAL_ARGUMENTS: dict[str, tuple[str, ...]] = {"recall_past_investigations": ("limit",)}

_ARGUMENT_SCHEMAS = {
    "event_id": {"type": "string", "description": "agent.events event_id"},
    "supplier_key": {"type": "string", "description": "Supplier key, e.g. cat_golf_balls"},
    "quarter": {"type": "string", "description": "Quarter as 2017-Q1"},
    "limit": {
        "type": "integer",
        "description": f"How many past cases to return (1-{MAX_RECALL}, default 3)",
    },
}

_DESCRIPTIONS = {
    "get_flag": "The risk flag event: supplier, quarter, tier change, score, top factor.",
    "get_supplier_history": "SCD2 history of the supplier's delivery and volume bands.",
    "get_score_breakdown": (
        "Score, tier and every weighted factor for a supplier-quarter, with the "
        "previous scored quarter and the quarter's delivery metrics."
    ),
    "get_country_risk": (
        "Shipment-weighted destination country risk (from World Bank LPI) for a "
        "supplier-quarter, trailing value used in scoring, and top destinations."
    ),
    "get_market_mix": "Share of shipments by destination market vs the previous quarter.",
    "recall_past_investigations": (
        "Earlier grounded investigations of this supplier: quarter, tier change, top "
        "factor, proposed action and the human decision, if any."
    ),
}

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": name,
            "description": _DESCRIPTIONS[name],
            "parameters": {
                "type": "object",
                "properties": {
                    argument: _ARGUMENT_SCHEMAS[argument]
                    for argument in arguments + OPTIONAL_ARGUMENTS.get(name, ())
                },
                "required": list(arguments),
                "additionalProperties": False,
            },
        },
    }
    for name, arguments in TOOL_ARGUMENTS.items()
]
