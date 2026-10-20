import json
import logging
from pathlib import Path

import duckdb
import pytest

from provenance.agent import approve as approve_cli
from provenance.agent.approval import (
    ApprovalError,
    ApprovalRequired,
    apply,
    approve,
    list_actions,
    reject,
    sanitize_action,
)
from provenance.agent.grounding import GroundingResult, check_grounding
from provenance.agent.loop import InvestigationResult
from provenance.agent.run import record_outcome
from provenance.agent.store import verify_audit_log, writer
from provenance.agent.tools.investigation_tools import InvestigationTools
from tests.unit.agent_fixtures import EARLIER_EVENT_ID, EVENT_ID

FLAG = {"supplier_key": "cat_widgets", "quarter": "2017-Q1", "score": 0.735}


def _outcome(event_id=EVENT_ID, summary="Score 0.735 in 2017-Q1.", action="watchlist"):
    report = {
        "summary": summary,
        "top_factor": "delay_stddev",
        "evidence": [{"tool_name": "get_flag", "value": "score=0.735"}],
        "proposed_action": action,
        "confidence": "high",
    }
    tool_calls = [{"step": 0, "name": "get_flag", "arguments": "{}", "result": FLAG}]
    result = InvestigationResult(event_id, report, 2, "final_answer", tool_calls, 100, 20)
    return result, check_grounding(report, tool_calls)


def _record(db_path, **kwargs):
    result, grounding = _outcome(**kwargs)
    return record_outcome(db_path, result, grounding, "test-model", True)


def _rows(db_path, sql):
    with duckdb.connect(str(db_path), read_only=True) as connection:
        return connection.execute(sql).fetchall()


def _audit_events(db_path):
    return [row[0] for row in _rows(db_path, "SELECT event FROM agent.audit_log ORDER BY seq")]


# -- allow-list -----------------------------------------------------------------


def test_sanitize_action_allows_only_the_three_actions():
    assert sanitize_action("watchlist") == ("watchlist", False)
    assert sanitize_action("request_review") == ("request_review", False)
    assert sanitize_action("no_action") == ("no_action", False)
    assert sanitize_action("suspend_supplier") == ("no_action", True)
    assert sanitize_action(None) == ("no_action", True)
    assert sanitize_action("Watchlist") == ("no_action", True)


def test_disallowed_model_action_is_stored_as_no_action_with_audit(warehouse):
    recorded = _record(warehouse, action="suspend_supplier")

    assert recorded.status == "proposed"
    assert _rows(warehouse, "SELECT action_type, status FROM agent.actions") == [
        ("no_action", "proposed")
    ]
    assert "action_type_rejected" in _audit_events(warehouse)
    detail = _rows(
        warehouse,
        "SELECT detail FROM agent.audit_log WHERE event = 'action_type_rejected'",
    )[0][0]
    assert json.loads(detail)["proposed"] == "suspend_supplier"


def test_action_takes_supplier_and_quarter_from_the_event_not_the_model(warehouse):
    _record(warehouse)

    assert _rows(warehouse, "SELECT supplier_key, quarter::VARCHAR FROM agent.actions") == [
        ("cat_widgets", "2017-01-01")
    ]


# -- ungrounded -------------------------------------------------------------------


def test_ungrounded_report_creates_no_action(warehouse):
    recorded = _record(warehouse, summary="Score 0.735, up from 0.512.")

    assert recorded.status == "rejected_ungrounded"
    assert recorded.action_id is None
    assert _rows(warehouse, "SELECT count(*) FROM agent.actions") == [(0,)]
    assert _rows(warehouse, "SELECT status, grounded FROM agent.investigations") == [
        ("rejected_ungrounded", False)
    ]
    assert _audit_events(warehouse) == ["investigation_rejected_ungrounded"]


# -- approval -------------------------------------------------------------------


def test_apply_without_approval_fails_even_when_approval_is_switched_off(
    warehouse, monkeypatch, caplog
):
    monkeypatch.setenv("AGENT_REQUIRE_APPROVAL", "false")
    action_id = _record(warehouse).action_id

    with caplog.at_level(logging.WARNING), pytest.raises(ApprovalRequired):
        apply(warehouse, action_id, "someone")

    assert "never applied without an explicit approval" in caplog.text
    assert _rows(warehouse, "SELECT count(*) FROM agent.watchlist") == [(0,)]
    assert _rows(warehouse, "SELECT status FROM agent.actions") == [("proposed",)]
    assert _audit_events(warehouse)[-1] == "apply_refused"


def test_a_forged_status_without_an_approval_audit_is_refused(warehouse):
    action_id = _record(warehouse).action_id
    with duckdb.connect(str(warehouse)) as connection:
        connection.execute(
            "UPDATE agent.actions SET status = 'approved', decided_by = 'mallory', "
            "decided_at = now()"
        )

    with pytest.raises(ApprovalRequired):
        apply(warehouse, action_id, "mallory")
    assert _rows(warehouse, "SELECT count(*) FROM agent.watchlist") == [(0,)]


def test_approve_applies_watchlist_and_records_the_decision(warehouse):
    action_id = _record(warehouse).action_id

    result = approve(warehouse, action_id[:8], "Ajanya", "Delay variance is real")

    assert result == {"applied": True, "action_id": action_id, "effect": "agent.watchlist"}
    assert _rows(warehouse, "SELECT action_id, supplier_key, added_by FROM agent.watchlist") == [
        (action_id, "cat_widgets", "Ajanya")
    ]
    assert _rows(warehouse, "SELECT status, decided_by FROM agent.actions") == [
        ("applied", "Ajanya")
    ]
    assert _rows(
        warehouse, "SELECT human_decision, decision_reason, decided_by FROM agent.investigations"
    ) == [("approved", "Delay variance is real", "Ajanya")]
    assert _audit_events(warehouse)[-2:] == ["action_approved", "action_applied"]


