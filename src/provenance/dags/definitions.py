from dagster import AssetSelection, Definitions, define_asset_job, in_process_executor

from provenance.dags.ingestion_assets import (
    dataco_shipments,
    gate_dataco_shipments,
    gate_usaspending_contracts,
    gate_worldbank_lpi,
    usaspending_contracts,
    worldbank_lpi,
)

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

defs = Definitions(
    assets=[
        dataco_shipments,
        usaspending_contracts,
        worldbank_lpi,
        gate_dataco_shipments,
        gate_usaspending_contracts,
        gate_worldbank_lpi,
    ],
    jobs=[ingest_all_sources, bronze_quality_gates, ingest_and_validate_all_sources],
)
