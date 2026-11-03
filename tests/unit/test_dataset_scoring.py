"""Phase 10c-1: canonical silver, metrics, scores and flags for an approved dataset.

Every test builds its own temporary warehouse from synthetic CSVs; the LLM is never
called (mappings come from the heuristic and are approved directly).
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import duckdb
import pytest
from dagster import Failure, build_op_context

from provenance.agent.grounding import GroundingResult
from provenance.agent.loop import InvestigationResult
from provenance.agent.run import record_outcome
from provenance.agent.store import verify_audit_log
from provenance.agent.tools.investigation_tools import InvestigationTools, dataset_id_for_event
from provenance.dags.score_dataset import score_dataset_op
from provenance.data_quality.gate import ensure_agent_events_table
from provenance.datasets import country, mapping_store
from provenance.datasets import score as score_module
from provenance.datasets.mapping_agent import propose_mapping
from provenance.datasets.onboard import Paths, onboard
from provenance.datasets.score import ScoringRefused, score_dataset
from provenance.datasets.validator import load_context
from provenance.risk_engine import scorer
from tests.unit.scoring_fixtures import add_lpi, make_shipments_csv

FLAGSHIP_EVENT = "risk_flag:1.2:2017-04-01:cat_cleats"


def _mapped(tmp_path: Path, *, lpi: bool = False, sets: list[str] | None = None, **fixture):
    """Onboard a synthetic CSV, take the heuristic mapping (optionally edited), approve it."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    csv_path = make_shipments_csv(tmp_path / "shipments.csv", **fixture)
    paths = Paths(tmp_path / "warehouse.duckdb", tmp_path / "datasets", tmp_path / "quarantine")
    result = onboard(csv_path, "synthetic shipments", paths=paths)
    assert result.status == "gated", result.problems
    if lpi:
        add_lpi(paths.db_path)
    proposal = propose_mapping(load_context(paths.db_path, result.dataset_id), heuristic_only=True)
    mapping_id = mapping_store.record_proposal(
        paths.db_path, result.dataset_id, proposal, created_by="heuristic"
    )
    if sets:
        mapping_id, _ = mapping_store.edit(
            paths.db_path, mapping_id, mapping_store.parse_sets(sets), by="tester"
        )
    mapping_store.approve(paths.db_path, mapping_id, by="tester")
    return paths.db_path, result.dataset_id, csv_path


def _query(db: Path, sql: str, params=None):
    with duckdb.connect(str(db), read_only=True) as connection:
        return connection.execute(sql, params or []).fetchall()


def _csv_rows(csv_path: Path) -> list[dict[str, str]]:
    with csv_path.open(encoding="utf-8", newline="") as handle:
        return [row for row in csv.DictReader(handle) if not row["shipment_id"].startswith("BAD")]


# --- canonical silver --------------------------------------------------------------


@pytest.mark.parametrize(
    ("outcome", "expected_kind", "late_rule"),
    [
        (
            "days_pair_hours",
            "days_pair",
            lambda r: int(r["actual_hours"]) > int(r["planned_hours"]),
        ),
        ("days_pair_days", "days_pair", lambda r: int(r["actual_days"]) > int(r["planned_days"])),
        ("delay_days", "delay_value", lambda r: int(r["delay_days"]) > 0),
        ("late_flag_on_time", "late_flag", lambda r: r["on_time"] == "no"),
        ("late_flag", "late_flag", lambda r: r["is_late"] == "1"),
    ],
)
def test_each_outcome_kind_and_polarity_gives_the_same_lateness(
    tmp_path, outcome, expected_kind, late_rule
):
    db, dataset_id, csv_path = _mapped(tmp_path, outcome=outcome)
    result = score_dataset(db, dataset_id)
    mapping = [m for m in mapping_store.list_mappings(db, dataset_id) if m["status"] == "approved"][
        0
    ]["proposal"]

    assert mapping["outcome"]["kind"] == expected_kind
    if outcome == "late_flag_on_time":
        assert mapping["outcome"]["late_when"] == "false"
    expected_late = sum(late_rule(row) for row in _csv_rows(csv_path))
    late = _query(
        db, f"SELECT count(*) FILTER (WHERE is_late) FROM ds_{dataset_id}.silver_shipments"
    )
    assert late[0][0] == expected_late
    assert result.scores_written > 0


def test_hours_become_days(tmp_path):
    hours_db, hours_id, _ = _mapped(tmp_path / "h", outcome="days_pair_hours")
    days_db, days_id, _ = _mapped(tmp_path / "d", outcome="days_pair_days")
    score_dataset(hours_db, hours_id)
    score_dataset(days_db, days_id)
    sql = "SELECT entity_key, event_date, delay_days FROM ds_{}.silver_shipments ORDER BY ALL"

    assert _query(hours_db, sql.format(hours_id)) == _query(days_db, sql.format(days_id))
    assert (
        _query(hours_db, f"SELECT max(delay_days) FROM ds_{hours_id}.silver_shipments")[0][0] < 24
    )


