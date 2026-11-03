"""Phase 10c key correctness test: DataCo through the generic path equals the flagship.

DataCo is onboarded as an ordinary dataset into a COPY of the local warehouse,
mapped through the contract the way the flagship reads it, approved, and scored
with bounds_mode='flagship'. Its mart, scores, tiers and flags must match the
flagship's gold tables in the same copy (scores within 1e-9), and the flagship
tables must be untouched. The real warehouse is only read (copied).

Skips cleanly when the DataCo CSV or the warehouse is absent (CI has neither).
It takes a few minutes: profiling and gating 180k rows x 53 columns dominates.
"""

from __future__ import annotations

import shutil

import duckdb
import pytest

from provenance.datasets import mapping_store
from provenance.datasets.mapping_agent import propose_mapping
from provenance.datasets.onboard import PROJECT_ROOT, Paths, onboard
from provenance.datasets.score import score_dataset
from provenance.datasets.validator import load_context

pytestmark = pytest.mark.integration

DATACO_CSV = PROJECT_ROOT / "data" / "raw" / "DataCoSupplyChainDataset.csv"
WAREHOUSE = PROJECT_ROOT / "data" / "provenance.duckdb"
TOLERANCE = 1e-9

# The flagship's reading of DataCo, expressed in the contract (stg_dataco_order_items,
# fact_shipments): Late_delivery_risk equals real > scheduled days on every row that
# was not cancelled, and cancelled rows count as shipments but not toward lateness.
DATACO_MAPPING = [
    "entity=Category Name",
    "event_date.col=order date (DateOrders)",
    "event_date.format=%m/%d/%Y %H:%M",
    "outcome.kind=days_pair",
    "outcome.scheduled_col=Days for shipment (scheduled)",
    "outcome.actual_col=Days for shipping (real)",
    "outcome.unit=days",
    "country=Order Country",
    "cancelled.col=Delivery Status",
    "cancelled.value=Shipping canceled",
]


def _rows(connection, sql):
    return connection.execute(sql).fetchall()


@pytest.fixture(scope="module")
def scored(tmp_path_factory):
    if not DATACO_CSV.exists():
        pytest.skip("DataCo CSV not present (data/raw/DataCoSupplyChainDataset.csv)")
    if not WAREHOUSE.exists():
        pytest.skip("No local warehouse to copy (data/provenance.duckdb)")
    root = tmp_path_factory.mktemp("equivalence")
    # dbt views name their catalog after the file, so the copy keeps the file name.
    copy = root / "provenance.duckdb"
    try:
        shutil.copy(WAREHOUSE, copy)
        wal = WAREHOUSE.with_name(WAREHOUSE.name + ".wal")
        if wal.exists():
            shutil.copy(wal, copy.with_name(copy.name + ".wal"))
    except OSError as error:
        pytest.skip(f"Could not copy the warehouse (is a pipeline run holding it?): {error}")

    def flagship_snapshot():
        with duckdb.connect(str(copy), read_only=True) as connection:
            return {
                "scores": _rows(connection, "SELECT * FROM gold.supplier_risk_score ORDER BY ALL"),
                "flags": _rows(connection, "SELECT * FROM gold.supplier_risk_flags ORDER BY ALL"),
                "events": _rows(
                    connection,
                    "SELECT event_id, payload::VARCHAR, resolved_at, resolution "
                    "FROM agent.events WHERE source = 'risk_engine' ORDER BY ALL",
                ),
            }

    before = flagship_snapshot()
    paths = Paths(copy, root / "datasets", root / "quarantine")
    onboarded = onboard(DATACO_CSV, "DataCo via the contract", paths=paths, max_bytes=200_000_000)
    assert onboarded.status == "gated", (onboarded.message, onboarded.problems)
    dataset_id = onboarded.dataset_id

    context = load_context(copy, dataset_id)
    proposal = propose_mapping(context, heuristic_only=True)
    del context
    first = mapping_store.record_proposal(copy, dataset_id, proposal, created_by="heuristic")
    mapping_id, validation = mapping_store.edit(
        copy, first, mapping_store.parse_sets(DATACO_MAPPING), by="equivalence-test"
    )
    assert validation.ok and validation.scoreable.ok, validation.as_dict()
    mapping_store.approve(copy, mapping_id, by="equivalence-test")
    result = score_dataset(copy, dataset_id, bounds_mode="flagship")
    return {
        "db": copy,
        "dataset_id": dataset_id,
        "result": result,
        "before": before,
        "after": flagship_snapshot(),
    }


