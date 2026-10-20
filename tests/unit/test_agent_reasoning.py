import json

import groq
import httpx
import pytest

from provenance.agent.grounding import check_grounding, extract_numbers
from provenance.agent.llm import GroqLLM, LLMError, LLMResponse, ToolCall
from provenance.agent.loop import investigate
from provenance.agent.run import record_investigation, run_investigation, status_for

FLAG_RESULT = {
    "supplier_key": "cat_widgets",
    "quarter": "2017-Q1",
    "new_tier": "high",
    "score": 0.735,
    "top_factor": {"name": "delay_stddev", "raw_value": 1.615, "weight": 0.35},
}
TOOL_CALLS = [{"name": "get_flag", "result": FLAG_RESULT}]


def _report(**overrides):
    report = {
        "summary": "cat_widgets moved to high in 2017-Q1 with score 0.735.",
        "top_factor": "delay_stddev",
        "evidence": [{"tool_name": "get_flag", "value": "raw_value=1.615"}],
        "proposed_action": "request_review",
        "confidence": "high",
    }
    report.update(overrides)
    return report


# -- grounding ----------------------------------------------------------------


def test_grounding_accepts_numbers_from_tool_results():
    result = check_grounding(
        _report(summary="Score 0.735 (0.73 rounded), weight 35% in 2017-Q1."), TOOL_CALLS
    )

    assert result.grounded
    assert result.grounded_ratio == 1.0
    assert result.ungrounded_numbers == []


def test_grounding_catches_an_invented_number():
    result = check_grounding(
        _report(summary="Score 0.735, up from 0.512 last quarter in 2017-Q1."), TOOL_CALLS
    )

    assert not result.grounded
    assert result.ungrounded_numbers == ["0.512"]
    assert 0 < result.grounded_ratio < 1


def test_grounding_rejects_extra_precision_not_in_results():
    # 0.7351 is closer than any rounding of 0.735 allows, so it was not copied.
    result = check_grounding(_report(summary="Score 0.7351."), TOOL_CALLS)

    assert result.ungrounded_numbers == ["0.7351"]


def test_grounding_rejects_numbers_from_failed_tools_and_uncalled_tools():
    calls = TOOL_CALLS + [{"name": "get_market_mix", "result": {"error": "no score for 2099"}}]
    report = _report(
        summary="Score 0.735 in 2099.",
        evidence=[{"tool_name": "get_market_mix", "value": "share=0.5"}],
    )

    result = check_grounding(report, calls)

    assert result.ungrounded_numbers == ["2099", "0.5"]
    assert result.unknown_evidence_tools == ["get_market_mix"]


def test_number_extraction_skips_labels_and_splits_dates():
    values = [number.value for number in extract_numbers("2017-Q1 v2 on 2017-01-05: -0.25, 12%")]

    assert values == [2017, 2017, 1, 5, -0.25, 12]


# -- loop ---------------------------------------------------------------------


class ScriptedLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def chat(self, messages, tools=None, tool_choice="auto"):
        self.calls.append({"messages": list(messages), "tool_choice": tool_choice})
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]


class FakeToolbox:
    def __init__(self):
        self.calls = []

    def call(self, name, arguments):
        self.calls.append((name, arguments))
        return FLAG_RESULT if name == "get_flag" else {"error": "unused"}


def _tool_response(name="get_flag", arguments='{"event_id": "e1"}', call_id="c1"):
    return LLMResponse(None, [ToolCall(call_id, name, arguments)], 100, 10, "tool_calls")


def _final_response(content):
    return LLMResponse(content, [], 200, 50, "stop")


