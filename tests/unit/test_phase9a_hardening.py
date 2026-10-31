"""Phase 9a: safe re-runs and new data."""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
import time
from datetime import date

import duckdb
import pandas as pd
import pytest
from dagster import Failure, RetryRequested

from provenance import db as db_module
from provenance.agent.llm import LLMResponse
from provenance.agent.loop import investigate
from provenance.agent.run import writer_activity
from provenance.agent.tools.investigation_tools import InvestigationTools
from provenance.dags import restore_point
from provenance.data_quality.gate import validate_source_batches
from provenance.ingestion.precheck import expected_columns, precheck_dataco_csv
from provenance.risk_engine.scorer import (
    TOP_FACTOR_FALLBACK,
    payload_hash,
    score_supplier_risk,
    select_top_factor,
)
from tests.unit.agent_fixtures import EVENT_ID
from tests.unit.test_data_quality_gate import _dataframe, _write_batch
from tests.unit.test_risk_engine import _mart_row, _replay_rules, _scoring_database

# --------------------------------------------------------------- item 0


def test_top_factor_for_a_rise_is_never_a_factor_that_fell():
    # cat_boxing_mma 2017-Q3 (low -> medium): weighted-contribution changes.
    changes = {"country_risk": -0.232, "delay_stddev": 0.216, "late_rate": 0.152}
    factors = {
        "country_risk": {"weighted_contribution": 0.079},
        "delay_stddev": {"weighted_contribution": 0.216},
        "late_rate": {"weighted_contribution": 0.193},
    }

    assert select_top_factor(changes, factors, direction=1) == ("delay_stddev", False)
    assert select_top_factor(changes, factors, direction=-1) == ("country_risk", False)


def test_top_factor_falls_back_to_largest_contributor_when_nothing_rose():
    changes = {"country_risk": -0.1, "delay_stddev": 0.0, "late_rate": -0.05}
    factors = {
        "country_risk": {"weighted_contribution": 0.30},
        "delay_stddev": {"weighted_contribution": 0.10},
        "late_rate": {"weighted_contribution": 0.20},
    }

    assert select_top_factor(changes, factors, direction=1) == ("country_risk", True)


def test_scorer_flag_names_the_rising_factor_and_marks_fallbacks(tmp_path):
    db_path = tmp_path / "risk.duckdb"
    # late_rate drives the tier rise; the other two factors carry no weight.
    _scoring_database(
        db_path,
        [_mart_row(date(2016, 1, 1), 0.1, 1), _mart_row(date(2016, 4, 1), 0.9, 2)],
    )
    score_supplier_risk(db_path, _replay_rules(tmp_path / "rules.yaml"))

    with duckdb.connect(str(db_path), read_only=True) as connection:
        factor = json.loads(
            connection.execute(
                "SELECT top_contributing_factor FROM gold.supplier_risk_flags"
            ).fetchone()[0]
        )
    assert factor["name"] == "late_rate"
    assert factor["weighted_contribution_change"] > 0
    assert "selection" not in factor
    assert TOP_FACTOR_FALLBACK == "largest_contributor_fallback"


# --------------------------------------------------------------- item 1


def _dataco_batch(tmp_path, batch_id, frame):
    return _write_batch(tmp_path, "dataco_shipments", batch_id, frame)