def test_unparseable_rows_go_to_rejects_with_a_reason(tmp_path):
    db, dataset_id, _ = _mapped(tmp_path, bad_rows=3)
    result = score_dataset(db, dataset_id)

    rejects = _query(
        db, f"SELECT reason, count(*) FROM ds_{dataset_id}.silver_rejects GROUP BY 1 ORDER BY 1"
    )
    assert rejects == [
        ("entity has no letters or digits", 1),
        ("entity is empty", 1),
        ("scheduled or actual duration is not a number", 1),
    ]
    assert result.silver.rows_in == result.silver.rows_out + 3


def test_cancelled_rows_count_as_shipments_but_not_toward_lateness(tmp_path):
    db, dataset_id, _ = _mapped(
        tmp_path,
        outcome="delay_days",
        cancelled_share=0.1,
        sets=["cancelled.col=status", "cancelled.value=Cancelled"],
    )
    score_dataset(db, dataset_id)
    schema = f"ds_{dataset_id}"
    cancelled = _query(db, f"SELECT count(*) FROM {schema}.silver_shipments WHERE is_cancelled")[0][
        0
    ]
    shipments, canceled_count, late = _query(
        db,
        "SELECT sum(shipment_count), sum(canceled_count), sum(late_count) "
        f"FROM {schema}.quarter_metrics",
    )[0]
    late_not_cancelled = _query(
        db, f"SELECT count(*) FROM {schema}.silver_shipments WHERE is_late AND NOT is_cancelled"
    )[0][0]

    assert cancelled > 0 and canceled_count == cancelled
    assert shipments == _query(db, f"SELECT count(*) FROM {schema}.silver_shipments")[0][0]
    assert late == late_not_cancelled


# --- country -----------------------------------------------------------------------


def test_country_normaliser_handles_codes_names_and_the_spanish_seed():
    report = country.normalise(
        [("DE", 3), ("FRA", 2), ("Mexico", 1), ("Estados Unidos", 1), ("Japón", 1), ("Atlantis", 2)]
    )
    assert report.mapping == {
        "DE": "DEU",
        "FRA": "FRA",
        "Mexico": "MEX",
        "Estados Unidos": "USA",
        "Japón": "JPN",
        "Atlantis": None,
    }
    assert report.match_rate == 8 / 10
    assert report.as_dict()["top_unmatched"] == [{"value": "Atlantis", "rows": 2}]
    assert country.lookup().to_iso3("Europe & Central Asia") is None  # aggregates never match


def test_country_risk_is_used_when_countries_match_and_lpi_exists(tmp_path):
    db, dataset_id, _ = _mapped(tmp_path, lpi=True)
    result = score_dataset(db, dataset_id)

    assert result.excluded_factors == {}
    assert set(result.weights) == {"late_rate", "delay_stddev", "country_risk"}
    assert result.silver.country["match_rate"] == 1.0
    confidence = _query(
        db, f"SELECT DISTINCT confidence FROM ds_{dataset_id}.risk_score ORDER BY 1"
    )
    assert ("full",) in confidence


@pytest.mark.parametrize(
    ("fixture", "reason"),
    [
        ({"country": False}, "no country column"),
        ({"unmatched_country_share": 0.5}, "below the 80%"),
    ],
)
def test_without_usable_country_weights_renormalise_and_confidence_drops(tmp_path, fixture, reason):
    db, dataset_id, _ = _mapped(tmp_path, lpi=True, **fixture)
    result = score_dataset(db, dataset_id)

    assert result.weights == pytest.approx({"late_rate": 0.25 / 0.6, "delay_stddev": 0.35 / 0.6})
    assert reason in result.excluded_factors["country_risk"]
    factors, confidence = _query(
        db, f"SELECT contributing_factors, confidence FROM ds_{dataset_id}.risk_score LIMIT 1"
    )[0]
    factors = json.loads(factors)
    assert confidence == "low"
    assert (
        factors["country_risk"]["excluded"] is True and reason in factors["country_risk"]["reason"]
    )
    assert factors["country_risk"]["weight"] == 0.0


def test_late_flag_only_scores_without_delay_variability(tmp_path):
    db, dataset_id, _ = _mapped(tmp_path, outcome="late_flag", lpi=True)
    result = score_dataset(db, dataset_id)

    assert "delay_stddev" in result.excluded_factors
    assert result.weights == pytest.approx({"late_rate": 0.25 / 0.65, "country_risk": 0.40 / 0.65})


# --- scoreable ---------------------------------------------------------------------


