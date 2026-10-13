from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pytest
from dagster import (
    AssetKey,
    Backoff,
    DefaultScheduleStatus,
    DefaultSensorStatus,
    RunRequest,
    SkipReason,
)

from provenance.dags import ingestion_assets
from provenance.dags.definitions import (
    defs,
    provenance_full_pipeline_schedule,
)
from provenance.dags.ingestion_assets import API_MAX_RETRIES
from provenance.dags.trigger_agent_investigation import (
    agent_investigation_job,
    agent_investigation_sensor,
    build_investigation_request,
)
from provenance.data_quality.gate import GateResult, ensure_agent_events_table


def test_full_pipeline_asset_order_and_selected_assets() -> None:
    job = defs.resolve_job_def("provenance_full_pipeline")
    selected = job.asset_layer.selected_asset_keys
    expected = {
        AssetKey("dataco_shipments"),
        AssetKey("usaspending_contracts"),
        AssetKey("worldbank_lpi"),
        AssetKey("gate_dataco_shipments"),
        AssetKey("gate_usaspending_contracts"),
        AssetKey("gate_worldbank_lpi"),
        AssetKey("dim_supplier_scd2"),
        AssetKey(["gold", "mart_supplier_risk"]),
        AssetKey(["gold", "mart_shipment_delay"]),
        AssetKey("supplier_risk_scoring"),
    }
    assert expected <= selected

    graph = defs.resolve_asset_graph()
    # ingestion -> gates -> silver dbt -> gold dbt -> scorer, edge by edge.
    must_run_before = [
        ("dataco_shipments", "usaspending_contracts"),
        ("usaspending_contracts", "worldbank_lpi"),
        ("worldbank_lpi", "gate_dataco_shipments"),
        ("gate_dataco_shipments", "gate_usaspending_contracts"),
        ("gate_usaspending_contracts", "gate_worldbank_lpi"),
        ("gate_dataco_shipments", "fact_shipments"),
        ("gate_worldbank_lpi", "stg_worldbank_lpi"),
        ("fact_shipments", "gold/mart_supplier_risk"),
        ("dim_supplier_scd2", "gold/mart_supplier_risk"),
        ("stg_worldbank_lpi", "gold/mart_supplier_risk"),
        ("gold/mart_supplier_risk", "supplier_risk_scoring"),
    ]
    for earlier, later in must_run_before:
        earlier_key = AssetKey.from_user_string(earlier)
        later_key = AssetKey.from_user_string(later)
        assert earlier_key in graph.get_ancestor_asset_keys(later_key), (earlier, later)

    assert graph.get(AssetKey("usaspending_contracts")).parent_keys == {
        AssetKey("dataco_shipments")
    }
    assert graph.get(AssetKey("worldbank_lpi")).parent_keys == {
        AssetKey("usaspending_contracts")
    }
    assert graph.get(AssetKey("gate_dataco_shipments")).parent_keys == {
        AssetKey("dataco_shipments"),
        AssetKey("usaspending_contracts"),
        AssetKey("worldbank_lpi"),
    }
    assert graph.get(AssetKey("gate_usaspending_contracts")).parent_keys == {
        AssetKey("usaspending_contracts"),
        AssetKey("gate_dataco_shipments"),
    }
    assert graph.get(AssetKey("gate_worldbank_lpi")).parent_keys == {
        AssetKey("worldbank_lpi"),
        AssetKey("gate_usaspending_contracts"),
    }
    assert graph.get(AssetKey("supplier_risk_scoring")).parent_keys == {
        AssetKey(["gold", "mart_supplier_risk"])
    }
    assert job.tags["dagster/concurrency_key"] == "duckdb_writer"
    assert (
        defs.resolve_implicit_global_asset_job_def().tags[
            "dagster/concurrency_key"
        ]
        == "duckdb_writer"
    )


