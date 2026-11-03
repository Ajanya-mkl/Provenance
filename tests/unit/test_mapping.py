"""Phase 10b: column mapping. The LLM is mocked; every test uses a temporary database."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import duckdb
import pytest
from fastapi.testclient import TestClient

from provenance.agent.llm import LLMError, LLMResponse, ToolCall
from provenance.agent.store import verify_audit_log
from provenance.api.main import create_app
from provenance.datasets import contract, mapping_store, registry
from provenance.datasets import map as map_cli
from provenance.datasets.eval.run import load_cases, score
from provenance.datasets.heuristic_mapper import heuristic_mapping
from provenance.datasets.mapping_agent import escalation_reasons, propose_mapping
from provenance.datasets.onboard import PROJECT_ROOT, Paths, onboard
from provenance.datasets.validator import MappingInvalid, load_context, validate

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "datasets"
CLEAN = FIXTURES / "shipments_clean.csv"
MARKS = FIXTURES / "student_marks.csv"

GOOD = {
    "entity": "supplier_code",
    "event_date": {"col": "order_date", "format": "%Y-%m-%d"},
    "outcome": {"kind": "delay_value", "col": "delay_days", "unit": "days"},
    "country": "destination_country",
    "evidence": {"entity": ["distinct_count=5"]},
    "confidence": 0.9,
    "notes": "",
}


@pytest.fixture(scope="module")
def template(tmp_path_factory) -> dict[str, object]:
    """Onboard the clean and student-marks fixtures once; tests get a copy."""
    root = tmp_path_factory.mktemp("mapping_template")
    paths = Paths(root / "warehouse.duckdb", root / "datasets", root / "quarantine")
    clean = onboard(CLEAN, "Clean shipments", paths=paths)
    marks = onboard(MARKS, "Student marks", paths=paths)
    assert clean.status == marks.status == "gated"
    return {"db": paths.db_path, "clean": clean.dataset_id, "marks": marks.dataset_id}


@pytest.fixture
def db(template, tmp_path) -> Path:
    copy = tmp_path / "warehouse.duckdb"
    shutil.copy(template["db"], copy)
    return copy


@pytest.fixture
def clean_id(template) -> str:
    return template["clean"]


@pytest.fixture
def context(db, clean_id):
    return load_context(db, clean_id)


def _with(**changes):
    mapping = json.loads(json.dumps(GOOD))
    for path, value in changes.items():
        root, _, leaf = path.partition("__")
        if leaf:
            mapping[root][leaf] = value
        else:
            mapping[root] = value
    return mapping


class ScriptedLLM:
    """Returns canned responses in order and records every message it was sent."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.sent: list[list[dict]] = []

    def chat(self, messages, tools=None, tool_choice="auto"):
        self.sent.append(json.loads(json.dumps(messages)))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def _tools(*names):
    calls = [ToolCall(id=f"c{i}", name=name, arguments="{}") for i, name in enumerate(names)]
    return LLMResponse(None, calls, 100, 10, "tool_calls")


def _answer(mapping):
    return LLMResponse(json.dumps(mapping), [], 200, 50, "stop")


# --- validator --------------------------------------------------------------------


def test_good_mapping_is_valid_and_mappable_but_too_small_to_score(context):
    result = validate(GOOD, context)

    assert result.ok, result.errors
    assert result.mappable.ok
    assert not result.scoreable.ok and "quarter" in result.scoreable.reason


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"outcome__col": "supplier_code", "entity": "shipment_id"}, "not numbers"),
        ({"entity": "unit_price"}, "holds float values"),
        ({"event_date__format": "%d/%m/%Y"}, "parses only"),
        ({"country": "supplier_code"}, "used twice"),
        ({"entity": "no_such_column"}, "does not exist"),
        (
            {"outcome": {"kind": "late_flag", "col": "on_time", "late_when": "true"}},
            "olarity",
        ),
        ({"evidence": {"entity": ["distinct_count=999"]}}, "999"),
    ],
    ids=["wrong-type", "float-entity", "date-format", "reused", "missing", "polarity", "evidence"],
)
def test_validator_rejects(context, changes, message):
    result = validate(_with(**changes), context)

    assert not result.ok and not result.mappable.ok
    assert any(message in error for error in result.errors), result.errors


def test_inverted_polarity_is_accepted(context):
    for late_when in ("false", False, "FALSE"):
        mapping = _with(outcome={"kind": "late_flag", "col": "on_time", "late_when": late_when})
        result = validate(mapping, context)
        assert result.ok, result.errors
        assert result.mapping["outcome"]["late_when"] == "false"