def test_fewer_than_three_quarters_is_not_scoreable(tmp_path):
    db, dataset_id, _ = _mapped(tmp_path, quarters=2)

    with pytest.raises(ScoringRefused, match="not scoreable") as refused:
        score_dataset(db, dataset_id)
    assert "quarter" in str(refused.value)
    assert not _query(
        db,
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema = ? AND table_name = 'risk_score'",
        [f"ds_{dataset_id}"],
    )


def test_partial_last_quarter_leaves_too_few_eligible_quarters(tmp_path):
    # The mapping estimate counts 3 quarters, but the flagship rule drops the partial one.
    db, dataset_id, _ = _mapped(tmp_path, quarters=3, days_short=20)

    with pytest.raises(ScoringRefused, match=r"only 2 eligible quarter\(s\)"):
        score_dataset(db, dataset_id)
    assert not _query(db, "SELECT * FROM meta.dataset_rules")  # rolled back


def test_unmapped_dataset_is_refused(tmp_path):
    csv_path = make_shipments_csv(tmp_path / "s.csv")
    paths = Paths(tmp_path / "w.duckdb", tmp_path / "datasets", tmp_path / "quarantine")
    dataset_id = onboard(csv_path, "not yet mapped", paths=paths).dataset_id

    with pytest.raises(ScoringRefused, match="only a dataset with an approved mapping"):
        score_dataset(paths.db_path, dataset_id)


# --- frozen bounds, events ---------------------------------------------------------


def test_frozen_bounds_do_not_move_when_rows_are_added(tmp_path):
    db, dataset_id, _ = _mapped(tmp_path)
    first = score_dataset(db, dataset_id)
    with duckdb.connect(str(db)) as connection:
        # A second batch where every shipment is very late would move the percentiles.
        connection.execute(
            f"""
            INSERT INTO ds_{dataset_id}.bronze_raw
            SELECT * REPLACE ('new-batch' AS _batch_id,
                              (try_cast(actual_hours AS INTEGER) + 240)::VARCHAR AS actual_hours)
            FROM ds_{dataset_id}.bronze_raw
            """
        )
    second = score_dataset(db, dataset_id)

    assert first.rules_frozen_now and not second.rules_frozen_now
    assert (
        second.bounds == first.bounds and second.rules_version == first.rules_version == "1.2-ds1"
    )
    assert _query(db, "SELECT count(*) FROM meta.dataset_rules")[0][0] == 1
    refrozen = score_dataset(db, dataset_id, refreeze=True)
    assert refrozen.rules_version == "1.2-ds2" and refrozen.bounds != first.bounds


def _seed_flagship(db: Path) -> None:
    with duckdb.connect(str(db)) as connection:
        scorer._create_score_tables(connection)
        ensure_agent_events_table(connection)
        connection.execute(
            "INSERT INTO agent.events "
            "(event_id, event_type, source, batch_id, created_at, payload) "
            "VALUES (?, 'risk_flag', 'risk_engine', '2017-04-01', now(), ?::JSON)",
            [FLAGSHIP_EVENT, json.dumps({"supplier_key": "cat_cleats", "quarter": "2017-04-01"})],
        )
        connection.execute(
            "INSERT INTO gold.supplier_risk_flags "
            "(supplier_key, quarter, old_tier, new_tier, score, "
            "top_contributing_factor, confidence, event_id, status) VALUES "
            "('cat_cleats', '2017-04-01', 'low', 'medium', 0.5, '{}', 'full', ?, 'active')",
            [FLAGSHIP_EVENT],
        )


def test_dataset_events_are_prefixed_and_dataco_events_untouched(tmp_path):
    db, dataset_id, _ = _mapped(tmp_path)
    _seed_flagship(db)
    before = _query(db, "SELECT * FROM agent.events WHERE source = 'risk_engine'")
    flags_before = _query(db, "SELECT * FROM gold.supplier_risk_flags")
    result = score_dataset(db, dataset_id)

    events = _query(
        db, "SELECT event_id, payload FROM agent.events WHERE source = 'dataset_risk_engine'"
    )
    assert result.events_added == len(events) > 0
    assert all(event_id.startswith(f"ds_{dataset_id}:risk_flag:1.2-ds1:") for event_id, _ in events)
    assert all(json.loads(payload)["dataset_id"] == dataset_id for _, payload in events)
    assert all(dataset_id_for_event(event_id) == dataset_id for event_id, _ in events)
    assert _query(db, "SELECT * FROM agent.events WHERE source = 'risk_engine'") == before
    assert _query(db, "SELECT * FROM gold.supplier_risk_flags") == flags_before

    # And the flagship's own supersede pass leaves dataset events alone.
    with duckdb.connect(str(db)) as connection:
        scorer._supersede_previous_flags(connection, "9.9")
    assert _query(
        db,
        "SELECT count(*) FROM agent.events "
        "WHERE source = 'dataset_risk_engine' AND resolved_at IS NULL",
    )[0][0] == len(events)


