import json
from urllib.parse import quote

import duckdb
import pytest
from fastapi.testclient import TestClient

from provenance.agent import approval
from provenance.agent.grounding import check_grounding
from provenance.agent.loop import InvestigationResult
from provenance.agent.run import record_outcome
from provenance.api.main import create_app
from tests.unit.agent_fixtures import EVENT_ID, FACTORS

BLOB_MARKER = "RAW-GE-RESULT-BLOB"
WARNING_TEXT = "column city has unexpected nulls"


@pytest.fixture
def db(warehouse):
    with duckdb.connect(str(warehouse)) as connection:
        connection.execute("CREATE SCHEMA bronze")
        connection.execute(
            """
            CREATE TABLE gold.supplier_risk_flags (
                supplier_key VARCHAR, quarter DATE, old_tier VARCHAR, new_tier VARCHAR,
                score DOUBLE, top_contributing_factor JSON, confidence VARCHAR
            )
            """
        )
        connection.execute(
            "INSERT INTO gold.supplier_risk_flags VALUES "
            "('cat_widgets', '2017-01-01', 'medium', 'high', 0.734948, ?, 'full')",
            [json.dumps({"name": "delay_stddev", **FACTORS["delay_stddev"]})],
        )
        connection.execute(
            """
            CREATE TABLE bronze.dq_results (
                batch_id VARCHAR, source VARCHAR, status VARCHAR,
                passed_expectations INTEGER, failed_expectations INTEGER,
                checked_at TIMESTAMPTZ, result JSON, warnings JSON
            )
            """
        )
        connection.execute(
            "INSERT INTO bronze.dq_results VALUES "
            "('b1', 'dataco_shipments', 'quarantined', 49, 1, now(), ?, ?), "
            "('b2', 'worldbank_lpi', 'validated', 8, 0, now(), '{}', '[]')",
            [json.dumps({"failed_details": [BLOB_MARKER]}), json.dumps([WARNING_TEXT])],
        )
    return warehouse


@pytest.fixture
def client(db, tmp_path):
    return TestClient(create_app(db, results_dir=tmp_path / "no-results"))


def _investigate(db, summary="cat_widgets scored 0.735 in 2017-Q1.", action="watchlist"):
    flag = {"supplier_key": "cat_widgets", "quarter": "2017-Q1", "score": 0.735}
    report = {
        "summary": summary,
        "top_factor": "delay_stddev",
        "evidence": [{"tool_name": "get_flag", "value": "score=0.735"}],
        "proposed_action": action,
        "confidence": "high",
    }
    calls = [{"step": 0, "name": "get_flag", "arguments": "{}", "result": flag}]
    result = InvestigationResult(EVENT_ID, report, 2, "final_answer", calls, 900, 100)
    return record_outcome(db, result, check_grounding(report, calls), "test-model", True)


def _path(event_id):
    return quote(event_id, safe="")


def test_health_reports_model_and_mode_but_no_secrets(client, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "gsk_super_secret_value")
    monkeypatch.setenv("LLM_MODEL_NAME", "openai/gpt-oss-120b")

    body = client.get("/api/health").json()

    assert body == {
        "status": "ok",
        "model": "openai/gpt-oss-120b",
        "approval_required": True,
        "db_reachable": True,
    }


def test_no_endpoint_returns_env_secrets(client, db, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "gsk_super_secret_value")
    _investigate(db)
    urls = [
        "/api/health",
        "/api/flags",
        f"/api/flags/{_path(EVENT_ID)}",
        f"/api/investigations/{_path(EVENT_ID)}",
        "/api/approvals",
        "/api/audit",
        "/api/quality",
    ]
    for url in urls:
        response = client.get(url)
        assert response.status_code == 200, url
        assert "gsk_super_secret_value" not in response.text, url


