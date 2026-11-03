"""Canonical silver for a mapped dataset: ds_<id>.silver_shipments and silver_rejects.

One row per bronze row that parses, in the contract's canonical shape:

    entity_key, entity_name, event_date, quarter, is_late, delay_days,
    is_cancelled, country_iso3, _batch_id

The SQL is a fixed template. Column identifiers come only from the validated,
approved mapping and are quoted by identifiers.quote_column (which refuses any
name the profile did not record); every value (date format, polarity words,
the cancelled value, the null tokens, the hours divisor) is a bound parameter.
Rows that fail to parse go to silver_rejects with the first reason that applied.

Definitions follow the flagship: the entity key is the lower-cased name with
runs of other characters replaced by '_' (stg_dataco_order_items' supplier_key,
without its 'cat_' prefix); delay is actual - scheduled for a days_pair, the
value itself for delay_value, and nothing for a late_flag; hours become days by
dividing by 24; late means actual > scheduled, a delay > 0, or the flag's
late_when value. Country values become ISO3 through datasets.country.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from typing import Any

import duckdb

from provenance.datasets import country as country_module
from provenance.datasets.identifiers import quote_column, quote_schema
from provenance.datasets.profile import NULL_TOKENS
from provenance.datasets.validator import FALSY, TRUTHY

ENTITY_KEY_CHARS = 64
HOURS_PER_DAY = 24.0


@dataclass
class SilverReport:
    rows_in: int = 0
    rows_out: int = 0
    rejected: dict[str, int] = field(default_factory=dict)
    country: dict[str, Any] | None = None
    country_used: bool = False
    country_reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "rows_in": self.rows_in,
            "rows_out": self.rows_out,
            "rows_rejected": sum(self.rejected.values()),
            "rejected_by_reason": dict(sorted(self.rejected.items())),
            "country": self.country,
            "country_used": self.country_used,
            "country_reason": self.country_reason,
        }


def _clean(column_sql: str) -> str:
    """Trimmed text, with the profile's null tokens ('', NA, null, ...) as NULL."""
    return (
        f"CASE WHEN list_contains($null_tokens, lower(trim({column_sql}))) THEN NULL "
        f"ELSE trim({column_sql}) END"
    )


def _outcome_sql(outcome: Mapping[str, Any], quote) -> tuple[str, str, str, str, str]:
    """(value_1, value_2, is_late, delay_days, problem-when-unparsed) SQL for an outcome."""
    kind = outcome["kind"]
    if kind == "late_flag":
        value = _clean(quote(outcome["col"]))
        flag = "lower(outcome_value)"
        is_late = (
            f"CASE WHEN list_contains($truthy, {flag}) THEN $late_when = 'true' "
            f"WHEN list_contains($falsy, {flag}) THEN $late_when = 'false' END"
        )
        return value, "NULL", is_late, "NULL::DOUBLE", "late flag is not yes/no, true/false or 1/0"
    if kind == "days_pair":
        scheduled = _clean(quote(outcome["scheduled_col"]))
        actual = _clean(quote(outcome["actual_col"]))
        sched = "try_cast(outcome_value AS DOUBLE)"
        act = "try_cast(outcome_value_2 AS DOUBLE)"
        return (
            scheduled,
            actual,
            f"{act} > {sched}",
            f"({act} - {sched}) / $divisor",
            "scheduled or actual duration is not a number",
        )
    value = _clean(quote(outcome["col"]))
    delay = "try_cast(outcome_value AS DOUBLE)"
    return value, "NULL", f"{delay} > 0", f"{delay} / $divisor", "delay is not a number"