def test_rescoring_twice_creates_no_new_events(tmp_path):
    db, dataset_id, _ = _mapped(tmp_path, lpi=True)
    first = score_dataset(db, dataset_id)
    second = score_dataset(db, dataset_id)
    third = score_dataset(db, dataset_id)

    assert first.events_added > 0
    assert (second.events_added, second.events_superseded, second.flags_resolved) == (0, 0, 0)
    assert (third.events_added, third.events_superseded) == (0, 0)
    with duckdb.connect(str(db), read_only=True) as connection:
        assert verify_audit_log(connection) == []
        scored = connection.execute(
            "SELECT count(*) FROM agent.audit_log WHERE event = 'dataset_scored'"
        ).fetchone()[0]
    assert scored == 3


# --- Dagster, CLI ------------------------------------------------------------------


def test_dagster_job_refuses_an_unmapped_dataset_and_a_restore_point(tmp_path, monkeypatch):
    csv_path = make_shipments_csv(tmp_path / "s.csv")
    paths = Paths(tmp_path / "w.duckdb", tmp_path / "datasets", tmp_path / "quarantine")
    dataset_id = onboard(csv_path, "unmapped", paths=paths).dataset_id
    monkeypatch.setenv("PROVENANCE_DUCKDB_PATH", str(paths.db_path))

    with pytest.raises(Failure, match="approved mapping"):
        score_dataset_op(build_op_context(op_config={"dataset_id": dataset_id}))
    with duckdb.connect(str(paths.db_path)) as connection:
        connection.execute("CREATE SCHEMA _restore_point")
    with pytest.raises(Failure, match="restore point"):
        score_dataset_op(build_op_context(op_config={"dataset_id": dataset_id}))


def test_dagster_job_scores_a_mapped_dataset(tmp_path, monkeypatch):
    db, dataset_id, _ = _mapped(tmp_path)
    monkeypatch.setenv("PROVENANCE_DUCKDB_PATH", str(db))
    output = score_dataset_op(build_op_context(op_config={"dataset_id": dataset_id}))

    assert output.value == "1.2-ds1"


def test_cli_scores_and_refuses(tmp_path, capsys):
    db, dataset_id, _ = _mapped(tmp_path)
    assert score_module.main([dataset_id, "--db-path", str(db)]) == 0
    assert "scored with rules 1.2-ds1" in capsys.readouterr().out
    assert score_module.main([dataset_id, "--db-path", str(db), "--bounds-mode", "flagship"]) == 2
    assert "bounds_mode='dataset'" in capsys.readouterr().err


# --- investigation tools -----------------------------------------------------------


def test_investigation_tools_read_the_dataset(tmp_path):
    db, dataset_id, _ = _mapped(tmp_path)
    score_dataset(db, dataset_id)
    event_id = _query(db, "SELECT event_id FROM agent.events WHERE source = 'dataset_risk_engine'")[
        0
    ][0]

    with InvestigationTools(db, dataset_id=dataset_id) as tools:
        flag = tools.call("get_flag", {"event_id": event_id})
        breakdown = tools.call(
            "get_score_breakdown",
            {"supplier_key": flag["supplier_key"], "quarter": flag["quarter"]},
        )
        history = tools.call("get_supplier_history", {"supplier_key": flag["supplier_key"]})
        market = tools.call(
            "get_market_mix", {"supplier_key": flag["supplier_key"], "quarter": flag["quarter"]}
        )
        country_risk = tools.call(
            "get_country_risk", {"supplier_key": flag["supplier_key"], "quarter": flag["quarter"]}
        )
    assert "error" not in flag and flag["new_tier"]
    assert breakdown["dataset_id"] == dataset_id
    assert "country_risk" in breakdown["excluded_factors"]  # no LPI in this warehouse
    assert {f["factor"] for f in breakdown["factors"]} == {"late_rate", "delay_stddev"}
    assert history["scored_quarters"]
    assert "DataCo-only" in market["error"]
    assert "without country risk" in country_risk["error"]

    with InvestigationTools(db) as dataco_tools:
        assert "different dataset" in dataco_tools.call("get_flag", {"event_id": event_id})["error"]


def test_investigations_record_their_dataset(tmp_path):
    db, dataset_id, _ = _mapped(tmp_path)
    score_dataset(db, dataset_id)
    event_id = _query(db, "SELECT event_id FROM agent.events WHERE source = 'dataset_risk_engine'")[
        0
    ][0]
    result = InvestigationResult(event_id=event_id, report=None, steps=1, stop_reason="max_steps")
    record_outcome(db, result, GroundingResult(False, 0.0, 0), "test-model", True)

    assert _query(db, "SELECT dataset_id, status FROM agent.investigations") == [
        (dataset_id, "no_report")
    ]