@pytest.fixture
def flags_context(tmp_path):
    """Late flags spelled Y/N, 0/1 and yes/no."""
    rows = ["supplier,order_date,late_yn,late_01,on_time_answer"]
    for i in range(60):
        late = i % 3 == 0
        rows.append(
            f"S{i % 4},2023-01-{1 + i % 28:02d},{'Y' if late else 'N'},{int(late)},"
            f"{'no' if late else 'yes'}"
        )
    csv_path = tmp_path / "flags.csv"
    csv_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    paths = Paths(tmp_path / "w.duckdb", tmp_path / "datasets", tmp_path / "quarantine")
    result = onboard(csv_path, "flags", paths=paths)
    assert result.status == "gated", result.problems
    return load_context(paths.db_path, result.dataset_id)


@pytest.mark.parametrize(
    ("col", "given", "expected"),
    [
        ("late_yn", "Y", "true"),
        ("on_time_answer", "no", "false"),
        ("on_time_answer", "NO", "false"),
        ("late_01", "1", "true"),
        ("late_01", "true", "true"),
    ],
)
def test_late_when_given_as_a_column_value_is_normalised(flags_context, col, given, expected):
    mapping = {
        "entity": "supplier",
        "event_date": {"col": "order_date", "format": "%Y-%m-%d"},
        "outcome": {"kind": "late_flag", "col": col, "late_when": given},
    }
    result = validate(mapping, flags_context)

    assert result.ok, result.errors
    assert result.mapping["outcome"]["late_when"] == expected


@pytest.mark.parametrize(
    ("col", "given"), [("late_yn", "yes"), ("late_01", "N"), ("on_time_answer", "0")]
)
def test_late_when_that_is_not_a_value_of_the_column_is_rejected(flags_context, col, given):
    mapping = {
        "entity": "supplier",
        "event_date": {"col": "order_date", "format": "%Y-%m-%d"},
        "outcome": {"kind": "late_flag", "col": col, "late_when": given},
    }
    result = validate(mapping, flags_context)

    assert not result.ok
    assert any("is not a value of" in error for error in result.errors), result.errors


def test_null_required_field_is_valid_but_not_mappable(context):
    result = validate(_with(outcome=None, notes="no lateness column"), context)

    assert result.ok and result.unmapped == ["outcome"]
    assert not result.mappable.ok and "no lateness column" in result.mappable.reason


def test_scoreable_rule_with_synthetic_counts():
    quarters = ["2023Q1", "2023Q2", "2023Q3"]
    ok = {(e, q): 60 for e in ("A", "B", "C") for q in quarters}
    assert contract.scoreable_verdict(ok).ok

    two_quarters = {(e, q): 60 for e in ("A", "B") for q in quarters[:2]}
    verdict = contract.scoreable_verdict(two_quarters)
    assert not verdict.ok and "2 quarter(s)" in verdict.reason

    thin = {**ok, **{(e, q): 5 for e in ("D", "E", "F") for q in quarters}}
    verdict = contract.scoreable_verdict(thin)
    assert not verdict.ok and "3 of 6 entities" in verdict.reason

    assert not contract.scoreable_verdict({}).ok


# --- heuristic --------------------------------------------------------------------


HARD_CASES = ("h_abbreviated", "i_mixed_languages", "j_misleading_date", "k_hours")


@pytest.mark.parametrize("case", load_cases(), ids=lambda case: case["case_id"])
def test_heuristic_on_the_fixtures(case, tmp_path):
    """Original cases: every field right. Hard cases (written to beat the heuristic): a
    required field it gets wrong must make heuristic-first ask the agent."""
    paths = Paths(tmp_path / "w.duckdb", tmp_path / "datasets", tmp_path / "quarantine")
    result = onboard(PROJECT_ROOT / case["fixture"], case["case_id"], paths=paths)
    context = load_context(paths.db_path, result.dataset_id)
    mapping = heuristic_mapping(context)
    validation = validate(mapping, context)

    assert validation.ok, validation.errors
    marks = score(mapping, validation.mappable.ok, case["expected"])
    if case["case_id"] not in HARD_CASES:
        assert all(marks.values()), marks
        return
    wrong_required = [f for f in ("entity", "event_date", "outcome") if not marks.get(f, True)]
    if wrong_required:
        assert escalation_reasons(context), (case["case_id"], wrong_required)


# --- agent ------------------------------------------------------------------------


def test_agent_answer_that_validates_is_kept_with_tokens(context):
    llm = ScriptedLLM([_tools("get_contract", "get_dataset_profile"), _answer(GOOD)])
    proposal = propose_mapping(context, llm_factory=lambda: llm, mode="agent")

    assert proposal.source == "agent" and not proposal.attempts
    assert (proposal.prompt_tokens, proposal.completion_tokens) == (300, 60)
    assert proposal.validation.mappable.ok