def test_failed_batch_is_moved_and_does_not_block_the_next_run(tmp_path):
    bronze = tmp_path / "bronze"
    quarantine = tmp_path / "quarantine"
    db_path = tmp_path / "warehouse.duckdb"
    bad = _dataframe("dataco_shipments")
    bad.loc[0, "Late_delivery_risk"] = 9
    _dataco_batch(tmp_path, "a-bad", bad)

    first = validate_source_batches(
        "dataco_shipments", bronze_root=bronze, db_path=db_path, quarantine_root=quarantine
    )

    assert [result.passed for result in first] == [False]
    assert not (bronze / "dataco_shipments" / "batch_id=a-bad").exists()
    assert (quarantine / "dataco_shipments" / "batch_id=a-bad" / "part-0.parquet").is_file()

    good = _dataframe("dataco_shipments")
    good["Order Item Id"] = [2000, 2001]
    _dataco_batch(tmp_path, "b-good", good)
    second = validate_source_batches(
        "dataco_shipments", bronze_root=bronze, db_path=db_path, quarantine_root=quarantine
    )
    third = validate_source_batches(
        "dataco_shipments", bronze_root=bronze, db_path=db_path, quarantine_root=quarantine
    )

    assert [(result.batch_id, result.passed) for result in second] == [("b-good", True)]
    assert third == []  # nothing is validated twice
    with duckdb.connect(str(db_path), read_only=True) as connection:
        ids = [
            row[0]
            for row in connection.execute(
                'SELECT "Order Item Id" FROM bronze."dataco_shipments" ORDER BY 1'
            ).fetchall()
        ]
        statuses = dict(
            connection.execute("SELECT batch_id, status FROM bronze.dq_results").fetchall()
        )
    assert ids == [2000, 2001]  # the quarantined batch's rows are not in bronze
    assert statuses == {"a-bad": "quarantined", "b-good": "validated"}


def test_quarantined_content_is_not_reloaded_into_bronze(tmp_path):
    from provenance.ingestion import common

    bronze = tmp_path / "bronze"
    db_path = tmp_path / "warehouse.duckdb"
    bad = _dataframe("dataco_shipments")
    bad.loc[0, "Late_delivery_risk"] = 9
    _dataco_batch(tmp_path, "a-bad", bad)
    validate_source_batches(
        "dataco_shipments",
        bronze_root=bronze,
        db_path=db_path,
        quarantine_root=tmp_path / "quarantine",
    )

    common.write_bronze_dataframe(
        bad.drop(columns=["_batch_id", "_source", "_source_file", "_loaded_at"]),
        source="dataco_shipments",
        batch_id="a-bad",
        db_path=db_path,
        bronze_root=bronze,
    )

    assert not (bronze / "dataco_shipments" / "batch_id=a-bad").exists()


# --------------------------------------------------------------- item 2


def _approved_first_batch(tmp_path):
    bronze = tmp_path / "bronze"
    db_path = tmp_path / "warehouse.duckdb"
    _dataco_batch(tmp_path, "a-first", _dataframe("dataco_shipments"))
    validate_source_batches(
        "dataco_shipments",
        bronze_root=bronze,
        db_path=db_path,
        quarantine_root=tmp_path / "quarantine",
    )
    return bronze, db_path


def test_exact_cross_batch_duplicates_are_skipped_with_a_warning(tmp_path):
    bronze, db_path = _approved_first_batch(tmp_path)
    second = _dataframe("dataco_shipments", row_count=3)  # ids 1000-1002, 1000-1001 repeat
    _dataco_batch(tmp_path, "b-second", second)

    [result] = validate_source_batches(
        "dataco_shipments",
        bronze_root=bronze,
        db_path=db_path,
        quarantine_root=tmp_path / "quarantine",
    )

    assert result.passed
    warning = next(w for w in result.warnings if w["checkpoint"] == "cross_batch_duplicates")
    assert warning["unexpected_count"] == 2
    assert "skipped" in warning["message"]
    kept = pd.read_parquet(bronze / "dataco_shipments" / "batch_id=b-second" / "part-0.parquet")
    assert kept["Order Item Id"].tolist() == [1002]
    with duckdb.connect(str(db_path), read_only=True) as connection:
        ids = [
            row[0]
            for row in connection.execute(
                'SELECT "Order Item Id" FROM bronze."dataco_shipments" ORDER BY 1'
            ).fetchall()
        ]
        status = connection.execute(
            "SELECT status FROM bronze.dq_results WHERE batch_id = 'b-second'"
        ).fetchone()[0]
    assert ids == [1000, 1001, 1002]
    assert status == "passed_with_warnings"