def test_loop_calls_tools_then_returns_validated_report():
    llm = ScriptedLLM(
        [
            _tool_response(
                "get_score_breakdown", '{"supplier_key": "cat_widgets", ' '"quarter": "2017-Q1"}'
            ),
            _final_response(json.dumps(_report())),
        ]
    )
    toolbox = FakeToolbox()

    result = investigate("e1", llm, toolbox)

    assert result.stop_reason == "final_answer"
    assert result.steps == 2
    assert result.report["proposed_action"] == "request_review"
    assert toolbox.calls[-1] == (
        "get_score_breakdown",
        {"supplier_key": "cat_widgets", "quarter": "2017-Q1"},
    )
    assert result.tool_calls[0]["result"] == FLAG_RESULT
    assert (result.prompt_tokens, result.completion_tokens) == (300, 60)
    tool_message = llm.calls[1]["messages"][-1]
    assert tool_message["role"] == "tool" and tool_message["tool_call_id"] == "c1"


def test_loop_stops_at_max_steps():
    llm = ScriptedLLM([_tool_response()])
    toolbox = FakeToolbox()

    result = investigate("e1", llm, toolbox, max_steps=6)

    assert result.stop_reason == "max_steps"
    assert result.report is None
    assert result.steps == 6
    assert len(llm.calls) == 6
    assert [call["tool_choice"] for call in llm.calls] == ["auto"] * 5 + ["none"]


def test_loop_asks_again_after_an_invalid_report():
    invalid = _report(confidence="certain")
    llm = ScriptedLLM(
        [
            _final_response(json.dumps(invalid)),
            _final_response(f"```json\n{json.dumps(_report())}\n```"),
        ]
    )

    result = investigate("e1", llm, FakeToolbox())

    assert result.report is not None
    assert result.steps == 2
    assert "confidence" in llm.calls[1]["messages"][-1]["content"]


def test_loop_leaves_the_action_allow_list_to_the_guardrail():
    # A disallowed action is not re-asked here; approval.propose_action audits and
    # replaces it with no_action.
    llm = ScriptedLLM([_final_response(json.dumps(_report(proposed_action="suspend")))])

    result = investigate("e1", llm, FakeToolbox())

    assert result.report["proposed_action"] == "suspend"


def test_loop_always_recalls_memory_before_the_first_model_call():
    llm = ScriptedLLM([_final_response(json.dumps(_report()))])
    toolbox = FakeToolbox()

    result = investigate("e1", llm, toolbox)

    assert toolbox.calls == [
        ("get_flag", {"event_id": "e1"}),
        ("recall_past_investigations", {"supplier_key": "cat_widgets", "limit": 3}),
    ]
    assert [call["step"] for call in result.tool_calls] == [0, 0]
    first_messages = llm.calls[0]["messages"]
    assert [message["role"] for message in first_messages] == [
        "system",
        "user",
        "assistant",
        "tool",
        "tool",
    ]
    assert result.steps == 1


def test_loop_skips_recall_when_the_flag_is_unknown():
    class MissingFlag(FakeToolbox):
        def call(self, name, arguments):
            self.calls.append((name, arguments))
            return {"error": "unknown event"}

    toolbox = MissingFlag()
    investigate("e1", ScriptedLLM([_final_response(json.dumps(_report()))]), toolbox)

    assert toolbox.calls == [("get_flag", {"event_id": "e1"})]


def test_loop_returns_tool_argument_errors_to_the_model():
    llm = ScriptedLLM(
        [_tool_response(arguments="{not json"), _final_response(json.dumps(_report()))]
    )
    toolbox = FakeToolbox()

    result = investigate("e1", llm, toolbox)

    assert [name for name, _ in toolbox.calls] == ["get_flag", "recall_past_investigations"]
    assert result.tool_calls[-1]["result"] == {"error": "tool arguments were not valid JSON"}


# -- llm retries --------------------------------------------------------------


def _completion():
    message = type("Message", (), {"content": "done", "tool_calls": None})()
    choice = type("Choice", (), {"message": message, "finish_reason": "stop"})()
    usage = type("Usage", (), {"prompt_tokens": 12, "completion_tokens": 3})()
    return type("Completion", (), {"choices": [choice], "usage": usage})()


def _rate_limit(headers=None):
    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    response = httpx.Response(429, request=request, headers=headers or {})
    return groq.RateLimitError("rate limited", response=response, body=None)


