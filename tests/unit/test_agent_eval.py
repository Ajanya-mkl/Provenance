import json

import duckdb

from provenance.agent.eval.eval_runner import compute_metrics, load_cases, run_eval, score_case
from provenance.agent.grounding import GroundingResult
from provenance.agent.llm import LLMResponse
from provenance.agent.loop import InvestigationResult
from tests.unit.agent_fixtures import EARLIER_EVENT_ID, EVENT_ID

CASES = [
    {
        "case_id": "widgets_2016q4",
        "kind": "flag",
        "event_id": EARLIER_EVENT_ID,
        "expected_top_factor": "delay_stddev",
        "expect_insufficient_data": False,
        "expect_recall": False,
        "synthetic_event": None,
    },
    {
        "case_id": "repeat_widgets_2017q1",
        "kind": "edge_repeat",
        "event_id": EVENT_ID,
        "expected_top_factor": "delay_stddev",
        "expect_insufficient_data": False,
        "expect_recall": True,
        "synthetic_event": None,
    },
    {
        "case_id": "edge_unknown_supplier",
        "kind": "edge_unknown_supplier",
        "event_id": "eval:edge:unknown_supplier",
        "expected_top_factor": None,
        "expect_insufficient_data": True,
        "expect_recall": False,
        "synthetic_event": {
            "payload": {
                "supplier_key": "cat_nobody",
                "quarter": "2017-01-01",
                "new_tier": "high",
                "score": 0.9,
                "confidence": "full",
                "top_contributing_factor": {"name": "late_rate"},
            }
        },
    },
    {
        "case_id": "wrong_factor",
        "kind": "flag",
        "event_id": EVENT_ID,
        "expected_top_factor": "late_rate",
        "expect_insufficient_data": False,
        "expect_recall": False,
        "synthetic_event": None,
    },
]


class FakeModel:
    """Answers in one step from the prelude's tool results, like a careful model."""

    def __init__(self):
        self.calls = 0

    def chat(self, messages, tools=None, tool_choice="auto"):
        self.calls += 1
        results = [json.loads(m["content"]) for m in messages if m["role"] == "tool"]
        flag = results[0]
        recall = results[1] if len(results) > 1 else {"error": "none"}
        if "error" in recall:
            report = {
                "summary": "insufficient data: the supplier is unknown.",
                "top_factor": "unknown",
                "evidence": [{"tool_name": "get_flag", "value": "error"}],
                "proposed_action": "no_action",
                "confidence": "low",
            }
        else:
            past = recall["past_investigations"]
            memory = f" Matches the {past[0]['quarter']} case." if past else ""
            report = {
                "summary": f"{flag['supplier_key']} scored {flag['score']}.{memory}",
                "top_factor": flag["top_factor"]["name"],
                "evidence": [{"tool_name": "get_flag", "value": f"score={flag['score']}"}],
                "proposed_action": "watchlist",
                "confidence": "high",
            }
        return LLMResponse(json.dumps(report), [], 300, 40, "stop")


def _run(warehouse, tmp_path, factory, fresh=False):
    return run_eval(
        CASES,
        warehouse,
        factory,
        "fake-model",
        results_dir=tmp_path / "results",
        doc_path=tmp_path / "agent_evaluation.md",
        fresh=fresh,
        sleep_seconds=0,
        log=lambda _: None,
    )


def test_eval_runner_computes_metrics_on_a_fake_model(warehouse, tmp_path):
    model = FakeModel()

    report = _run(warehouse, tmp_path, lambda: model)
    metrics = {name: value["value"] for name, value in report["metrics"].items()}

    assert metrics["top_factor_accuracy"] == round(2 / 3, 3)
    assert metrics["grounded_rate"] == 1.0
    assert metrics["valid_action_rate"] == 1.0
    assert metrics["edge_case_handled_rate"] == 1.0
    assert metrics["memory_recall_rate"] == 1.0
    assert metrics["low_confidence_handled_rate"] is None
    assert metrics["guardrail_violations"] == 0
    assert metrics["avg_steps"] == 1.0
    assert metrics["avg_tokens"] == 340.0
    assert model.calls == 4
    failures = {case["case_id"]: case["failures"] for case in report["cases"]}
    assert failures["wrong_factor"] == ["top factor 'delay_stddev', expected 'late_rate'"]
    assert failures["repeat_widgets_2017q1"] == []
    doc = (tmp_path / "agent_evaluation.md").read_text(encoding="utf-8")
    assert "fake-model" in doc and "| top_factor_accuracy | 0.667 |" in doc
    assert "**wrong_factor**" in doc


def test_eval_never_writes_to_the_source_database(warehouse, tmp_path):
    _run(warehouse, tmp_path, FakeModel)

    with duckdb.connect(str(warehouse), read_only=True) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'agent'"
            ).fetchall()
        }
        synthetic = connection.execute(
            "SELECT count(*) FROM agent.events WHERE event_id LIKE 'eval:%'"
        ).fetchone()[0]
    assert tables == {"events"}
    assert synthetic == 0


def test_eval_uses_the_cache_on_rerun(warehouse, tmp_path):
    first = _run(warehouse, tmp_path, FakeModel)

    def must_not_build():
        raise AssertionError("a cached run must not create an LLM client")

    second = _run(warehouse, tmp_path, must_not_build)

    assert second["cached_cases"] == len(CASES) and second["live_cases"] == 0
    assert second["metrics"] == first["metrics"]
    # Memory still works from cache: replayed cases are recorded into the copy.
    assert {c["case_id"]: c["recalled_cases"] for c in second["cases"]}[
        "repeat_widgets_2017q1"
    ] == 1

    fresh_model = FakeModel()
    third = _run(warehouse, tmp_path, lambda: fresh_model, fresh=True)
    assert third["live_cases"] == len(CASES) and fresh_model.calls == len(CASES)


def test_score_case_low_confidence_needs_insufficient_data_or_no_action():
    case = {
        "case_id": "c",
        "kind": "low_confidence",
        "expected_top_factor": "delay_stddev",
        "expect_insufficient_data": True,
    }
    report = {
        "summary": "Tier moved to high.",
        "top_factor": "delay_stddev",
        "proposed_action": "request_review",
    }
    result = InvestigationResult("e", report, 2, "final_answer", [], 1, 1)

    score = score_case(case, result, GroundingResult(True, 1.0, 1))

    assert score["insufficient_data_handled"] is False
    assert score["failures"] == ["expected 'insufficient data' or no_action, got 'request_review'"]
    metrics = compute_metrics([score], [1.5], 0)
    assert metrics["low_confidence_handled_rate"] == {"value": 0.0, "n": 1}
    assert metrics["avg_latency_s"] == {"value": 1.5, "n": 1}


def test_shipped_cases_cover_the_required_mix():
    cases = load_cases()
    kinds = [case["kind"] for case in cases]
    factors = {case["expected_top_factor"] for case in cases} - {None}

    assert 15 <= len(cases) <= 20
    assert factors == {"delay_stddev", "late_rate", "country_risk"}
    assert kinds.count("low_confidence") >= 2
    assert {"edge_unknown_supplier", "edge_unknown_quarter", "edge_repeat"} <= set(kinds)
    assert len({case["case_id"] for case in cases}) == len(cases)
    repeat = next(case for case in cases if case["kind"] == "edge_repeat")
    earlier = [
        case["supplier_key"] for case in cases[: cases.index(repeat)] if case["kind"] == "flag"
    ]
    assert repeat["supplier_key"] in earlier