def test_conflicting_cross_batch_duplicate_quarantines_the_batch(tmp_path):
    bronze, db_path = _approved_first_batch(tmp_path)
    second = _dataframe("dataco_shipments")
    second.loc[0, "Sales"] = 999.0  # id 1000 again, different value
    _dataco_batch(tmp_path, "b-conflict", second)

    [result] = validate_source_batches(
        "dataco_shipments",
        bronze_root=bronze,
        db_path=db_path,
        quarantine_root=tmp_path / "quarantine",
    )

    assert not result.passed
    detail = next(d for d in result.failed_details if d["checkpoint"] == "cross_batch_duplicates")
    assert detail["result"]["partial_unexpected_list"] == [1000]
    assert "already exist in an approved batch with different values" in detail["message"]
    assert not (bronze / "dataco_shipments" / "batch_id=b-conflict").exists()


# --------------------------------------------------------------- item 3


def _events(db_path):
    with duckdb.connect(str(db_path), read_only=True) as connection:
        return connection.execute(
            "SELECT event_id, resolved_at IS NOT NULL, resolution, payload "
            "FROM agent.events WHERE event_type = 'risk_flag' ORDER BY created_at, event_id"
        ).fetchall()


def test_unchanged_payload_keeps_its_event_untouched(tmp_path):
    db_path = tmp_path / "risk.duckdb"
    _scoring_database(
        db_path,
        [_mart_row(date(2016, 1, 1), 0.1, 1), _mart_row(date(2016, 4, 1), 0.9, 2)],
    )
    rules = _replay_rules(tmp_path / "rules.yaml")
    score_supplier_risk(db_path, rules)
    before = _events(db_path)

    second = score_supplier_risk(db_path, rules)

    assert _events(db_path) == before
    assert second.events_superseded == 0 and second.events_emitted == 0
    assert before[0][0] == "risk_flag:1.2:2016-04-01:cat_test"


def test_payload_change_supersedes_the_old_event_and_creates_a_new_one(tmp_path):
    db_path = tmp_path / "risk.duckdb"
    _scoring_database(
        db_path,
        [_mart_row(date(2016, 1, 1), 0.1, 1), _mart_row(date(2016, 4, 1), 0.9, 2)],
    )
    rules = _replay_rules(tmp_path / "rules.yaml")
    score_supplier_risk(db_path, rules)
    [(old_id, _, _, old_payload)] = _events(db_path)
    with duckdb.connect(str(db_path)) as connection:
        connection.execute(
            "UPDATE gold.mart_supplier_risk SET late_rate = 0.95 WHERE quarter = '2016-04-01'"
        )
        # An investigation bound to the old event must keep seeing the old numbers.
        connection.execute("CREATE TABLE agent.bound (event_id VARCHAR)")
        connection.execute("INSERT INTO agent.bound VALUES (?)", [old_id])

    summary = score_supplier_risk(db_path, rules)

    events = _events(db_path)
    old, new = events
    assert summary.events_superseded == 1 and summary.events_emitted == 1
    assert old[0] == old_id and old[1] is True
    assert old[2] == f"superseded: payload changed; replaced by {new[0]}"
    assert old[3] == old_payload  # never rewritten
    new_payload = json.loads(new[3])
    assert new[0] == f"{old_id}:p{payload_hash(new_payload)}"
    assert new[1] is False
    assert new_payload["score"] == pytest.approx(0.95)
    with duckdb.connect(str(db_path), read_only=True) as connection:
        assert connection.execute("SELECT event_id FROM gold.supplier_risk_flags").fetchone() == (
            new[0],
        )