def test_agent_gets_one_retry_with_the_validator_message(context):
    bad = _with(entity="unit_price")
    llm = ScriptedLLM([_tools("get_dataset_profile"), _answer(bad), _answer(GOOD)])
    proposal = propose_mapping(context, llm_factory=lambda: llm, mode="agent")

    assert proposal.source == "agent" and len(proposal.attempts) == 1
    retry_prompt = llm.sent[-1][-1]["content"]
    assert "validator rejected" in retry_prompt and "unit_price" in retry_prompt


def test_two_failures_fall_back_to_the_heuristic(context):
    bad = _with(country="supplier_code")
    llm = ScriptedLLM([_answer(bad), _answer(bad)])
    proposal = propose_mapping(context, llm_factory=lambda: llm, mode="agent")

    assert proposal.source == "heuristic"
    assert len(proposal.attempts) == 2 and "twice" in proposal.fallback_reason
    assert proposal.prompt_tokens == 400  # the failed attempts' tokens are still recorded
    assert proposal.validation.mappable.ok


def test_unreachable_llm_falls_back(context):
    llm = ScriptedLLM([LLMError("rate limited after retries")])
    proposal = propose_mapping(context, llm_factory=lambda: llm, mode="agent")

    assert proposal.source == "heuristic" and "LLMError" in proposal.fallback_reason


def test_no_text_values_are_sent_to_the_model_by_default(context):
    text_values = set()
    for name, column in context.columns.items():
        if column["inferred_type"] == "string":
            text_values |= set(context.frame[name].dropna().astype(str))
    assert {"SUP-02", "Mexico", "S00001"} <= text_values

    def sent_text(sample_values):
        llm = ScriptedLLM([_tools("get_dataset_profile", "get_column_profile"), _answer(GOOD)])
        llm.replies[0].tool_calls[1] = ToolCall(
            "c9", "get_column_profile", '{"column": "supplier_code"}'
        )
        propose_mapping(context, llm_factory=lambda: llm, mode="agent", sample_values=sample_values)
        return json.dumps(llm.sent)

    default = sent_text(False)
    assert not [value for value in text_values if value in default]
    assert "min_length" in default
    assert "SUP-02" in sent_text(True)  # only when explicitly turned on


# --- approval ---------------------------------------------------------------------


def _status(db, dataset_id):
    return registry.get_dataset(db, dataset_id)["status"]


def _audit(db):
    with duckdb.connect(str(db), read_only=True) as connection:
        events = [
            r[0]
            for r in connection.execute("SELECT event FROM agent.audit_log ORDER BY seq").fetchall()
        ]
        return events, verify_audit_log(connection)


def test_only_an_approved_mapping_moves_the_dataset_to_mapped(db, clean_id, context):
    mapping_id = mapping_store.record_proposal(
        db, clean_id, propose_mapping(context, heuristic_only=True), created_by="heuristic"
    )
    assert _status(db, clean_id) == "gated"

    with pytest.raises(mapping_store.DecisionError):
        mapping_store.approve(db, mapping_id, by="  ")
    assert _status(db, clean_id) == "gated"

    mapping_store.approve(db, mapping_id, by="Ajanya", reason="checked")
    record = mapping_store.get_mapping(db, mapping_id)
    assert record["status"] == "approved" and record["decided_by"] == "Ajanya"
    assert _status(db, clean_id) == "mapped"

    with pytest.raises(mapping_store.DecisionError, match="not proposed"):
        mapping_store.approve(db, mapping_id, by="Ajanya")


def test_unmappable_mapping_cannot_be_approved(db, template):
    marks_id = template["marks"]
    proposal = propose_mapping(load_context(db, marks_id), heuristic_only=True)
    mapping_id = mapping_store.record_proposal(db, marks_id, proposal, created_by="heuristic")

    assert not mapping_store.get_mapping(db, mapping_id)["mappable"]
    with pytest.raises(mapping_store.DecisionError, match="not mappable"):
        mapping_store.approve(db, mapping_id, by="Ajanya")
    assert _status(db, marks_id) == "gated"