def _tool_use_failed():
    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    response = httpx.Response(400, request=request)
    body = {"error": {"code": "tool_use_failed", "message": "Failed to call a function"}}
    return groq.BadRequestError("tool_use_failed", response=response, body=body)


class FakeCompletions:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.requests = []

    def create(self, **request):
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _llm(outcomes):
    completions = FakeCompletions(outcomes)
    client = type("Client", (), {"chat": type("Chat", (), {"completions": completions})()})()
    sleeps = []
    return GroqLLM(model="test-model", client=client, sleep=sleeps.append), completions, sleeps


def test_llm_retries_429_with_exponential_backoff():
    llm, completions, sleeps = _llm([_rate_limit(), _rate_limit(), _completion()])

    response = llm.chat([{"role": "user", "content": "hi"}])

    assert response.content == "done"
    assert sleeps == [2.0, 4.0]
    assert len(completions.requests) == 3
    assert completions.requests[0]["temperature"] == 0
    assert completions.requests[0]["max_tokens"] == 1024
    assert (llm.prompt_tokens, llm.completion_tokens) == (12, 3)
    assert llm.usage[0].attempts == 3


def test_llm_honours_retry_after_header():
    llm, _, sleeps = _llm([_rate_limit({"retry-after": "7"}), _completion()])

    llm.chat([{"role": "user", "content": "hi"}])

    assert sleeps == [7.0]


def test_llm_gives_up_after_three_attempts():
    llm, completions, sleeps = _llm([_rate_limit(), _rate_limit(), _rate_limit()])

    with pytest.raises(LLMError, match="after 3 attempts"):
        llm.chat([{"role": "user", "content": "hi"}])
    assert len(completions.requests) == 3
    assert len(sleeps) == 2


def test_llm_retries_malformed_tool_calls_but_not_other_bad_requests():
    llm, _, sleeps = _llm([_tool_use_failed(), _completion()])
    assert llm.chat([{"role": "user", "content": "hi"}]).content == "done"
    assert sleeps == [2.0]

    request = httpx.Request("POST", "https://api.groq.com")
    other = groq.BadRequestError("bad", response=httpx.Response(400, request=request), body={})
    llm, completions, _ = _llm([other])
    with pytest.raises(groq.BadRequestError):
        llm.chat([{"role": "user", "content": "hi"}])
    assert len(completions.requests) == 1


def test_llm_repr_hides_the_client(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "gsk_secret_value")
    monkeypatch.setenv("LLM_MODEL_NAME", "")

    llm = GroqLLM.from_env()

    assert llm.model == "llama-3.3-70b-versatile"
    assert "gsk_secret_value" not in repr(llm)


# -- run ----------------------------------------------------------------------


def test_run_stores_ungrounded_report_as_rejected(tmp_path, monkeypatch):
    import duckdb

    db_path = tmp_path / "warehouse.duckdb"
    duckdb.connect(str(db_path)).close()

    class Tools:
        def __init__(self, path):
            pass

        def __enter__(self):
            return FakeToolbox()

        def __exit__(self, *_):
            pass

    monkeypatch.setattr("provenance.agent.run.InvestigationTools", Tools)
    invented = _report(summary="Score 0.735, was 0.512.")
    llm = ScriptedLLM([_tool_response(), _final_response(json.dumps(invented))])

    result, grounding = run_investigation("e1", db_path, llm)
    record_investigation(db_path, result, grounding, "test-model", True)

    assert status_for(result, grounding) == "rejected_ungrounded"
    with duckdb.connect(str(db_path), read_only=True) as connection:
        row = connection.execute(
            "SELECT event_id, status, grounded, ungrounded_numbers, steps, total_tokens "
            "FROM agent.investigations"
        ).fetchone()
    assert row[:3] == ("e1", "rejected_ungrounded", False)
    assert json.loads(row[3]) == ["0.512"]
    assert row[4:] == (2, 360)
