"""Phase 10d: starting the agent from the console. It only ever proposes.

The model is scripted and the runner stubbed; the one test that starts the real
subprocess points the model client at a closed local port, so nothing leaves this machine.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from urllib.parse import quote

import duckdb
import pytest
from fastapi.testclient import TestClient

from provenance.agent import background
from provenance.agent.llm import LLMResponse
from provenance.agent.store import verify_audit_log
from provenance.api.main import create_app
from provenance.datasets import runs
from provenance.datasets.onboard import Paths
from tests.unit.agent_fixtures import EVENT_ID

RUN_URL = f"/api/investigations/{quote(EVENT_ID, safe='')}/run"


class Model:
    """Answers with one report, straight away."""

    model = "scripted-model"

    def __init__(self, summary: str = "cat_widgets scored 0.735 in 2017-Q1.") -> None:
        self.summary = summary

    def chat(self, messages, tools=None, tool_choice="auto"):
        report = {
            "summary": self.summary,
            "top_factor": "delay_stddev",
            "evidence": [{"tool_name": "get_flag", "value": "score=0.735"}],
            "proposed_action": "watchlist",
            "confidence": "high",
        }
        return LLMResponse(json.dumps(report), [], 200, 50, "stop")


@pytest.fixture
def paths(warehouse, tmp_path) -> Paths:
    return Paths(warehouse, tmp_path / "datasets", tmp_path / "quarantine")


def _client(paths, tmp_path, *, key: bool = True, started: list | None = None) -> TestClient:
    def runner(db_path, data_root, event_id):
        (started if started is not None else []).append(event_id)
        return str(uuid.uuid4())

    return TestClient(
        create_app(
            paths.db_path,
            results_dir=tmp_path / "no-results",
            paths=paths,
            agent_llm_check=lambda: key,
            agent_runner=runner,
        )
    )


def _rows(db, sql, params=None):
    with duckdb.connect(str(db), read_only=True) as connection:
        return connection.execute(sql, params or []).fetchall()


def test_the_run_starts_in_the_background_and_returns_a_run_id(paths, tmp_path):
    started: list[str] = []
    response = _client(paths, tmp_path, started=started).post(RUN_URL)

    assert response.status_code == 200
    assert response.json()["status"] == "queued" and uuid.UUID(response.json()["run_id"])
    assert started == [EVENT_ID]


def test_the_run_refuses_when_a_writer_is_busy(paths, tmp_path):
    started: list[str] = []
    busy_run = str(uuid.uuid4())
    runs.write_status(paths.data_root, busy_run, status="running", stage="silver", pid=os.getpid())
    response = _client(paths, tmp_path, started=started).post(RUN_URL)

    assert response.status_code == 409
    assert response.json()["code"] == "busy" and busy_run in response.json()["detail"]
    assert started == []


def test_the_run_refuses_when_the_key_is_missing(paths, tmp_path):
    started: list[str] = []
    response = _client(paths, tmp_path, key=False, started=started).post(RUN_URL)

    assert response.status_code == 400 and "LLM_API_KEY" in response.json()["detail"]
    assert started == []


def test_a_blank_key_counts_as_missing_even_with_a_dotenv(paths, tmp_path, monkeypatch):
    # load_dotenv never overrides a variable that is already set, so "" stays "".
    monkeypatch.setenv("LLM_API_KEY", "")
    client = TestClient(create_app(paths.db_path, results_dir=tmp_path / "no-results", paths=paths))

    assert background.llm_configured() is False
    assert client.post(RUN_URL).status_code == 400


def test_the_run_refuses_unknown_and_resolved_events(paths, tmp_path):
    client = _client(paths, tmp_path)
    assert client.post("/api/investigations/risk_flag%3Anope/run").status_code == 404

    with duckdb.connect(str(paths.db_path)) as connection:
        connection.execute(
            "UPDATE agent.events SET resolved_at = now() WHERE event_id = ?", [EVENT_ID]
        )
    response = client.post(RUN_URL)
    assert response.status_code == 400 and "resolved" in response.json()["detail"]


@pytest.mark.parametrize("require_approval", ["true", "false"])
def test_a_grounded_run_only_proposes_and_never_approves(paths, monkeypatch, require_approval):
    # Even with the approval flag switched off, nothing is approved or applied.
    monkeypatch.setenv("AGENT_REQUIRE_APPROVAL", require_approval)
    run_id = str(uuid.uuid4())
    code = background.execute(run_id, EVENT_ID, paths.db_path, paths.data_root, llm_factory=Model)

    assert code == 0
    state = runs.run_state(paths.db_path, paths.data_root, run_id)
    assert state["kind"] == "investigation" and state["status"] == "succeeded"
    assert state["grounded"] is True and state["action_id"]
    assert [stage["status"] for stage in state["stages"]] == ["done", "done"]

    actions = _rows(
        paths.db_path, "SELECT status, decided_by, decided_at, applied_at FROM agent.actions"
    )
    assert actions == [("proposed", None, None, None)]
    assert _rows(paths.db_path, "SELECT count(*) FROM agent.watchlist") == [(0,)]
    events = [row[0] for row in _rows(paths.db_path, "SELECT event FROM agent.audit_log")]
    assert "action_proposed" in events
    assert not {"action_approved", "action_applied"} & set(events)
    with duckdb.connect(str(paths.db_path), read_only=True) as connection:
        assert verify_audit_log(connection) == []


def test_an_ungrounded_run_creates_no_action(paths):
    run_id = str(uuid.uuid4())

    def model():
        return Model("cat_widgets scored 0.512 a year earlier.")

    background.execute(run_id, EVENT_ID, paths.db_path, paths.data_root, llm_factory=model)

    state = runs.run_state(paths.db_path, paths.data_root, run_id)
    assert state["status"] == "succeeded" and state["grounded"] is False
    assert state["investigation_status"] == "rejected_ungrounded" and state["action_id"] is None
    assert _rows(paths.db_path, "SELECT count(*) FROM agent.actions") == [(0,)]


def test_the_real_subprocess_reports_a_failure_plainly(paths, monkeypatch, tmp_path):
    # Blanking the key is not enough: Windows drops empty variables from a child's
    # environment, so the child would load the real key from .env. A closed port is
    # what guarantees no model call.
    monkeypatch.setenv("LLM_API_KEY", "not-a-real-key")
    monkeypatch.setenv("GROQ_BASE_URL", "http://127.0.0.1:9")
    run_id = background.start(paths.db_path, paths.data_root, EVENT_ID)
    client = _client(paths, tmp_path)
    deadline = time.monotonic() + 120
    while True:
        state = client.get(f"/api/runs/{run_id}").json()
        if state["status"] in ("succeeded", "failed") or time.monotonic() > deadline:
            break
        time.sleep(0.5)

    assert state["status"] == "failed" and state["event_id"] == EVENT_ID
    assert "could not be reached" in state["error"] and "not-a-real-key" not in state["error"]
    # Nothing was recorded: the failed run never even created the agent tables.
    assert not _rows(
        paths.db_path,
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema = 'agent' AND table_name = 'investigations'",
    )


def test_the_agent_summary_counts_work_and_pending_proposals(paths, tmp_path):
    client = _client(paths, tmp_path)
    empty = client.get("/api/agent/summary").json()
    assert empty["investigations"] == 0 and empty["grounded_rate"] is None

    background.execute(
        str(uuid.uuid4()), EVENT_ID, paths.db_path, paths.data_root, llm_factory=Model
    )
    summary = client.get("/api/agent/summary").json()

    assert summary["investigations"] == 1 and summary["grounded"] == 1
    assert summary["grounded_rate"] == 1.0 and summary["pending_approvals"] == 1
    assert summary["last_agent_action"]["event"] == "action_proposed"
    assert summary["last_agent_action"]["event_id"] == EVENT_ID
    assert summary["model"]
