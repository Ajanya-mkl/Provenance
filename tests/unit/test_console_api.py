"""Phase 10c-2: the console's dataset flow through the API, on temporary warehouses.

Scoring runs as a real background subprocess (datasets.runs) against the temporary
database; the tests poll /api/runs like the console does.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from provenance.agent.llm import LLMError
from provenance.api.main import create_app
from provenance.datasets import runs
from provenance.datasets.onboard import Paths
from tests.unit.scoring_fixtures import make_shipments_csv

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "datasets"
RUN_TIMEOUT_S = 120


def _no_llm():
    raise LLMError("no model in tests")


@pytest.fixture
def paths(tmp_path) -> Paths:
    return Paths(tmp_path / "warehouse.duckdb", tmp_path / "datasets", tmp_path / "quarantine")


@pytest.fixture
def client(paths, tmp_path) -> TestClient:
    app = create_app(
        paths.db_path,
        results_dir=tmp_path / "no-results",
        mapping_llm_factory=_no_llm,
        paths=paths,
    )
    return TestClient(app)


def _upload(client, path: Path, name: str | None = None, filename: str | None = None):
    with path.open("rb") as handle:
        return client.post(
            "/api/datasets/upload",
            files={"file": (filename or path.name, handle, "text/csv")},
            data={"name": name} if name else {},
        )


def _scoreable_csv(tmp_path: Path) -> Path:
    # About 3 entities x 4 quarters x 60 rows; Ember Textiles deteriorates.
    return make_shipments_csv(
        tmp_path / "three_suppliers.csv",
        suppliers=("Acme Parts", "Cobalt Freight", "Ember Textiles"),
        rows_per_day=2,
        quarters=4,
        country=False,
    )


def _wait(client, run_id: str) -> dict:
    deadline = time.monotonic() + RUN_TIMEOUT_S
    seen = set()
    while time.monotonic() < deadline:
        state = client.get(f"/api/runs/{run_id}").json()
        seen.add((state["status"], state.get("stage")))
        if state["status"] in ("succeeded", "failed"):
            state["seen"] = seen
            return state
        time.sleep(0.5)
    raise AssertionError(f"run {run_id} did not finish: {seen}")


# --- upload ------------------------------------------------------------------------


def test_upload_rejects_a_non_csv_file_and_writes_nothing(client, paths, tmp_path):
    other = tmp_path / "report.xlsx"
    other.write_bytes(b"PK\x03\x04 not a csv")
    response = _upload(client, other)

    assert response.status_code == 400 and "Only .csv" in response.json()["detail"]
    assert not paths.db_path.exists()
    assert not (paths.data_root / "incoming").exists()  # rejected before anything is stored


def test_upload_rejects_an_oversize_file(client, paths, tmp_path, monkeypatch):
    monkeypatch.setenv("DATASET_UPLOAD_MAX_MB", "0.001")  # about 1 KB
    big = tmp_path / "big.csv"
    big.write_text("a,b\n" + "1,2\n" * 2000, encoding="utf-8")
    response = _upload(client, big)

    assert response.status_code == 413 and "upload limit" in response.json()["detail"]
    assert not paths.db_path.exists()
    assert list((paths.data_root / "incoming").glob("*")) == []


def test_upload_onboards_and_a_duplicate_is_detected(client, paths):
    first = _upload(client, FIXTURES / "shipments_clean.csv", name="Clean shipments")
    body = first.json()

    assert first.status_code == 200 and body["status"] == "gated"
    assert body["dataset_id"] and body["gate"]["passed"] and body["gate"]["rules"]
    assert all(
        {"check", "why", "severity", "success"} <= set(rule) for rule in body["gate"]["rules"]
    )
    assert body["profile"]["row_count"] == 200
    assert list((paths.data_root / "incoming").glob("*")) == []  # the upload copy is removed

    again = _upload(client, FIXTURES / "shipments_clean.csv", filename="renamed.csv").json()
    assert again["status"] == "already_registered" and again["dataset_id"] == body["dataset_id"]
    items = client.get("/api/datasets").json()["items"]
    assert [item["dataset_id"] for item in items] == [body["dataset_id"]]


def test_a_quarantined_upload_says_where_the_file_went(client):
    body = _upload(client, FIXTURES / "shipments_dirty.csv").json()

    assert body["status"] == "quarantined"
    assert body["quarantine_path"].endswith(body["dataset_id"])
    hard = [
        rule for rule in body["gate"]["rules"] if rule["severity"] == "hard" and not rule["success"]
    ]
    assert hard and all(rule["why"] for rule in hard)


# --- mapping and runs --------------------------------------------------------------


def _uploaded(client, tmp_path) -> str:
    return _upload(client, _scoreable_csv(tmp_path), name="Three suppliers").json()["dataset_id"]


def test_propose_is_heuristic_first_and_says_which_path_decided(client, tmp_path):
    dataset_id = _uploaded(client, tmp_path)
    body = client.post(f"/api/datasets/{dataset_id}/mapping/propose", json={"by": "Ajanya"}).json()

    assert body["decided_by"] == "heuristic" and body["escalation_reasons"] == []
    assert body["validation"]["decided_by"] == "heuristic"
    assert body["scoreable"] is True


def test_a_run_refuses_an_unmapped_or_unscoreable_dataset(client, tmp_path):
    dataset_id = _uploaded(client, tmp_path)
    refused = client.post(f"/api/datasets/{dataset_id}/run")
    assert refused.status_code == 400 and "approved mapping" in refused.json()["detail"]

    small = _upload(client, FIXTURES / "shipments_clean.csv").json()["dataset_id"]
    mapping_id = client.post(
        f"/api/datasets/{small}/mapping/propose", json={"by": "Ajanya", "heuristic_only": True}
    ).json()["mapping_id"]
    client.post(f"/api/datasets/{small}/mapping/{mapping_id}/approve", json={"by": "Ajanya"})
    not_scoreable = client.post(f"/api/datasets/{small}/run")
    assert not_scoreable.status_code == 400 and "not scoreable" in not_scoreable.json()["detail"]
    assert client.post("/api/datasets/000000000000/run").status_code == 404


def test_a_second_writer_run_is_busy(client, paths, tmp_path):
    dataset_id = _uploaded(client, tmp_path)
    run_id = str(uuid.uuid4())
    # A run that is still going: its status file names a live process (this one).
    runs.write_status(paths.data_root, run_id, status="running", stage="metrics", pid=os.getpid())
    response = client.post(f"/api/datasets/{dataset_id}/run")

    assert response.status_code == 409
    assert response.json()["code"] == "busy" and run_id in response.json()["detail"]


def test_unknown_runs_are_404(client):
    assert client.get(f"/api/runs/{uuid.uuid4()}").status_code == 404
    assert client.get("/api/runs/not-a-run").status_code == 404


def test_full_flow_upload_propose_edit_approve_run_results(client, paths, tmp_path):
    dataset_id = _uploaded(client, tmp_path)
    assert client.get(f"/api/datasets/{dataset_id}/results").status_code == 404

    proposed = client.post(
        f"/api/datasets/{dataset_id}/mapping/propose", json={"by": "Ajanya"}
    ).json()
    invalid = client.post(
        f"/api/datasets/{dataset_id}/mapping/{proposed['mapping_id']}/edit",
        json={"by": "Ajanya", "set": {"entity": "planned_hours"}},
    )
    assert invalid.status_code == 400
    edited = client.post(
        f"/api/datasets/{dataset_id}/mapping/{proposed['mapping_id']}/edit",
        json={"by": "Ajanya", "set": {"notes": "checked the hours columns"}},
    ).json()
    assert edited["source"] == "human" and edited["proposal"]["confidence"] is None
    approved = client.post(
        f"/api/datasets/{dataset_id}/mapping/{edited['mapping_id']}/approve",
        json={"by": "Ajanya", "reason": "looks right"},
    )
    assert approved.json()["dataset_status"] == "mapped"

    started = client.post(f"/api/datasets/{dataset_id}/run").json()
    state = _wait(client, started["run_id"])
    assert state["status"] == "succeeded", state
    assert [stage["status"] for stage in state["stages"]] == ["done"] * 4
    assert state["dataset_id"] == dataset_id and state["finished_at"]

    results = client.get(f"/api/datasets/{dataset_id}/results").json()
    assert results["comparability_note"] == "Scores are comparable within this dataset only."
    assert results["rules_version"] == "1.2-ds1" and results["bounds_mode"] == "dataset"
    assert results["counts"]["entities"] == 3 and results["counts"]["quarters"] == 4
    assert results["counts"]["rows_rejected"] == 3
    assert {reject["reason"] for reject in results["rejects"]} == {
        "entity is empty",
        "entity has no letters or digits",
        "scheduled or actual duration is not a number",
    }
    assert results["scores"] and set(results["bounds"]) == {"late_rate", "delay_stddev"}
    assert results["last_run"]["run_id"] == started["run_id"]
    listed = {item["dataset_id"]: item for item in client.get("/api/datasets").json()["items"]}
    assert listed[dataset_id]["display_status"] == "scored"

    # Excluded factors: no country column, so country risk is out, with its reason,
    # and the remaining weights renormalised.
    factors = {factor["name"]: factor for factor in results["factors"]}
    assert list(factors) == ["late_rate", "delay_stddev", "country_risk"]
    assert factors["country_risk"]["used"] is False
    assert "no country column" in factors["country_risk"]["reason"]
    assert (
        factors["country_risk"]["weight"] == 0.0 and factors["country_risk"]["base_weight"] == 0.4
    )
    assert factors["late_rate"]["weight"] == pytest.approx(0.25 / 0.6)
    assert factors["delay_stddev"]["weight"] == pytest.approx(0.35 / 0.6)
    assert factors["late_rate"]["reason"] is None

    # Flags read through ?dataset=, and a dataset event resolves to its own tables.
    flags = client.get(f"/api/flags?dataset={dataset_id}").json()["items"]
    assert flags == results["flags"]
    if flags:
        flag = flags[0]
        assert flag["event_id"].startswith(f"ds_{dataset_id}:risk_flag:1.2-ds1:")
        detail = client.get(f"/api/flags/{flag['event_id']}").json()
        assert detail["dataset_id"] == dataset_id and detail["supplier_name"]
        score = client.get(
            f"/api/suppliers/{flag['supplier_key']}/score/{flag['quarter']}?dataset={dataset_id}"
        ).json()
        assert score["excluded_factors"]["country_risk"]

    # A second run of the same dataset creates no new events.
    again = _wait(client, client.post(f"/api/datasets/{dataset_id}/run").json()["run_id"])
    assert again["status"] == "succeeded"
    assert client.get(f"/api/flags?dataset={dataset_id}").json()["items"] == flags


def test_a_failed_run_reports_a_plain_error(client, paths, tmp_path):
    csv_path = make_shipments_csv(
        tmp_path / "partial.csv", quarters=3, days_short=20, country=False
    )
    dataset_id = _upload(client, csv_path).json()["dataset_id"]
    mapping_id = client.post(
        f"/api/datasets/{dataset_id}/mapping/propose", json={"by": "Ajanya"}
    ).json()["mapping_id"]
    client.post(f"/api/datasets/{dataset_id}/mapping/{mapping_id}/approve", json={"by": "Ajanya"})
    state = _wait(client, client.post(f"/api/datasets/{dataset_id}/run").json()["run_id"])

    assert state["status"] == "failed"
    assert "only 2 eligible quarter(s)" in state["error"]
    assert any(stage["status"] == "failed" for stage in state["stages"])
    assert client.get(f"/api/datasets/{dataset_id}/results").status_code == 404


# --- DataCo unchanged; localhost only -----------------------------------------------


def test_an_unknown_dataset_parameter_is_404(client):
    _upload(client, FIXTURES / "shipments_clean.csv")  # creates the warehouse
    assert client.get("/api/flags?dataset=nope").status_code == 404
    assert client.get("/api/flags?dataset=000000000000").json() == {"items": [], "count": 0}


def test_requests_from_other_hosts_are_refused(paths, tmp_path):
    app = create_app(
        paths.db_path, results_dir=tmp_path / "r", mapping_llm_factory=_no_llm, paths=paths
    )
    remote = TestClient(app, client=("192.168.1.20", 50000))
    response = remote.get("/api/health")

    assert response.status_code == 403 and "localhost" in response.json()["detail"]
    assert TestClient(app).get("/api/health").status_code == 200


def test_run_status_file_is_written_atomically(paths):
    run_id = str(uuid.uuid4())
    runs.write_status(paths.data_root, run_id, status="queued", stage=None, dataset_id="x")
    runs.write_status(paths.data_root, run_id, status="running", stage="silver")
    status = json.loads(runs.status_path(paths.data_root, run_id).read_text(encoding="utf-8"))

    assert status["status"] == "running" and status["dataset_id"] == "x"
    assert runs.stage_view("running", "metrics") == [
        {"name": "silver", "status": "done"},
        {"name": "metrics", "status": "running"},
        {"name": "scoring", "status": "pending"},
        {"name": "flags", "status": "pending"},
    ]