def build_silver(
    connection: duckdb.DuckDBPyConnection,
    *,
    schema: str,
    profiled: Collection[str],
    mapping: Mapping[str, Any],
    min_country_match: float = country_module.DEFAULT_MIN_MATCH_RATE,
) -> SilverReport:
    """(Re)build silver_shipments, silver_rejects and country_map in `schema`.

    Runs on the caller's connection and transaction, so a failure later in the
    scoring run leaves the previous silver in place.
    """
    target = quote_schema(schema)

    def quote(name: str) -> str:
        return quote_column(name, profiled)

    outcome = mapping["outcome"]
    value_1, value_2, is_late, delay, outcome_problem = _outcome_sql(outcome, quote)
    country_col = mapping.get("country")
    cancelled = mapping.get("cancelled")
    params: dict[str, Any] = {
        "null_tokens": sorted(NULL_TOKENS),
        "date_format": mapping["event_date"]["format"],
    }
    if outcome["kind"] == "late_flag":
        params.update(truthy=sorted(TRUTHY), falsy=sorted(FALSY), late_when=outcome["late_when"])
    else:
        params["divisor"] = HOURS_PER_DAY if outcome.get("unit") == "hours" else 1.0
    cancelled_sql = "false"
    if cancelled:
        cancelled_sql = f"coalesce({_clean(quote(cancelled['col']))} = $cancelled_value, false)"
        params["cancelled_value"] = str(cancelled["value"]).strip()

    connection.execute("DROP TABLE IF EXISTS _silver_parsed")
    connection.execute(
        f"""
        CREATE TEMP TABLE _silver_parsed AS
        WITH raw AS (
            SELECT
                rowid AS bronze_rowid,
                _batch_id,
                {_clean(quote(mapping["entity"]))} AS entity_value,
                {_clean(quote(mapping["event_date"]["col"]))} AS date_value,
                {value_1} AS outcome_value,
                {value_2} AS outcome_value_2,
                {_clean(quote(country_col)) if country_col else "NULL::VARCHAR"} AS country_value,
                {cancelled_sql} AS is_cancelled
            FROM {target}.bronze_raw
        ),
        parsed AS (
            SELECT
                *,
                left(
                    trim(regexp_replace(lower(entity_value), '[^a-z0-9]+', '_', 'g'), '_'),
                    {ENTITY_KEY_CHARS}
                ) AS entity_key,
                try_strptime(date_value, $date_format) AS event_ts,
                {is_late} AS is_late,
                {delay} AS delay_days
            FROM raw
        )
        SELECT
            *,
            CASE
                WHEN entity_value IS NULL THEN 'entity is empty'
                WHEN entity_key = '' THEN 'entity has no letters or digits'
                WHEN date_value IS NULL THEN 'event_date is empty'
                WHEN event_ts IS NULL THEN 'event_date does not match the mapped format'
                WHEN outcome_value IS NULL
                    OR ({"outcome_value_2 IS NULL" if outcome["kind"] == "days_pair" else "false"})
                    THEN 'outcome is empty'
                WHEN is_late IS NULL THEN '{outcome_problem}'
            END AS reject_reason
        FROM parsed
        """,
        params,
    )

    report = SilverReport()
    report.rows_in = connection.execute("SELECT count(*) FROM _silver_parsed").fetchone()[0]
    report.rejected = dict(
        connection.execute(
            "SELECT reject_reason, count(*) FROM _silver_parsed "
            "WHERE reject_reason IS NOT NULL GROUP BY 1"
        ).fetchall()
    )
    report.rows_out = report.rows_in - sum(report.rejected.values())

    # Country values -> ISO3, matched in Python over distinct values only.
    connection.execute(f"DROP TABLE IF EXISTS {target}.country_map")
    connection.execute(
        f"CREATE TABLE {target}.country_map (raw_value VARCHAR PRIMARY KEY, iso3 VARCHAR)"
    )
    if country_col:
        counts = connection.execute(
            "SELECT country_value, count(*) FROM _silver_parsed "
            "WHERE reject_reason IS NULL AND country_value IS NOT NULL GROUP BY 1"
        ).fetchall()
        matched = country_module.normalise(counts)
        if matched.mapping:
            connection.executemany(
                f"INSERT INTO {target}.country_map VALUES (?, ?)",
                list(matched.mapping.items()),
            )
        report.country = matched.as_dict()
        rate = matched.match_rate
        report.country_used = matched.rows > 0 and rate >= min_country_match
        report.country_reason = (
            f"{round(100 * rate, 1)}% of rows with a country matched an ISO3 code"
            + (
                ""
                if report.country_used
                else f" (below the {round(100 * min_country_match)}% needed)"
            )
        )
    else:
        report.country_reason = "the mapping has no country column"

    connection.execute(f"DROP TABLE IF EXISTS {target}.silver_shipments")
    connection.execute(
        f"""
        CREATE TABLE {target}.silver_shipments AS
        SELECT
            parsed.entity_key,
            parsed.entity_value AS entity_name,
            parsed.event_ts::DATE AS event_date,
            date_trunc('quarter', parsed.event_ts)::DATE AS quarter,
            parsed.is_late,
            parsed.delay_days,
            parsed.is_cancelled,
            country_map.iso3 AS country_iso3,
            parsed._batch_id
        FROM _silver_parsed AS parsed
        LEFT JOIN {target}.country_map AS country_map
            ON country_map.raw_value = parsed.country_value
        WHERE parsed.reject_reason IS NULL
        """
    )
    connection.execute(f"DROP TABLE IF EXISTS {target}.silver_rejects")
    connection.execute(
        f"""
        CREATE TABLE {target}.silver_rejects AS
        SELECT bronze_rowid, _batch_id, reject_reason AS reason,
               entity_value, date_value, outcome_value, outcome_value_2
        FROM _silver_parsed
        WHERE reject_reason IS NOT NULL
        """
    )
    connection.execute("DROP TABLE _silver_parsed")
    return report