def test_edit_creates_a_new_version_and_supersedes(db, clean_id, context):
    first = mapping_store.record_proposal(
        db, clean_id, propose_mapping(context, heuristic_only=True), created_by="heuristic"
    )
    new_id, validation = mapping_store.edit(
        db,
        first,
        mapping_store.parse_sets(
            ["outcome.kind=late_flag", "outcome.col=on_time", "outcome.late_when=false"]
        ),
        by="Ajanya",
    )

    assert validation.ok
    old, new = mapping_store.get_mapping(db, first), mapping_store.get_mapping(db, new_id)
    assert old["status"] == "superseded" and old["superseded_by"] == new_id
    assert old["proposal"]["outcome"]["kind"] == "delay_value"  # never overwritten
    assert (new["version"], new["source"], new["status"]) == (2, "human", "proposed")
    assert new["proposal"]["outcome"] == {
        "kind": "late_flag",
        "col": "on_time",
        "late_when": "false",
    }

    mapping_store.approve(db, new_id, by="Ajanya")
    events, broken = _audit(db)
    assert events[-3:] == ["mapping_proposed", "mapping_edited", "mapping_approved"]
    assert broken == []


def test_invalid_edit_is_refused_and_writes_nothing(db, clean_id, context):
    first = mapping_store.record_proposal(
        db, clean_id, propose_mapping(context, heuristic_only=True), created_by="heuristic"
    )
    with pytest.raises(MappingInvalid, match="used twice"):
        mapping_store.edit(db, first, {"country": "supplier_code"}, by="Ajanya")
    with pytest.raises(MappingInvalid, match="cannot be edited"):
        mapping_store.edit(db, first, {"confidence": "1"}, by="Ajanya")

    assert [m["status"] for m in mapping_store.list_mappings(db, clean_id)] == ["proposed"]


def test_reject_needs_a_reason_and_a_name(db, clean_id, context):
    first = mapping_store.record_proposal(
        db, clean_id, propose_mapping(context, heuristic_only=True), created_by="heuristic"
    )
    with pytest.raises(mapping_store.DecisionError, match="reason"):
        mapping_store.reject(db, first, by="Ajanya", reason=" ")
    mapping_store.reject(db, first, by="Ajanya", reason="wrong entity")

    assert mapping_store.get_mapping(db, first)["status"] == "rejected"
    assert _status(db, clean_id) == "gated"
    events, broken = _audit(db)
    assert events[-1] == "mapping_rejected" and broken == []


def test_cli_propose_edit_approve(db, clean_id, capsys):
    assert map_cli.main([clean_id, "--heuristic-only", "--db-path", str(db)]) == 0
    printed = capsys.readouterr().out
    assert "mappable:  YES" in printed and "scoreable: NO" in printed
    mapping_id = mapping_store.list_mappings(db, clean_id)[-1]["mapping_id"]

    assert (
        map_cli.main(
            ["--edit", mapping_id, "--set", "country=null", "--by", "Ajanya", "--db-path", str(db)]
        )
        == 0
    )
    edited = mapping_store.list_mappings(db, clean_id)[-1]
    assert edited["proposal"]["country"] is None

    assert map_cli.main(["--approve", edited["mapping_id"], "--db-path", str(db)]) == 2
    assert (
        map_cli.main(["--approve", edited["mapping_id"], "--by", "Ajanya", "--db-path", str(db)])
        == 0
    )
    assert _status(db, clean_id) == "mapped"


# --- API --------------------------------------------------------------------------


@pytest.fixture
def client(db, tmp_path):
    def no_llm():
        raise LLMError("no model in tests")

    return TestClient(
        create_app(db, results_dir=tmp_path / "no-results", mapping_llm_factory=no_llm)
    )


def test_api_dataset_reads(client, clean_id):
    items = client.get("/api/datasets").json()["items"]
    assert clean_id in [item["dataset_id"] for item in items]

    detail = client.get(f"/api/datasets/{clean_id}").json()
    assert detail["status"] == "gated" and len(detail["columns"]) == 9
    assert client.get(f"/api/datasets/{clean_id}/mapping").json() == {
        "current": None,
        "history": [],
    }
    assert client.get("/api/datasets/000000000000").status_code == 404


