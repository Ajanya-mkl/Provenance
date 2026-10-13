from __future__ import annotations

from typing import Any

import pytest
from dagster import AssetKey

from provenance.data_quality.gate import GateResult

GATE_KEYS = {
    AssetKey("gate_dataco_shipments"),
    AssetKey("gate_usaspending_contracts"),
    AssetKey("gate_worldbank_lpi"),
}


def test_silver_reads_bronze_only_through_gate_assets() -> None:
    from provenance.dags.silver_assets import silver_dbt_assets

    upstream = {
        dep.asset_key
        for spec in silver_dbt_assets.specs
        for dep in spec.deps
        if dep.asset_key not in silver_dbt_assets.keys
    }
    assert upstream == GATE_KEYS | {AssetKey(["bronze", "dq_results"])}
    assert AssetKey("dim_supplier_scd2") in silver_dbt_assets.keys
    assert AssetKey("fact_shipments") in silver_dbt_assets.keys


def test_dataco_gate_failure_skips_silver(monkeypatch: pytest.MonkeyPatch) -> None:
    from provenance.dags import ingestion_assets
    from provenance.dags.definitions import defs

    failed_result = GateResult(
        passed=False,
        batch_id="synthetic-failure",
        source="dataco_shipments",
        passed_expectations=49,
        failed_expectations=1,
        failed_details=[],
        result_json={"passed": False},
    )

    def fail_dataco_gate(source: str, **kwargs: Any) -> list[GateResult]:
        assert source == "dataco_shipments"
        return [failed_result]

    monkeypatch.setattr(ingestion_assets, "validate_source_batches", fail_dataco_gate)
    result = defs.resolve_job_def("gates_and_silver").execute_in_process(raise_on_error=False)
    step_events = {
        event.step_key: event.event_type_value
        for event in result.all_events
        if event.step_key is not None
    }

    assert not result.success
    assert step_events["gate_dataco_shipments"] == "STEP_FAILURE"
    assert not any(key.startswith("silver_dbt_assets") for key in step_events)