def test_canonical_silver_matches_fact_shipments(scored):
    schema = f"ds_{scored['dataset_id']}"
    with duckdb.connect(str(scored["db"]), read_only=True) as connection:
        generic = _rows(
            connection,
            f"""
            SELECT 'cat_' || entity_key, event_date, count(*), count(*) FILTER (WHERE is_late),
                   count(*) FILTER (WHERE is_cancelled), count(country_iso3)
            FROM {schema}.silver_shipments GROUP BY ALL ORDER BY ALL
            """,
        )
        flagship = _rows(
            connection,
            """
            SELECT supplier_key, order_date, count(*),
                   count(*) FILTER (WHERE days_real > days_scheduled),
                   count(*) FILTER (WHERE delivery_status = 'Shipping canceled'),
                   count(order_country_iso3)
            FROM silver.fact_shipments GROUP BY ALL ORDER BY ALL
            """,
        )
    assert scored["result"].silver.rows_out == scored["result"].silver.rows_in
    assert generic == flagship


def test_mart_metrics_match(scored):
    schema = f"ds_{scored['dataset_id']}"
    with duckdb.connect(str(scored["db"]), read_only=True) as connection:
        mismatches = _rows(
            connection,
            f"""
            SELECT coalesce(g.supplier_key, 'cat_' || d.entity_key), coalesce(g.quarter, d.quarter)
            FROM gold.mart_supplier_risk AS g
            FULL OUTER JOIN {schema}.mart_risk AS d
                ON g.supplier_key = 'cat_' || d.entity_key AND g.quarter = d.quarter
            WHERE g.supplier_key IS NULL OR d.entity_key IS NULL
               OR g.eligible_quarters_to_date <> d.eligible_quarters_to_date
               OR g.shipment_count <> d.shipment_count
               OR abs(g.late_rate - d.late_rate) > {TOLERANCE}
               OR abs(g.delay_stddev - d.delay_stddev) > {TOLERANCE}
               OR abs(g.country_risk - d.country_risk) > {TOLERANCE}
               OR abs(g.country_coverage - d.country_coverage) > {TOLERANCE}
            """,
        )
    assert mismatches == []


def test_scores_and_tiers_match_within_1e_9(scored):
    schema = f"ds_{scored['dataset_id']}"
    with duckdb.connect(str(scored["db"]), read_only=True) as connection:
        counts = _rows(
            connection,
            f"SELECT (SELECT count(*) FROM gold.supplier_risk_score), "
            f"(SELECT count(*) FROM {schema}.risk_score)",
        )[0]
        worst, tier_mismatches, confidence_mismatches = _rows(
            connection,
            f"""
            SELECT max(abs(g.score - d.score)),
                   count(*) FILTER (WHERE g.tier <> d.tier),
                   count(*) FILTER (WHERE g.confidence <> d.confidence)
            FROM gold.supplier_risk_score AS g
            INNER JOIN {schema}.risk_score AS d
                ON g.supplier_key = 'cat_' || d.entity_key AND g.quarter = d.quarter
            """,
        )[0]
    print(
        f"\nscores: flagship {counts[0]}, generic {counts[1]}; max |diff| {worst!r}; "
        f"tier mismatches {tier_mismatches}; confidence mismatches {confidence_mismatches}"
    )
    assert counts[0] == counts[1] > 0
    assert worst <= TOLERANCE, worst
    assert tier_mismatches == 0 and confidence_mismatches == 0


def test_flag_set_is_identical(scored):
    schema = f"ds_{scored['dataset_id']}"
    with duckdb.connect(str(scored["db"]), read_only=True) as connection:
        flagship = _rows(
            connection,
            "SELECT supplier_key, quarter, old_tier, new_tier FROM gold.supplier_risk_flags "
            "WHERE coalesce(status, 'active') = 'active' ORDER BY ALL",
        )
        generic = _rows(
            connection,
            f"SELECT 'cat_' || entity_key, quarter, old_tier, new_tier FROM {schema}.risk_flags "
            "WHERE status = 'active' ORDER BY ALL",
        )
        prefixed = _rows(
            connection,
            "SELECT count(*), count(*) FILTER (WHERE starts_with(event_id, "
            f"'ds_{scored['dataset_id']}:risk_flag:')) FROM agent.events "
            "WHERE source = 'dataset_risk_engine'",
        )[0]
    print(f"\nactive flags: flagship {len(flagship)}, generic {len(generic)}")
    assert generic == flagship and len(flagship) > 0
    assert prefixed[0] == prefixed[1] == len(generic)


def test_flagship_outputs_and_event_ids_unchanged(scored):
    assert scored["after"] == scored["before"]
