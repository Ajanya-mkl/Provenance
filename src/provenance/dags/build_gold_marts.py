from collections.abc import Iterator, Mapping
from typing import Any

from dagster import AssetExecutionContext
from dagster_dbt import DagsterDbtTranslator, DbtCliResource, dbt_assets

from provenance.dags.silver_assets import dbt_project


class GoldDbtTranslator(DagsterDbtTranslator):
    def get_group_name(self, _dbt_resource_props: Mapping[str, Any]) -> str | None:
        return "gold"


@dbt_assets(
    select="path:models/gold",
    manifest=dbt_project.manifest_path,
    project=dbt_project,
    dagster_dbt_translator=GoldDbtTranslator(),
)
def gold_dbt_assets(context: AssetExecutionContext, dbt: DbtCliResource) -> Iterator[Any]:
    yield from dbt.cli(["build"], context=context).stream()