def test_flags_list_and_filters(client):
    items = client.get("/api/flags").json()["items"]

    assert [item["event_id"] for item in items] == [EVENT_ID]
    assert items[0]["quarter"] == "2017-Q1"
    assert items[0]["score"] == 0.735
    assert items[0]["top_factor"]["name"] == "delay_stddev"
    assert items[0]["supplier_name"] == "Widgets"
    assert client.get("/api/flags?tier=low").json()["count"] == 0
    assert client.get("/api/flags?quarter=2017-Q1&confidence=full").json()["count"] == 1
    assert client.get("/api/flags?tier=extreme").status_code == 400
    assert client.get("/api/flags?quarter=Q9").status_code == 400


def test_detail_endpoints_reuse_the_agent_tools(client):
    flag = client.get(f"/api/flags/{_path(EVENT_ID)}").json()
    assert flag["top_factor"]["raw_value"] == 1.615
    assert flag["supplier_name"] == "Widgets"

    history = client.get("/api/suppliers/cat_widgets/history").json()
    assert [version["version"] for version in history["versions"]] == [2, 1]

    score = client.get("/api/suppliers/cat_widgets/score/2017-Q1").json()
    assert score["factors"][0]["factor"] == "delay_stddev"

    markets = client.get("/api/suppliers/cat_widgets/markets/2017-Q1").json()
    assert markets["markets"][0]["market"] == "LATAM"

    assert client.get("/api/suppliers/cat_nobody/history").status_code == 404
    assert client.get(f"/api/flags/{_path('risk_flag:missing')}").status_code == 404


def test_investigation_ties_every_number_to_its_tool(client, db):
    _investigate(db)

    body = client.get(f"/api/investigations/{_path(EVENT_ID)}").json()

    assert body["status"] == "proposed" and body["grounded"] is True
    numbers = [span for span in body["report"]["summary_spans"] if span["kind"] == "number"]
    assert [(span["text"], span["tool"]) for span in numbers] == [
        ("0.735", "get_flag"),
        ("2017", "get_flag"),
    ]
    assert "".join(span["text"] for span in body["report"]["summary_spans"]) == (
        "cat_widgets scored 0.735 in 2017-Q1."
    )
    assert body["report"]["evidence"][0]["spans"][1] == {
        "kind": "number",
        "text": "0.735",
        "tool": "get_flag",
    }
    assert body["action"]["action_type"] == "watchlist"


def test_ungrounded_investigation_has_no_action(client, db):
    _investigate(db, summary="Score 0.735, up from 0.512.")

    body = client.get(f"/api/investigations/{_path(EVENT_ID)}").json()

    assert body["status"] == "rejected_ungrounded"
    assert body["action"] is None
    assert body["ungrounded_numbers"] == ["0.512"]
    unsourced = [s for s in body["report"]["summary_spans"] if s.get("tool") is None]
    assert [s["text"] for s in unsourced if s["kind"] == "number"] == ["0.512"]


def test_missing_investigation_is_404(client):
    assert client.get(f"/api/investigations/{_path(EVENT_ID)}").status_code == 404


@pytest.mark.parametrize("by", ["", "   "])
def test_approve_with_empty_by_fails_and_changes_nothing(client, db, by):
    action_id = _investigate(db).action_id

    response = client.post(f"/api/approvals/{action_id}/approve", json={"by": by})

    assert response.status_code == 400
    assert "by" in response.json()["detail"]
    with duckdb.connect(str(db), read_only=True) as connection:
        assert connection.execute("SELECT status FROM agent.actions").fetchall() == [("proposed",)]
        assert connection.execute("SELECT count(*) FROM agent.watchlist").fetchone() == (0,)


def test_approve_goes_through_the_guardrail(client, db):
    action_id = _investigate(db).action_id

    response = client.post(
        f"/api/approvals/{action_id}/approve", json={"by": "Ajanya", "reason": "ok"}
    )

    assert response.status_code == 200
    assert response.json()["effect"] == "agent.watchlist"
    items = client.get("/api/approvals").json()
    assert items["pending"] == 0
    assert [entry["event"] for entry in items["items"][0]["audit"]] == [
        "action_proposed",
        "action_approved",
        "action_applied",
    ]
    again = client.post(f"/api/approvals/{action_id}/approve", json={"by": "Ajanya"})
    assert again.status_code == 409