def test_flags_that_stop_qualifying_are_resolved_not_deleted(tmp_path):
    db_path = tmp_path / "risk.duckdb"
    _scoring_database(
        db_path,
        [_mart_row(date(2016, 1, 1), 0.1, 1), _mart_row(date(2016, 4, 1), 0.9, 2)],
    )
    rules = _replay_rules(tmp_path / "rules.yaml")
    score_supplier_risk(db_path, rules)
    with duckdb.connect(str(db_path)) as connection:
        connection.execute(
            "UPDATE gold.mart_supplier_risk SET late_rate = 0.1 WHERE quarter = '2016-04-01'"
        )

    summary = score_supplier_risk(db_path, rules)

    assert summary.flags_resolved == 1
    with duckdb.connect(str(db_path), read_only=True) as connection:
        flag = connection.execute(
            "SELECT status, resolution FROM gold.supplier_risk_flags"
        ).fetchone()
        event = connection.execute(
            "SELECT resolved_at IS NOT NULL, resolution FROM agent.events"
        ).fetchone()
    assert flag == ("resolved", "resolved: no longer qualifies under rules 1.2")
    assert event == (True, "resolved: no longer qualifies under rules 1.2")


# --------------------------------------------------------------- item 4


def test_read_only_open_retries_a_lock_and_succeeds_on_the_second_try(monkeypatch, tmp_path):
    db_path = tmp_path / "w.duckdb"
    duckdb.connect(str(db_path)).close()
    real_connect = duckdb.connect
    attempts = []

    def flaky(path, read_only=False):
        attempts.append(read_only)
        if len(attempts) == 1:
            raise duckdb.IOException("IO Error: Could not set lock on file")
        return real_connect(path, read_only=read_only)

    monkeypatch.setattr(db_module.duckdb, "connect", flaky)
    sleeps = []

    connection = db_module.connect_read_only(db_path, sleep=sleeps.append)
    connection.close()

    assert attempts == [True, True]
    assert sleeps == [0.25]


def test_read_only_open_gives_up_after_three_tries(monkeypatch, tmp_path):
    def locked(*_args, **_kwargs):
        raise duckdb.IOException("IO Error: File is already open")

    monkeypatch.setattr(db_module.duckdb, "connect", locked)
    sleeps = []
    with pytest.raises(duckdb.IOException):
        db_module.connect_read_only(tmp_path / "w.duckdb", sleep=sleeps.append)
    assert sleeps == [0.25, 0.5]


def test_agent_releases_its_connection_before_every_llm_call(warehouse):
    class WriterProbingLLM:
        """Opens a read-write connection inside the LLM call: fails if a reader is open."""

        def __init__(self, toolbox):
            self.toolbox = toolbox
            self.calls = 0

        def chat(self, messages, tools=None, tool_choice="auto"):
            self.calls += 1
            assert not self.toolbox.is_open
            duckdb.connect(str(warehouse)).close()  # raises if a read-only handle is held
            report = {
                "summary": "cat_widgets scored 0.735.",
                "top_factor": "delay_stddev",
                "evidence": [{"tool_name": "get_flag", "value": "score=0.735"}],
                "proposed_action": "watchlist",
                "confidence": "high",
            }
            return LLMResponse(json.dumps(report), [], 10, 5, "stop")

    with InvestigationTools(warehouse) as toolbox:
        llm = WriterProbingLLM(toolbox)
        result = investigate(EVENT_ID, llm, toolbox)

    assert llm.calls == 1
    assert result.report is not None
    assert [call["name"] for call in result.tool_calls] == [
        "get_flag",
        "recall_past_investigations",
    ]


def test_agent_refuses_to_start_while_another_process_holds_the_warehouse(tmp_path):
    db_path = tmp_path / "w.duckdb"
    duckdb.connect(str(db_path)).close()
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            textwrap.dedent(
                f"""
                import duckdb, time
                c = duckdb.connect({str(db_path)!r})
                print("held", flush=True)
                time.sleep(20)
                """
            ),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout.readline().strip() == "held"
        message = writer_activity(db_path)
    finally:
        holder.kill()
        holder.wait()
    assert message and "holds the warehouse" in message
    time.sleep(0.2)
    assert writer_activity(db_path) is None