def test_retry_policy_is_limited_to_api_ingestion_assets() -> None:
    api_assets = (ingestion_assets.usaspending_contracts, ingestion_assets.worldbank_lpi)
    for api_asset in api_assets:
        policy = api_asset.op.retry_policy
        assert policy is not None
        assert policy.max_retries == API_MAX_RETRIES == 3
        assert policy.backoff == Backoff.EXPONENTIAL

    assert ingestion_assets.dataco_shipments.op.retry_policy is None
    for asset_def in (
        ingestion_assets.gate_dataco_shipments,
        ingestion_assets.gate_usaspending_contracts,
        ingestion_assets.gate_worldbank_lpi,
    ):
        assert asset_def.op.retry_policy is None


def test_full_pipeline_gate_failure_skips_all_downstream_assets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ingestion_assets, "load_dataco_shipments", lambda **_kwargs: 1)
    monkeypatch.setattr(
        ingestion_assets, "load_usaspending_contracts", lambda **_kwargs: 1
    )
    monkeypatch.setattr(ingestion_assets, "load_worldbank_lpi", lambda **_kwargs: 1)
    failure = GateResult(
        passed=False,
        batch_id="broken-copy",
        source="dataco_shipments",
        passed_expectations=4,
        failed_expectations=1,
        failed_details=[],
        result_json={"passed": False},
    )
    monkeypatch.setattr(
        ingestion_assets,
        "validate_source_batches",
        lambda source, **_kwargs: [failure] if source == "dataco_shipments" else [],
    )

    result = defs.resolve_job_def("provenance_full_pipeline").execute_in_process(
        raise_on_error=False
    )
    step_states = {
        event.step_key: event.event_type_value
        for event in result.all_events
        if event.step_key is not None
        and event.event_type_value
        in {"STEP_FAILURE", "STEP_SKIPPED", "STEP_SUCCESS"}
    }
    assert not result.success
    assert step_states["gate_dataco_shipments"] == "STEP_FAILURE"
    started_steps = {
        event.step_key
        for event in result.all_events
        if event.step_key is not None and event.event_type_value == "STEP_START"
    }
    assert {
        "gate_usaspending_contracts",
        "gate_worldbank_lpi",
        "silver_dbt_assets",
        "gold_dbt_assets",
        "supplier_risk_scoring",
    }.isdisjoint(started_steps)


def _events_database(tmp_path: Path) -> Path:
    database = tmp_path / "events.duckdb"
    now = datetime.now(UTC)
    with duckdb.connect(str(database)) as connection:
        connection.execute("CREATE SCHEMA agent")
        # Pre-Phase 6 shape: no investigation_status column yet.
        connection.execute(
            """
            CREATE TABLE agent.events (
                event_id VARCHAR PRIMARY KEY,
                event_type VARCHAR NOT NULL,
                source VARCHAR NOT NULL,
                batch_id VARCHAR NOT NULL,
                created_at TIMESTAMPTZ NOT NULL,
                payload JSON NOT NULL,
                resolved_at TIMESTAMPTZ,
                resolution VARCHAR
            )
            """
        )
        connection.execute(
            """
            INSERT INTO agent.events VALUES
            ('risk-open', 'risk_flag', 'risk_engine', 'one', ?, '{"score": 80}', NULL, NULL),
            ('dq-demo', 'dq_failure', 'dataco_shipments', 'two', ?, '{"demo": true}', NULL, NULL),
            ('resolved', 'risk_flag', 'risk_engine', 'three', ?, '{}', ?, 'superseded'),
            ('other-type', 'note', 'test', 'four', ?, '{}', NULL, NULL)
            """,
            [now, now, now, now, now],
        )
    return database


def _statuses(database: Path) -> dict[str, str]:
    with duckdb.connect(str(database)) as connection:
        return dict(
            connection.execute(
                "SELECT event_id, investigation_status FROM agent.events"
            ).fetchall()
        )