def test_reject_needs_a_reason(client, db):
    action_id = _investigate(db).action_id

    assert (
        client.post(f"/api/approvals/{action_id}/reject", json={"by": "Ajanya"}).status_code == 400
    )
    response = client.post(
        f"/api/approvals/{action_id}/reject", json={"by": "Ajanya", "reason": "noise"}
    )
    assert response.json() == {"status": "rejected", "action_id": action_id}
    assert (
        client.post("/api/approvals/ffffffff-0000/approve", json={"by": "Ajanya"}).status_code
        == 404
    )


def test_locked_database_returns_409(client, db, monkeypatch):
    action_id = _investigate(db).action_id

    def locked(*_args, **_kwargs):
        raise duckdb.IOException("Could not set lock on file")

    monkeypatch.setattr(approval, "approve", locked)
    response = client.post(f"/api/approvals/{action_id}/approve", json={"by": "Ajanya"})

    assert response.status_code == 409
    assert response.json()["code"] == "locked"
    assert "Dagster" in response.json()["detail"]


def test_quality_never_returns_the_raw_details_blob(client):
    response = client.get("/api/quality")

    assert BLOB_MARKER not in response.text
    assert WARNING_TEXT not in response.text
    items = response.json()["items"]
    assert {item["source"] for item in items} == {"dataco_shipments", "worldbank_lpi"}
    for item in items:
        assert set(item) == {
            "source",
            "batch_id",
            "status",
            "passed",
            "passed_expectations",
            "failed_expectations",
            "warning_count",
            "checked_at",
        }
    quarantined = next(item for item in items if item["source"] == "dataco_shipments")
    assert quarantined["passed"] is False and quarantined["warning_count"] == 1


def test_audit_reports_chain_verification(client, db):
    _investigate(db)
    assert client.get("/api/audit").json()["chain"]["status"] == "intact"

    with duckdb.connect(str(db)) as connection:
        connection.execute("UPDATE agent.audit_log SET actor = 'mallory' WHERE seq = 1")

    chain = client.get("/api/audit").json()["chain"]
    assert chain["status"] == "broken" and chain["broken_seqs"] == [1]


def test_get_endpoints_are_read_only(client, db):
    for url in ["/api/flags", "/api/approvals", "/api/audit", "/api/quality"]:
        client.get(url)

    with duckdb.connect(str(db), read_only=True) as connection:
        tables = connection.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'agent'"
        ).fetchall()
    assert tables == [("events",)]


def test_cors_allows_only_the_console_origin(client):
    allowed = client.options(
        "/api/flags",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"},
    )
    other = client.get("/api/flags", headers={"Origin": "http://evil.example"})

    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "access-control-allow-origin" not in other.headers


def test_evaluation_is_404_until_a_report_exists(db, tmp_path):
    results = tmp_path / "results"
    client = TestClient(create_app(db, results_dir=results))
    assert client.get("/api/evaluation").status_code == 404

    results.mkdir()
    (results / "report.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-10-20T03:19:44+00:00",
                "model": "m",
                "case_count": 2,
                "metrics": {"grounded_rate": {"value": 0.5, "n": 2}},
            }
        ),
        encoding="utf-8",
    )
    assert client.get("/api/evaluation").json()["metrics"] == {"grounded_rate": 0.5}


def test_dataco_is_the_default_and_dataset_dataco_returns_the_same_data(client):
    # Phase 10c-2: ?dataset= selects an onboarded dataset; DataCo answers are unchanged.
    for path in ("/api/flags", f"/api/flags/{_path(EVENT_ID)}"):
        assert client.get(path).json() == client.get(f"{path}?dataset=dataco").json()
    assert client.get("/api/flags?dataset=dataco").json()["items"][0]["event_id"] == EVENT_ID
    assert "dataset_id" not in client.get(f"/api/flags/{_path(EVENT_ID)}").json()
    assert client.get("/api/flags?dataset=not-an-id").status_code == 404
