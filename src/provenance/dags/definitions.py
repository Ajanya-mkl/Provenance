from dagster import AssetSelection, Definitions, define_asset_job, in_process_executor

from provenance.dags.build_gold_marts import gold_dbt_assets
from provenance.dags.ingestion_assets import (
    dataco_shipments,
    gate_dataco_shipments,
    gate_usaspending_contracts,
    gate_worldbank_lpi,
    usaspending_contracts,
    worldbank_lpi,
)
from provenance.dags.run_risk_scoring import supplier_risk_scoring_asset
from provenance.dags.silver_assets import dbt_resource, silver_dbt_assets

ingest_all_sources = define_asset_job(
    "ingest_all_sources",
    selection=AssetSelection.assets(dataco_shipments, usaspending_contracts, worldbank_lpi),
    executor_def=in_process_executor,
)

bronze_quality_gates = define_asset_job(
    "bronze_quality_gates",
    selection=AssetSelection.assets(
        gate_dataco_shipments,
        gate_usaspending_contracts,
        gate_worldbank_lpi,
    ),
    executor_def=in_process_executor,
)

ingest_and_validate_all_sources = define_asset_job(
    "ingest_and_validate_all_sources",
    selection=AssetSelection.assets(
        dataco_shipments,
        usaspending_contracts,
        worldbank_lpi,
        gate_dataco_shipments,
        gate_usaspending_contracts,
        gate_worldbank_lpi,
    ),
    executor_def=in_process_executor,
)

gates_and_silver = define_asset_job(
    "gates_and_silver",
    selection=AssetSelection.assets(
        gate_dataco_shipments,
        gate_usaspending_contracts,
        gate_worldbank_lpi,
        silver_dbt_assets,
    ),
    executor_def=in_process_executor,
)

phase5_risk_pipeline = define_asset_job(
    "phase5_risk_pipeline",
    selection=AssetSelection.assets(
        gate_dataco_shipments,
        gate_usaspending_contracts,
        gate_worldbank_lpi,
        silver_dbt_assets,
        gold_dbt_assets,
        supplier_risk_scoring_asset,
    ),
    executor_def=in_process_executor,
)

defs = Definitions(
    assets=[
        dataco_shipments,
        usaspending_contracts,
        worldbank_lpi,
        gate_dataco_shipments,
        gate_usaspending_contracts,
        gate_worldbank_lpi,
        silver_dbt_assets,
        gold_dbt_assets,
        supplier_risk_scoring_asset,
    ],
    jobs=[
        ingest_all_sources,
        bronze_quality_gates,
        ingest_and_validate_all_sources,
        gates_and_silver,
        phase5_risk_pipeline,
    ],
    resources={"dbt": dbt_resource},
)