def test_events_migration_adds_open_investigation_status(tmp_path: Path) -> None:
    database = _events_database(tmp_path)
    with duckdb.connect(str(database)) as connection:
        ensure_agent_events_table(connection)
        ensure_agent_events_table(connection)
    assert set(_statuses(database).values()) == {"open"}


def test_sensor_queues_only_open_unresolved_events_once(tmp_path: Path) -> None:
    database = _events_database(tmp_path)

    request = build_investigation_request(database)

    assert isinstance(request, RunRequest)
    cases = request.run_config["ops"]["log_investigation_cases"]["config"]["cases"]
    assert {case["event_id"] for case in cases} == {"risk-open", "dq-demo"}
    assert next(c for c in cases if c["event_id"] == "dq-demo")["payload"] == {"demo": True}
    assert _statuses(database) == {
        "risk-open": "queued",
        "dq-demo": "queued",
        "resolved": "open",
        "other-type": "open",
    }
    run = agent_investigation_job.execute_in_process(run_config=request.run_config)
    assert run.output_for_node("log_investigation_cases") == 2

    second = build_investigation_request(database)
    assert isinstance(second, SkipReason)
    assert _statuses(database)["risk-open"] == "queued"


def test_sensor_skips_when_warehouse_is_locked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from provenance.dags import trigger_agent_investigation

    def locked(_db_path: object) -> None:
        raise duckdb.IOException("Could not set lock on file")

    monkeypatch.setattr(trigger_agent_investigation, "queue_open_events", locked)
    assert isinstance(build_investigation_request(tmp_path / "x.duckdb"), SkipReason)


def test_repeat_gate_failure_does_not_requeue_queued_event(tmp_path: Path) -> None:
    from provenance.data_quality.gate import _record_result

    database = tmp_path / "gate.duckdb"
    failure = GateResult(
        passed=False,
        batch_id="broken",
        source="dataco_shipments",
        passed_expectations=4,
        failed_expectations=1,
        failed_details=[],
        result_json={
            "source": "dataco_shipments",
            "batch_id": "broken",
            "passed": False,
            "passed_expectations": 4,
            "failed_expectations": 1,
        },
    )
    _record_result(failure, db_path=database, checked_at=datetime.now(UTC))
    assert isinstance(build_investigation_request(database), RunRequest)

    _record_result(failure, db_path=database, checked_at=datetime.now(UTC))

    assert _statuses(database) == {"dq_failure:dataco_shipments:broken": "queued"}
    assert isinstance(build_investigation_request(database), SkipReason)


def test_schedule_and_sensor_are_stopped_by_default() -> None:
    assert provenance_full_pipeline_schedule.default_status == DefaultScheduleStatus.STOPPED
    assert agent_investigation_sensor.default_status == DefaultSensorStatus.STOPPED


def test_runner_returns_nonzero_when_pipeline_fails(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from provenance.dags import definitions
    from provenance.run_pipeline import main

    class FailedJob:
        nodes_in_topological_order = [
            SimpleNamespace(name="gate_dataco_shipments"),
            SimpleNamespace(name="silver_dbt_assets"),
        ]

        @staticmethod
        def execute_in_process(*, raise_on_error: bool, **_kwargs: object):
            assert raise_on_error is False
            return SimpleNamespace(success=False, all_events=[])

    monkeypatch.setattr(
        definitions,
        "defs",
        SimpleNamespace(resolve_job_def=lambda _name: FailedJob()),
    )
    monkeypatch.delenv("DAGSTER_HOME", raising=False)
    assert main() == 1
    output = capsys.readouterr().out
    assert "Pipeline FAILED" in output
    assert "silver_dbt_assets: skipped" in output


def test_runner_refuses_while_a_writer_run_is_active(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from provenance import run_pipeline

    monkeypatch.setattr(run_pipeline, "_instance", lambda: object())
    monkeypatch.setattr(run_pipeline, "_active_writer_runs", lambda _instance: ["run-1"])
    assert run_pipeline.main() == 2
    assert "run-1" in capsys.readouterr().out
