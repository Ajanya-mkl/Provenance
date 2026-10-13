from pathlib import Path

import yaml
from dagster import (
    AssetSelection,
    DefaultScheduleStatus,
    Definitions,
    ScheduleDefinition,
    define_asset_job,
    in_process_executor,
)

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
from provenance.dags.trigger_agent_investigation import (
    agent_investigation_job,
    agent_investigation_sensor,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DUCKDB_WRITER_TAGS = {"dagster/concurrency_key": "duckdb_writer"}

ingest_all_sources = define_asset_job(
    "ingest_all_sources",
    selection=AssetSelection.assets(dataco_shipments, usaspending_contracts, worldbank_lpi),
    executor_def=in_process_executor,
    tags=DUCKDB_WRITER_TAGS,
)

bronze_quality_gates = define_asset_job(
    "bronze_quality_gates",
    selection=AssetSelection.assets(
        gate_dataco_shipments,
        gate_usaspending_contracts,
        gate_worldbank_lpi,
    ),
    executor_def=in_process_executor,
    tags=DUCKDB_WRITER_TAGS,
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
    tags=DUCKDB_WRITER_TAGS,
)

# ingestion -> gates -> silver dbt -> gold dbt -> scorer; asset deps fix the order.
provenance_full_pipeline = define_asset_job(
    "provenance_full_pipeline",
    selection=AssetSelection.all(),
    executor_def=in_process_executor,
    tags=DUCKDB_WRITER_TAGS,
)

# Ad hoc "Materialize" clicks in the UI use this job; tag it so they queue too.
implicit_global_asset_job = define_asset_job(
    "__ASSET_JOB",
    selection=AssetSelection.all(),
    executor_def=in_process_executor,
    tags=DUCKDB_WRITER_TAGS,
)

with (PROJECT_ROOT / "config" / "config.example.yaml").open(encoding="utf-8") as config_file:
    _project_config = yaml.safe_load(config_file)
WEEKLY_PIPELINE_CRON = _project_config["orchestration"]["weekly_pipeline_cron"]

provenance_full_pipeline_schedule = ScheduleDefinition(
    name="provenance_full_pipeline_weekly",
    job=provenance_full_pipeline,
    cron_schedule=WEEKLY_PIPELINE_CRON,
    default_status=DefaultScheduleStatus.STOPPED,
    execution_timezone="UTC",
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
        gates_and_silver,
        provenance_full_pipeline,
        implicit_global_asset_job,
        agent_investigation_job,
    ],
    schedules=[provenance_full_pipeline_schedule],
    sensors=[agent_investigation_sensor],
    resources={"dbt": dbt_resource},
)
