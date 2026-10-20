from datetime import date

import duckdb
import pytest

from provenance.agent.tools.investigation_tools import (
    TOOL_SCHEMAS,
    InvestigationTools,
    parse_quarter,
)
from tests.unit.agent_fixtures import EVENT_ID


@pytest.fixture
def tools(warehouse):
    with InvestigationTools(warehouse) as toolbox:
        yield toolbox


def test_tools_open_the_warehouse_read_only(tools):
    with pytest.raises(duckdb.Error):
        tools._connection.execute("CREATE TABLE agent.scratch (x INTEGER)")


def test_get_flag_returns_rounded_flag(tools):
    flag = tools.call("get_flag", {"event_id": EVENT_ID})

    assert flag["supplier_key"] == "cat_widgets"
    assert flag["quarter"] == "2017-Q1"
    assert flag["score"] == 0.735
    assert flag["top_factor"]["name"] == "delay_stddev"
    assert flag["top_factor"]["raw_value"] == 1.615


def test_get_supplier_history_lists_versions_newest_first(tools):
    history = tools.call("get_supplier_history", {"supplier_key": "cat_widgets"})

    assert history["supplier_name"] == "Widgets"
    assert [version["version"] for version in history["versions"]] == [2, 1]
    assert history["versions"][0]["is_current"] is True
    assert "row_hash" not in history["versions"][0]


def test_get_score_breakdown_parses_factors(tools):
    breakdown = tools.call(
        "get_score_breakdown", {"supplier_key": "cat_widgets", "quarter": "2017-Q1"}
    )

    assert breakdown["score"] == 0.735
    assert [factor["factor"] for factor in breakdown["factors"]] == [
        "delay_stddev",
        "late_rate",
    ]
    assert breakdown["factors"][0]["weighted_contribution"] == 0.304
    assert breakdown["previous_scored_quarter"] == {
        "quarter": "2016-Q4",
        "score": 0.39,
        "tier": "medium",
    }
    assert breakdown["quarter_metrics"]["shipment_count"] == 4


def test_get_country_risk_weights_destinations_by_shipments(tools):
    risk = tools.call("get_country_risk", {"supplier_key": "cat_widgets", "quarter": "2017-Q1"})

    # MEX uses its 2016 LPI (3.0 -> 0.5); XXX has none and takes the median of
    # MEX 3.0 and DEU 4.0 (3.5 -> 0.375). 3 of 4 shipments go to MEX.
    assert risk["quarter_country_risk"] == round((3 * 0.5 + 0.375) / 4, 3)
    assert risk["trailing_country_risk"] == 0.423
    assert risk["destination_count"] == 2
    assert risk["top_destinations"][0] == {
        "country_iso3": "MEX",
        "shipment_share": 0.75,
        "lpi_score": 3.0,
        "country_risk": 0.5,
    }
    assert risk["top_destinations"][1]["lpi_score"] is None


def test_get_market_mix_compares_with_previous_quarter(tools):
    mix = tools.call("get_market_mix", {"supplier_key": "cat_widgets", "quarter": "2017-01-01"})

    assert mix["previous_quarter"] == "2016-Q4"
    assert mix["markets"] == [
        {"market": "LATAM", "share": 0.75, "previous_share": 0.0, "share_change": 0.75},
        {"market": "Europe", "share": 0.25, "previous_share": 1.0, "share_change": -0.75},
    ]


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("get_supplier_history", {"supplier_key": "cat_unknown"}),
        ("get_score_breakdown", {"supplier_key": "cat_unknown", "quarter": "2017-Q1"}),
        ("get_country_risk", {"supplier_key": "cat_unknown", "quarter": "2017-Q1"}),
        ("get_market_mix", {"supplier_key": "cat_unknown", "quarter": "2017-Q1"}),
    ],
)
def test_unknown_supplier_returns_error_object(tools, name, arguments):
    result = tools.call(name, arguments)

    assert result == {"error": "unknown supplier 'cat_unknown'"}


@pytest.mark.parametrize(
    ("name", "arguments", "message"),
    [
        ("get_flag", {"event_id": "risk_flag:missing"}, "unknown event"),
        ("get_flag", {"event_id": "dq_failure:1"}, "not a risk_flag"),
        ("get_flag", {"event_id": "x'; DROP TABLE agent.events; --"}, "invalid format"),
        ("get_score_breakdown", {"supplier_key": "cat_widgets", "quarter": "2030-Q1"}, "no score"),
        ("get_country_risk", {"supplier_key": "cat_widgets", "quarter": "Q1"}, "quarter must"),
        ("get_market_mix", {"supplier_key": "Robert'); --", "quarter": "2017-Q1"}, "lowercase"),
        ("get_flag", {"event_id": EVENT_ID, "sql": "SELECT 1"}, "exactly these arguments"),
        ("run_sql", {"query": "SELECT 1"}, "unknown tool"),
    ],
)
def test_bad_arguments_return_error_objects(tools, name, arguments, message):
    result = tools.call(name, arguments)

    assert set(result) == {"error"}
    assert message in result["error"]


def test_tool_schemas_never_accept_sql():
    names = {schema["function"]["name"] for schema in TOOL_SCHEMAS}
    assert names == {
        "get_flag",
        "get_supplier_history",
        "get_score_breakdown",
        "get_country_risk",
        "get_market_mix",
        "recall_past_investigations",
    }
    for schema in TOOL_SCHEMAS:
        properties = schema["function"]["parameters"]["properties"]
        assert set(properties) <= {"event_id", "supplier_key", "quarter", "limit"}


def test_parse_quarter_formats():
    assert parse_quarter("2017-Q1") == date(2017, 1, 1)
    assert parse_quarter("2017q3") == date(2017, 7, 1)
    assert parse_quarter("2017-10-01") == date(2017, 10, 1)
    assert parse_quarter("2017-02-01") is None
    assert parse_quarter(2017) is None