def test_gate_asset_asks_dagster_to_retry_on_a_lock(monkeypatch):
    from provenance.dags import ingestion_assets

    def locked(*_args, **_kwargs):
        raise duckdb.IOException("IO Error: Could not set lock on file")

    monkeypatch.setattr(ingestion_assets, "validate_source_batches", locked)
    with pytest.raises(RetryRequested):
        ingestion_assets._run_quality_gate("dataco_shipments")


# --------------------------------------------------------------- item 5


def _layers(db_path):
    with duckdb.connect(str(db_path)) as connection:
        connection.execute("CREATE SCHEMA silver")
        connection.execute("CREATE SCHEMA gold")
        connection.execute("CREATE TABLE silver.fact_shipments AS SELECT 1 AS order_item_id")
        connection.execute("CREATE TABLE gold.mart_supplier_risk AS SELECT 0.4 AS score")
        connection.execute(
            "CREATE TABLE gold.supplier_risk_score (supplier_key VARCHAR PRIMARY KEY, s DOUBLE)"
        )
        connection.execute("INSERT INTO gold.supplier_risk_score VALUES ('cat_a', 0.4)")


def _snapshot(db_path):
    with duckdb.connect(str(db_path), read_only=True) as connection:
        return {
            "silver": connection.execute("SELECT * FROM silver.fact_shipments").fetchall(),
            "gold": connection.execute("SELECT * FROM gold.mart_supplier_risk").fetchall(),
            "tables": connection.execute(
                "SELECT table_schema, table_name FROM information_schema.tables "
                "WHERE table_schema IN ('silver', 'gold') ORDER BY 1, 2"
            ).fetchall(),
        }


class FakeInvocation:
    def __init__(self, db_path, *, mutate, success, error=""):
        self.db_path, self.mutate, self.success, self.error = db_path, mutate, success, error

    def stream(self):
        if self.mutate:
            with duckdb.connect(str(self.db_path)) as connection:
                connection.execute(
                    "CREATE OR REPLACE TABLE silver.fact_shipments AS SELECT 99 AS order_item_id"
                )
                connection.execute(
                    "CREATE OR REPLACE TABLE gold.mart_supplier_risk AS SELECT 0.9 AS score"
                )
                connection.execute("CREATE TABLE silver.new_model AS SELECT 1 AS x")
        yield "event"

    def is_successful(self):
        return self.success

    def get_error(self):
        return self.error or None

    def get_artifact(self, _name):
        return {"results": [{"message": self.error}]}


class FakeDbt:
    def __init__(self, invocation):
        self.invocation = invocation

    def cli(self, args, context=None, raise_on_error=True):
        return self.invocation


def test_failing_dbt_test_leaves_the_previous_silver_and_gold_intact(tmp_path):
    db_path = tmp_path / "w.duckdb"
    _layers(db_path)
    before = _snapshot(db_path)
    failing = FakeInvocation(
        db_path,
        mutate=True,
        success=False,
        error="Failure in test not_null_fact_shipments_order_country_iso3",
    )

    with pytest.raises(Failure, match="restored"):
        list(
            restore_point.guarded_dbt_build(
                None, FakeDbt(failing), ["build"], db_path=db_path, phase="silver"
            )
        )

    assert _snapshot(db_path) == before
    assert not restore_point.has_restore_point(db_path)


