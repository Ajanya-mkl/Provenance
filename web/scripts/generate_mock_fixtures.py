"""Regenerate web/src/mock/fixtures.json from the real API, against a COPY of the warehouse.

    .venv/Scripts/python web/scripts/generate_mock_fixtures.py

The copy keeps the real agent tables, replays the cached evaluation runs for
current flags (so there are pending approvals), records one rejection, and
records one deliberately ungrounded report so the NOT GROUNDED state has real
shape. The real database is never written. Fixtures are labelled as mock data
in the console.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote

from fastapi.testclient import TestClient

from provenance.agent.approval import reject
from provenance.agent.eval.eval_runner import RESULTS_DIR, load_cases
from provenance.agent.grounding import check_grounding
from provenance.agent.loop import InvestigationResult
from provenance.agent.run import record_outcome
from provenance.api.main import create_app
from provenance.risk_engine.scorer import DEFAULT_DB_PATH

OUT = Path(__file__).resolve().parents[1] / "src" / "mock" / "fixtures.json"
MODEL = "openai/gpt-oss-120b"
REJECT_CASE = "accessories_2016q4"
UNGROUNDED_CASE = "trade_in_2016q3"


def _replay(db: Path) -> None:
    for case in load_cases():
        if case.get("synthetic_event"):
            continue
        cached = RESULTS_DIR / "cases" / f"{case['case_id']}.json"
        if not cached.exists() or case["event_id"].endswith(":cat_cleats"):
            continue
        payload = json.loads(cached.read_text(encoding="utf-8"))
        result = InvestigationResult(**payload["result"])
        if case["case_id"] == UNGROUNDED_CASE and result.report:
            # Constructed example: an invented prior score the tools never returned.
            result.report["summary"] += " The score was 0.512 a year earlier."
        grounding = check_grounding(result.report, result.tool_calls)
        recorded = record_outcome(db, result, grounding, MODEL, True)
        if case["case_id"] == REJECT_CASE and recorded.action_id:
            reject(db, recorded.action_id, "AJANYA", "Delay variance is within seasonal range.")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="provenance_mock_") as workdir:
        db = Path(workdir) / "mock.duckdb"
        shutil.copy2(DEFAULT_DB_PATH, db)
        _replay(db)
        client = TestClient(create_app(db))

        def get(url: str) -> object:
            response = client.get(url)
            return {"status": response.status_code, "body": response.json()}

        routes: dict[str, object] = {}
        for url in (
            "/api/health",
            "/api/flags",
            "/api/approvals",
            "/api/quality",
            "/api/evaluation",
            "/api/audit?limit=50",
        ):
            routes[url] = get(url)
        routes["/api/health"]["body"]["model"] = MODEL
        for flag in routes["/api/flags"]["body"]["items"]:
            event = quote(flag["event_id"], safe="")
            key, quarter = flag["supplier_key"], flag["quarter"]
            for url in (
                f"/api/flags/{event}",
                f"/api/investigations/{event}",
                f"/api/suppliers/{key}/history",
                f"/api/suppliers/{key}/score/{quarter}",
                f"/api/suppliers/{key}/markets/{quarter}",
            ):
                routes[url] = get(url)
    OUT.write_text(json.dumps(routes, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {len(routes)} routes to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
