# No `from __future__ import annotations`: Dagster inspects the real type of
# the `context` annotation on the asset function.
import os
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from dagster import AssetExecutionContext, AssetKey
from dagster_dbt import DagsterDbtTranslator, DbtCliResource, DbtProject, dbt_assets

PROJECT_ROOT = Path(__file__).resolve().parents[3]
TRANSFORM_DIR = PROJECT_ROOT / "src" / "provenance" / "transform"
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "provenance.duckdb"

# dbt runs from the transform folder, so the warehouse path must be absolute.
os.environ.setdefault("PROVENANCE_DUCKDB_PATH", str(DEFAULT_DB_PATH))

# Each bronze table is consumed only after its Phase 3 gate asset succeeds.
# dq_results keeps its own key (bronze/dq_results): the gates write it, and
# all three gates are already upstream through the tables above.
SOURCE_GATES = {
    "dataco_shipments": AssetKey("gate_dataco_shipments"),
    "usaspending_contracts": AssetKey("gate_usaspending_contracts"),
    "worldbank_lpi": AssetKey("gate_worldbank_lpi"),
}

dbt_project = DbtProject(project_dir=TRANSFORM_DIR, profiles_dir=TRANSFORM_DIR)
if not dbt_project.manifest_path.exists():
    dbt_project.preparer.prepare(dbt_project)
dbt_project.prepare_if_dev()


class GatedSilverTranslator(DagsterDbtTranslator):
    def get_asset_key(self, dbt_resource_props: Mapping[str, Any]) -> AssetKey:
        if (
            dbt_resource_props["resource_type"] == "source"
            and dbt_resource_props["name"] in SOURCE_GATES
        ):
            return SOURCE_GATES[dbt_resource_props["name"]]
        return super().get_asset_key(dbt_resource_props)

    def get_group_name(self, dbt_resource_props: Mapping[str, Any]) -> str | None:
        return "silver"


@dbt_assets(
    manifest=dbt_project.manifest_path,
    project=dbt_project,
    dagster_dbt_translator=GatedSilverTranslator(),
)
def silver_dbt_assets(context: AssetExecutionContext, dbt: DbtCliResource) -> Iterator[Any]:
    yield from dbt.cli(["build"], context=context).stream()


dbt_resource = DbtCliResource(project_dir=dbt_project)