def test_approve_request_review_writes_review_requests(warehouse):
    action_id = _record(warehouse, action="request_review").action_id

    approve(warehouse, action_id, "Ajanya")

    assert _rows(warehouse, "SELECT action_id FROM agent.review_requests") == [(action_id,)]
    assert _rows(warehouse, "SELECT count(*) FROM agent.watchlist") == [(0,)]


def test_reject_applies_nothing_and_needs_a_reason(warehouse):
    action_id = _record(warehouse).action_id
    with pytest.raises(ApprovalError, match="reason"):
        reject(warehouse, action_id, "Ajanya", "  ")

    reject(warehouse, action_id, "Ajanya", "Seasonal noise")

    assert _rows(warehouse, "SELECT status FROM agent.actions") == [("rejected",)]
    assert _rows(warehouse, "SELECT human_decision FROM agent.investigations") == [("rejected",)]
    with pytest.raises(ApprovalRequired):
        apply(warehouse, action_id, "Ajanya")
    with pytest.raises(ApprovalError, match="already rejected"):
        approve(warehouse, action_id, "Ajanya")


def test_decisions_need_a_name_and_a_unique_id(warehouse):
    action_id = _record(warehouse).action_id
    with pytest.raises(ApprovalError, match="--by"):
        approve(warehouse, action_id, "")
    with pytest.raises(ApprovalError, match="at least 8"):
        approve(warehouse, action_id[:4], "Ajanya")
    with pytest.raises(ApprovalError, match="No action"):
        approve(warehouse, "ffffffff-0000", "Ajanya")


def test_approve_cli_lists_and_approves(warehouse, capsys):
    action_id = _record(warehouse).action_id

    assert approve_cli.main(["--list", "--db-path", str(warehouse)]) == 0
    assert action_id in capsys.readouterr().out

    code = approve_cli.main(["--approve", action_id, "--by", "Ajanya", "--db-path", str(warehouse)])
    assert code == 0
    assert "agent.watchlist" in capsys.readouterr().out
    assert list_actions(warehouse) == []
    assert (
        approve_cli.main(["--approve", action_id, "--by", "Ajanya", "--db-path", str(warehouse)])
        == 2
    )


# -- audit log ------------------------------------------------------------------


def test_audit_log_is_append_only_and_tamper_evident(warehouse):
    first = _record(warehouse).action_id
    before = _rows(warehouse, "SELECT * FROM agent.audit_log ORDER BY seq")
    approve(warehouse, first, "Ajanya")
    _record(warehouse, summary="Score 0.735, was 0.512.")
    after = _rows(warehouse, "SELECT * FROM agent.audit_log ORDER BY seq")

    assert after[: len(before)] == before
    assert [row[0] for row in after] == list(range(1, len(after) + 1))
    with duckdb.connect(str(warehouse)) as connection:
        assert verify_audit_log(connection) == []
        connection.execute("UPDATE agent.audit_log SET actor = 'mallory' WHERE seq = 2")
        assert verify_audit_log(connection) == [2]


def test_agent_code_never_updates_or_deletes_audit_rows():
    agent_dir = Path(__file__).resolve().parents[2] / "src" / "provenance" / "agent"
    for path in agent_dir.rglob("*.py"):
        source = path.read_text(encoding="utf-8").upper()
        assert "UPDATE AGENT.AUDIT_LOG" not in source, path
        assert "DELETE FROM AGENT.AUDIT_LOG" not in source, path


def test_failed_write_rolls_back_the_whole_transaction(warehouse):
    with pytest.raises(RuntimeError), writer(warehouse) as connection:
        connection.execute("INSERT INTO agent.watchlist VALUES ('x', 'e', 's', NULL, 'a', now())")
        raise RuntimeError("boom")

    assert _rows(warehouse, "SELECT count(*) FROM agent.watchlist") == [(0,)]


# -- memory ---------------------------------------------------------------------


def test_recall_returns_only_grounded_investigations(warehouse):
    grounded = _record(warehouse, event_id=EARLIER_EVENT_ID).action_id
    _record(warehouse, event_id=EVENT_ID, summary="Score 0.735, was 0.512.")
    result, _ = _outcome(event_id=EVENT_ID)
    result.report = None

    record_outcome(warehouse, result, GroundingResult(False, 0.0, 0), "test-model", True)
    approve(warehouse, grounded, "Ajanya", "Confirmed")

    with InvestigationTools(warehouse) as tools:
        recalled = tools.call("recall_past_investigations", {"supplier_key": "cat_widgets"})

    assert recalled == {
        "supplier_key": "cat_widgets",
        "count": 1,
        "past_investigations": [
            {
                "quarter": "2016-Q4",
                "old_tier": "low",
                "new_tier": "medium",
                "score": 0.735,
                "top_factor": "delay_stddev",
                "proposed_action": "watchlist",
                "human_decision": "approved",
                "decision_reason": "Confirmed",
            }
        ],
    }


def test_recall_validates_arguments_and_handles_no_history(warehouse):
    with InvestigationTools(warehouse) as tools:
        assert tools.call("recall_past_investigations", {"supplier_key": "cat_widgets"}) == {
            "supplier_key": "cat_widgets",
            "count": 0,
            "past_investigations": [],
        }
        assert "error" in tools.call("recall_past_investigations", {"supplier_key": "cat_unknown"})
        assert "error" in tools.call(
            "recall_past_investigations", {"supplier_key": "cat_widgets", "limit": 50}
        )