def test_gold_failure_also_restores_silver_from_the_silver_restore_point(tmp_path):
    db_path = tmp_path / "w.duckdb"
    _layers(db_path)
    before = _snapshot(db_path)
    silver_ok = FakeInvocation(db_path, mutate=True, success=True)
    list(
        restore_point.guarded_dbt_build(
            None, FakeDbt(silver_ok), [], db_path=db_path, phase="silver"
        )
    )
    assert restore_point.has_restore_point(db_path)

    gold_fails = FakeInvocation(db_path, mutate=False, success=False, error="Error in model x")
    with pytest.raises(Failure):
        list(
            restore_point.guarded_dbt_build(
                None, FakeDbt(gold_fails), [], db_path=db_path, phase="gold"
            )
        )

    assert _snapshot(db_path) == before
    with duckdb.connect(str(db_path), read_only=True) as connection:
        # The scorer's constrained table is not dbt's: left as it was, PK intact.
        assert connection.execute("SELECT * FROM gold.supplier_risk_score").fetchall() == [
            ("cat_a", 0.4)
        ]


def test_gold_success_drops_the_restore_point_and_lock_errors_retry(tmp_path):
    db_path = tmp_path / "w.duckdb"
    _layers(db_path)
    ok = FakeInvocation(db_path, mutate=False, success=True)
    list(restore_point.guarded_dbt_build(None, FakeDbt(ok), [], db_path=db_path, phase="silver"))
    list(restore_point.guarded_dbt_build(None, FakeDbt(ok), [], db_path=db_path, phase="gold"))
    assert not restore_point.has_restore_point(db_path)

    locked = FakeInvocation(
        db_path, mutate=False, success=False, error="IO Error: Could not set lock on file"
    )
    with pytest.raises(RetryRequested):
        list(
            restore_point.guarded_dbt_build(
                None, FakeDbt(locked), [], db_path=db_path, phase="silver"
            )
        )
    assert restore_point.has_restore_point(db_path)  # kept for the retry


# --------------------------------------------------------------- item 6


def _csv(tmp_path, rows):
    columns = expected_columns()
    frame = pd.DataFrame([{column: "x" for column in columns} for _ in rows])
    for index, overrides in enumerate(rows):
        for column, value in overrides.items():
            frame.loc[index, column] = value
    for column in (
        "Days for shipping (real)",
        "Days for shipment (scheduled)",
        "Order Item Quantity",
    ):
        frame[column] = frame[column].where(frame[column] != "x", "3")
    path = tmp_path / "upload.csv"
    frame.to_csv(path, index=False, encoding="latin-1")
    return path


GOOD_ROW = {
    "order date (DateOrders)": "1/31/2018 22:56",
    "shipping date (DateOrders)": "2/3/2018 22:56",
    "Order Country": "México",
}


def test_precheck_passes_a_well_formed_file(tmp_path):
    result = precheck_dataco_csv(_csv(tmp_path, [GOOD_ROW, GOOD_ROW]))

    assert result.passed, result.summary()
    assert result.row_count == 2


def test_precheck_reports_missing_countries_and_bad_dates(tmp_path):
    rows = [
        {**GOOD_ROW, "order date (DateOrders)": "2018-01-31 22:56"},
        {**GOOD_ROW, "Order Country": "Atlantis"},
        {**GOOD_ROW, "Order Country": "Narnia", "Order Item Quantity": ""},
    ]

    result = precheck_dataco_csv(_csv(tmp_path, rows))

    assert not result.passed
    text = result.summary()
    assert "Order Country value(s) are not in country_iso3_map: Atlantis, Narnia" in text
    assert "1 value(s) in 'order date (DateOrders)' are not in the format" in text
    assert "'2018-01-31 22:56'" in text
    assert "1 blank value(s) in integer column 'Order Item Quantity'" in text


def test_precheck_reports_column_order_and_missing_columns(tmp_path):
    path = _csv(tmp_path, [GOOD_ROW])
    frame = pd.read_csv(path, encoding="latin-1", dtype=str)
    columns = list(frame.columns)
    columns[0], columns[1] = columns[1], columns[0]
    frame[columns].drop(columns=["Sales"]).to_csv(path, index=False, encoding="latin-1")

    result = precheck_dataco_csv(path)

    assert not result.passed
    assert "missing ['Sales']" in result.problems[0]
