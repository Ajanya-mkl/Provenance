import json
from collections.abc import Iterator, Mapping
from typing import Any

from dagster import AssetExecutionContext
from dagster_dbt import DagsterDbtTranslator, DbtCliResource, dbt_assets

from provenance.dags.silver_assets import TRANSFORM_DIR, dbt_project
from provenance.risk_engine.scorer import load_rules


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
    rules = load_rules()
    dbt_vars = json.dumps(
        {
            "country_risk_trailing_eligible_quarters": rules[
                "country_risk_trailing_eligible_quarters"
            ]
        }
    )
    yield from dbt.cli(
        [
            "build",
            "--project-dir",
            str(TRANSFORM_DIR),
            "--profiles-dir",
            str(TRANSFORM_DIR),
            "--vars",
            dbt_vars,
        ],
        context=context,
    ).stream()