"""Small warehouse used by the agent tests (no network, no real data)."""

import json

import duckdb

from provenance.data_quality.gate import ensure_agent_events_table

EVENT_ID = "risk_flag:1.2:2017-01-01:cat_widgets"
EARLIER_EVENT_ID = "risk_flag:1.2:2016-10-01:cat_widgets"
FACTORS = {
    "delay_stddev": {
        "raw_value": 1.61483,
        "normalized_value": 0.86977,
        "weight": 0.35,
        "weighted_contribution": 0.30442,
    },
    "late_rate": {
        "raw_value": 0.63121,
        "normalized_value": 0.93718,
        "weight": 0.25,
        "weighted_contribution": 0.2343,
    },
}


def build_warehouse(db_path):
    with duckdb.connect(str(db_path)) as connection:
        for schema in ("silver", "gold", "agent"):
            connection.execute(f"CREATE SCHEMA {schema}")
        ensure_agent_events_table(connection)
        payload = {
            "supplier_key": "cat_widgets",
            "quarter": "2017-01-01",
            "old_tier": "medium",
            "new_tier": "high",
            "score": 0.734948,
            "confidence": "full",
            "top_contributing_factor": {"name": "delay_stddev", **FACTORS["delay_stddev"]},
        }
        earlier = {**payload, "quarter": "2016-10-01", "old_tier": "low", "new_tier": "medium"}
        connection.execute(
            """
            INSERT INTO agent.events
                (event_id, event_type, source, batch_id, created_at, payload,
                 investigation_status)
            VALUES (?, 'risk_flag', 'test', 'b1', now(), ?, 'queued'),
                   (?, 'risk_flag', 'test', 'b0', now() - INTERVAL 1 DAY, ?, 'queued'),
                   ('dq_failure:1', 'dq_failure', 'test', 'b1', now(), '{}', 'open')
            """,
            [EVENT_ID, json.dumps(payload), EARLIER_EVENT_ID, json.dumps(earlier)],
        )
        connection.execute(
            """
            CREATE TABLE silver.dim_supplier_scd2 (
                supplier_key VARCHAR, supplier_name VARCHAR, delivery_band VARCHAR,
                volume_band VARCHAR, row_hash VARCHAR, effective_from DATE,
                effective_to DATE, is_current BOOLEAN, version_number INTEGER
            )
            """
        )
        connection.execute(
            "INSERT INTO silver.dim_supplier_scd2 VALUES "
            "('cat_widgets', 'Widgets', 'low', 'medium', 'a', '2015-01-01', '2016-01-01', "
            "false, 1), "
            "('cat_widgets', 'Widgets', 'medium', 'medium', 'b', '2016-01-01', NULL, true, 2)"
        )
        connection.execute(
            """
            CREATE TABLE gold.supplier_risk_score (
                supplier_key VARCHAR, quarter DATE, score DOUBLE, tier VARCHAR,
                contributing_factors JSON, confidence VARCHAR, rules_version VARCHAR
            )
            """
        )
        connection.execute(
            "INSERT INTO gold.supplier_risk_score VALUES "
            "('cat_widgets', '2016-10-01', 0.39, 'medium', ?, 'full', '1.2'), "
            "('cat_widgets', '2017-01-01', 0.734948, 'high', ?, 'full', '1.2')",
            [json.dumps(FACTORS), json.dumps(FACTORS)],
        )
        connection.execute(
            """
            CREATE TABLE gold.mart_supplier_risk (
                supplier_key VARCHAR, quarter DATE, late_rate DOUBLE,
                avg_delay_days DOUBLE, delay_stddev DOUBLE, shipment_count BIGINT,
                eligible_quarters_to_date BIGINT, country_risk DOUBLE,
                country_coverage DOUBLE
            )
            """
        )
        connection.execute(
            "INSERT INTO gold.mart_supplier_risk VALUES "
            "('cat_widgets', '2017-01-01', 0.63121, 0.709, 1.61483, 4, 9, 0.42302, 0.75)"
        )
        connection.execute(
            """
            CREATE TABLE silver.fact_shipments (
                supplier_key VARCHAR, order_date DATE, order_country_iso3 VARCHAR,
                market VARCHAR
            )
            """
        )
        connection.execute(
            "INSERT INTO silver.fact_shipments VALUES "
            "('cat_widgets', '2017-01-05', 'MEX', 'LATAM'), "
            "('cat_widgets', '2017-02-05', 'MEX', 'LATAM'), "
            "('cat_widgets', '2017-03-05', 'MEX', 'LATAM'), "
            "('cat_widgets', '2017-03-06', 'XXX', 'Europe'), "
            "('cat_widgets', '2016-11-01', 'XXX', 'Europe'), "
            "('cat_widgets', '2017-04-01', 'MEX', 'LATAM')"
        )
        connection.execute(
            "CREATE TABLE silver.stg_worldbank_lpi "
            "(country_iso3 VARCHAR, lpi_year INTEGER, lpi_score DOUBLE)"
        )
        connection.execute(
            "INSERT INTO silver.stg_worldbank_lpi VALUES "
            "('MEX', 2014, 3.0), ('MEX', 2016, 3.0), ('DEU', 2016, 4.0), ('MEX', 2018, 1.0)"
        )
    return db_path
