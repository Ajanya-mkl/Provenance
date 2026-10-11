from dagster import AssetSelection, Definitions, define_asset_job, in_process_executor

from provenance.dags.ingestion_assets import (
    dataco_shipments,
    usaspending_contracts,
    worldbank_lpi,
)

ingest_all_sources = define_asset_job(
    "ingest_all_sources",
    selection=AssetSelection.assets(dataco_shipments, usaspending_contracts, worldbank_lpi),
    executor_def=in_process_executor,
)

defs = Definitions(
    assets=[dataco_shipments, usaspending_contracts, worldbank_lpi],
    jobs=[ingest_all_sources],
)
