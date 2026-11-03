"""Quarterly metrics and the risk mart for a dataset, with the flagship's definitions.

These are the dbt models int_supplier_quarter_metrics and mart_supplier_risk
rewritten over ds_<id>.silver_shipments, line for line where they can be:

- late rate and delay exclude cancelled rows, which still count as shipments;
- delay variability is the sample standard deviation of delay_days;
- a quarter is partial when the data starts after it begins, ends before it
  finishes, or fewer than `partial_quarter_min_day_coverage` of its days have
  a row (computed over the whole dataset, not per entity);
- an entity-quarter with fewer than `min_quarter_shipments` rows is low volume;
- eligible = not low volume, not partial, late rate known; eligible_quarters_to_date
  counts them per entity;
- country risk is destination-based: (5 - LPI) / 4 with the latest World Bank LPI
  at or before the quarter, the quarter's median LPI for countries without one,
  shipment-weighted over the trailing `country_risk_trailing_eligible_quarters`
  eligible quarters; coverage is the share of those shipments with a real LPI.

The thresholds are read from the dbt project's vars, so the two paths cannot
drift apart. The flagship mart's join to dim_supplier_scd2 only checks that a
dimension version covers the quarter (its band is not used for scoring), and
the dimension always starts at an entity's first quarter, so it is not needed.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import duckdb
import yaml

from provenance.datasets.identifiers import quote_schema

DBT_PROJECT = Path(__file__).resolve().parents[1] / "transform" / "dbt_project.yml"
LPI_VIEW = ("silver", "stg_worldbank_lpi")


@lru_cache(maxsize=1)
def dbt_vars() -> dict[str, Any]:
    with DBT_PROJECT.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)["vars"]


def lpi_available(connection: duckdb.DuckDBPyConnection) -> bool:
    return bool(
        connection.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_schema = ? AND table_name = ?",
            list(LPI_VIEW),
        ).fetchone()
    )


def build_quarter_metrics(connection: duckdb.DuckDBPyConnection, schema: str) -> int:
    """ds_<id>.quarter_metrics: int_supplier_quarter_metrics over the canonical table."""
    target = quote_schema(schema)
    variables = dbt_vars()
    connection.execute(f"DROP TABLE IF EXISTS {target}.quarter_metrics")
    connection.execute(
        f"""
        CREATE TABLE {target}.quarter_metrics AS
        WITH items AS (
            SELECT *, quarter AS quarter_start
            FROM {target}.silver_shipments
        ),
        data_bounds AS (
            SELECT min(event_date) AS first_order_date, max(event_date) AS last_order_date
            FROM items
        ),
        quarters AS (
            SELECT
                quarter_start,
                (quarter_start + INTERVAL 3 MONTH - INTERVAL 1 DAY)::DATE AS quarter_end,
                count(DISTINCT event_date) AS days_with_orders
            FROM items
            GROUP BY quarter_start
        ),
        quarter_flags AS (
            SELECT
                quarters.quarter_start,
                quarters.days_with_orders::DOUBLE
                    / (date_diff('day', quarters.quarter_start, quarters.quarter_end) + 1)
                    AS day_coverage,
                (
                    data_bounds.first_order_date > quarters.quarter_start
                    OR data_bounds.last_order_date < quarters.quarter_end
                    OR quarters.days_with_orders::DOUBLE
                        / (date_diff('day', quarters.quarter_start, quarters.quarter_end) + 1)
                        < $min_day_coverage
                ) AS partial_quarter
            FROM quarters
            CROSS JOIN data_bounds
        ),
        entity_quarters AS (
            SELECT
                entity_key,
                any_value(entity_name) AS entity_name,
                quarter_start,
                count(*) AS shipment_count,
                count(*) FILTER (WHERE is_cancelled) AS canceled_count,
                count(*) FILTER (WHERE NOT is_cancelled AND is_late) AS late_count,
                avg(delay_days) FILTER (WHERE NOT is_cancelled) AS avg_delay_days,
                stddev_samp(delay_days) FILTER (WHERE NOT is_cancelled) AS stddev_delay_days
            FROM items
            GROUP BY entity_key, quarter_start
        )
        SELECT
            entity_quarters.entity_key,
            entity_quarters.entity_name,
            entity_quarters.quarter_start,
            strftime(entity_quarters.quarter_start, '%Y') || '-Q'
                || quarter(entity_quarters.quarter_start) AS order_quarter,
            entity_quarters.shipment_count,
            entity_quarters.canceled_count,
            entity_quarters.late_count,
            entity_quarters.late_count::DOUBLE
                / nullif(entity_quarters.shipment_count - entity_quarters.canceled_count, 0)
                AS late_rate,
            entity_quarters.avg_delay_days,
            entity_quarters.stddev_delay_days,
            entity_quarters.shipment_count < $min_shipments AS low_volume,
            quarter_flags.partial_quarter,
            quarter_flags.day_coverage
        FROM entity_quarters
        INNER JOIN quarter_flags USING (quarter_start)
        """,
        {
            "min_day_coverage": float(variables["partial_quarter_min_day_coverage"]),
            "min_shipments": int(variables["min_quarter_shipments"]),
        },
    )
    return connection.execute(f"SELECT count(*) FROM {target}.quarter_metrics").fetchone()[0]


def build_mart(connection: duckdb.DuckDBPyConnection, schema: str, *, use_country: bool) -> int:
    """ds_<id>.mart_risk: mart_supplier_risk over the dataset's quarterly metrics.

    Without country risk (no country column, too few matches, or no LPI in the
    warehouse) country_risk and country_coverage are NULL and the scorer leaves
    that factor out.
    """
    target = quote_schema(schema)
    trailing = int(dbt_vars().get("country_risk_trailing_eligible_quarters", 4))
    eligible = f"""
        eligible_quarters AS (
            SELECT
                entity_key,
                entity_name,
                quarter_start,
                late_rate,
                avg_delay_days,
                stddev_delay_days AS delay_stddev,
                shipment_count,
                count(*) OVER (
                    PARTITION BY entity_key
                    ORDER BY quarter_start
                    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                ) AS eligible_quarters_to_date
            FROM {target}.quarter_metrics
            WHERE NOT low_volume
              AND NOT partial_quarter
              AND late_rate IS NOT NULL
        )"""
    connection.execute(f"DROP TABLE IF EXISTS {target}.mart_risk")
    if not use_country:
        connection.execute(
            f"""
            CREATE TABLE {target}.mart_risk AS
            WITH {eligible}
            SELECT
                entity_key, entity_name, quarter_start AS quarter, late_rate,
                avg_delay_days, delay_stddev, shipment_count, eligible_quarters_to_date,
                NULL::DOUBLE AS country_risk, NULL::DOUBLE AS country_coverage
            FROM eligible_quarters
            """
        )
    else:
        connection.execute(
            f"""
            CREATE TABLE {target}.mart_risk AS
            WITH {eligible},
            quarter_lpi AS (
                SELECT quarters.quarter_start, lpi.country_iso3, lpi.lpi_score
                FROM (SELECT DISTINCT quarter_start FROM eligible_quarters) AS quarters
                INNER JOIN silver.stg_worldbank_lpi AS lpi
                    ON lpi.lpi_year <= year(quarters.quarter_start)
                QUALIFY row_number() OVER (
                    PARTITION BY quarters.quarter_start, lpi.country_iso3
                    ORDER BY lpi.lpi_year DESC
                ) = 1
            ),
            quarter_lpi_median AS (
                SELECT quarter_start, median(lpi_score) AS median_lpi_score
                FROM quarter_lpi
                GROUP BY quarter_start
            ),
            entity_destination_shipments AS (
                SELECT
                    eligible.entity_key,
                    eligible.quarter_start,
                    eligible.eligible_quarters_to_date,
                    shipments.country_iso3,
                    count(*) AS shipment_count
                FROM eligible_quarters AS eligible
                INNER JOIN {target}.silver_shipments AS shipments
                    ON shipments.entity_key = eligible.entity_key
                    AND shipments.event_date >= eligible.quarter_start
                    AND shipments.event_date < eligible.quarter_start + INTERVAL 3 MONTH
                GROUP BY
                    eligible.entity_key,
                    eligible.quarter_start,
                    eligible.eligible_quarters_to_date,
                    shipments.country_iso3
            ),
            quarterly_destination_risk AS (
                SELECT
                    destinations.entity_key,
                    destinations.quarter_start,
                    destinations.eligible_quarters_to_date,
                    sum(
                        destinations.shipment_count
                        * (5 - coalesce(lpi.lpi_score, median_lpi.median_lpi_score)) / 4
                    ) AS weighted_country_risk,
                    sum(destinations.shipment_count) AS shipment_count,
                    sum(destinations.shipment_count) FILTER (
                        WHERE lpi.lpi_score IS NOT NULL
                    ) AS lpi_shipment_count
                FROM entity_destination_shipments AS destinations
                LEFT JOIN quarter_lpi AS lpi
                    ON lpi.quarter_start = destinations.quarter_start
                    AND lpi.country_iso3 = destinations.country_iso3
                INNER JOIN quarter_lpi_median AS median_lpi
                    ON median_lpi.quarter_start = destinations.quarter_start
                GROUP BY
                    destinations.entity_key,
                    destinations.quarter_start,
                    destinations.eligible_quarters_to_date
            ),
            trailing_destination_risk AS (
                SELECT
                    current.entity_key,
                    current.quarter_start,
                    sum(history.weighted_country_risk) / sum(history.shipment_count)
                        AS country_risk,
                    sum(history.lpi_shipment_count)::DOUBLE / sum(history.shipment_count)
                        AS country_coverage
                FROM eligible_quarters AS current
                INNER JOIN quarterly_destination_risk AS history
                    ON history.entity_key = current.entity_key
                    AND history.eligible_quarters_to_date BETWEEN greatest(
                        current.eligible_quarters_to_date - $trailing + 1, 1
                    ) AND current.eligible_quarters_to_date
                GROUP BY current.entity_key, current.quarter_start
            )
            SELECT
                eligible.entity_key,
                eligible.entity_name,
                eligible.quarter_start AS quarter,
                eligible.late_rate,
                eligible.avg_delay_days,
                eligible.delay_stddev,
                eligible.shipment_count,
                eligible.eligible_quarters_to_date,
                risk.country_risk,
                coalesce(risk.country_coverage, 0) AS country_coverage
            FROM eligible_quarters AS eligible
            INNER JOIN trailing_destination_risk AS risk
                ON risk.entity_key = eligible.entity_key
                AND risk.quarter_start = eligible.quarter_start
            """,
            {"trailing": trailing},
        )
    return connection.execute(f"SELECT count(*) FROM {target}.mart_risk").fetchone()[0]