def test_api_propose_edit_approve(client, db, clean_id):
    base = f"/api/datasets/{clean_id}/mapping"
    assert client.post(f"{base}/propose", json={"by": ""}).status_code == 400

    proposed = client.post(f"{base}/propose", json={"by": "Ajanya"}).json()
    assert proposed["source"] == "heuristic"  # the agent was unreachable
    mapping_id = proposed["mapping_id"]

    bad = client.post(
        f"{base}/{mapping_id}/edit", json={"by": "Ajanya", "set": {"entity": "unit_price"}}
    )
    assert bad.status_code == 400 and "float" in bad.json()["detail"]
    assert client.post(f"{base}/{mapping_id}/approve", json={"by": " "}).status_code == 400

    edited = client.post(
        f"{base}/{mapping_id}/edit", json={"by": "Ajanya", "set": {"country": None}}
    )
    assert edited.status_code == 200 and edited.json()["source"] == "human"
    new_id = edited.json()["mapping_id"]

    assert client.post(f"{base}/{mapping_id}/approve", json={"by": "Ajanya"}).status_code == 409
    assert client.post(f"{base}/{new_id}/approve", json={"by": "Ajanya"}).status_code == 200
    current = client.get(base).json()
    assert current["current"]["mapping_id"] == new_id and len(current["history"]) == 2
    assert client.post(f"{base}/nope/approve", json={"by": "Ajanya"}).status_code == 404
    assert _status(db, clean_id) == "mapped"


def test_api_lock_returns_409(client, clean_id, monkeypatch):
    def locked(*_args, **_kwargs):
        raise duckdb.IOException("Could not set lock on file")

    monkeypatch.setattr(mapping_store, "record_proposal", locked)
    response = client.post(
        f"/api/datasets/{clean_id}/mapping/propose", json={"by": "Ajanya", "heuristic_only": True}
    )
    assert response.status_code == 409


# --- Phase 10c: heuristic-first, human confidence, cancelled ----------------------


def _never_called():
    raise AssertionError("the agent must not be asked when the heuristic is confident")


def test_heuristic_first_keeps_a_confident_heuristic_without_calling_the_agent(context):
    proposal = propose_mapping(context, llm_factory=_never_called)

    assert proposal.decided_by == "heuristic" and proposal.source == "heuristic"
    assert proposal.escalation_reasons == [] and proposal.prompt_tokens == 0


def test_heuristic_first_asks_the_agent_when_a_required_field_is_unmapped(db, template):
    context = load_context(db, template["marks"])
    marks_answer = {
        "entity": None,
        "event_date": {"col": "exam_date", "format": "%d/%m/%Y"},
        "outcome": None,
        "notes": "student marks, not shipments",
    }
    llm = ScriptedLLM([_answer(marks_answer)])
    proposal = propose_mapping(context, llm_factory=lambda: llm)

    assert proposal.decided_by == "agent"
    assert "heuristic left entity unmapped" in proposal.escalation_reasons
    assert not proposal.validation.mappable.ok


def test_heuristic_first_asks_the_agent_below_the_confidence_threshold(context):
    llm = ScriptedLLM([_answer(GOOD)])
    proposal = propose_mapping(context, llm_factory=lambda: llm, threshold=0.99)

    assert proposal.decided_by == "agent"
    assert any("below 0.99" in reason for reason in proposal.escalation_reasons)


def test_heuristic_first_records_a_fallback_as_its_own_path(context):
    bad = _with(country="supplier_code")
    llm = ScriptedLLM([_answer(bad), _answer(bad)])
    proposal = propose_mapping(context, llm_factory=lambda: llm, threshold=0.99)

    assert proposal.decided_by == "heuristic_fallback" and proposal.source == "heuristic"


def test_the_decision_path_is_stored_with_the_proposal(db, clean_id, context):
    proposal = propose_mapping(context, llm_factory=_never_called)
    mapping_id = mapping_store.record_proposal(db, clean_id, proposal, created_by="heuristic")

    record = mapping_store.get_mapping(db, mapping_id)
    assert record["validation"]["decided_by"] == "heuristic"
    assert "decided by path: heuristic" in map_cli.render_mapping(record)


def test_a_human_edit_has_no_confidence_and_says_so(db, clean_id, context):
    first = mapping_store.record_proposal(
        db, clean_id, propose_mapping(context, heuristic_only=True), created_by="heuristic"
    )
    new_id, _ = mapping_store.edit(db, first, {"country": None}, by="Ajanya")
    record = mapping_store.get_mapping(db, new_id)

    assert record["proposal"]["confidence"] is None
    assert record["validation"]["decided_by"] == "human"
    rendered = map_cli.render_mapping(record)
    assert "human-edited (no model confidence)" in rendered and "confidence None" not in rendered


def test_cancelled_must_name_a_value_the_column_contains(context):
    ok = validate(_with(cancelled={"col": "on_time", "value": "true"}), context)
    missing = validate(_with(cancelled={"col": "on_time", "value": "Shipping canceled"}), context)
    malformed = validate(_with(cancelled={"col": "on_time"}), context)

    assert ok.ok, ok.errors
    assert any("would mark nothing" in error for error in missing.errors)
    assert any("cancelled must be null" in error for error in malformed.errors)
    assert ("cancelled", "on_time") in contract.used_columns(ok.mapping)
