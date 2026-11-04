"""Regenerate web/src/mock/datasets.json from the real API, on a temporary warehouse.

    .venv/Scripts/python web/scripts/generate_dataset_fixtures.py

Runs the real dataset flow end to end in a temporary folder (never the real
warehouse): uploads four files (a scoreable synthetic file without a country
column, the 10a clean fixture, student marks and the dirty fixture), proposes
mappings heuristic-first (the agent is asked only where the heuristic is unsure,
here for student marks), edits and approves the synthetic mapping, runs scoring
in the background subprocess and records every response the console reads.
The mock API (src/mock/datasetsMock.ts) replays them, so the mock screens show
real shapes and real numbers.
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tests.unit.scoring_fixtures import make_shipments_csv  # noqa: E402

from provenance.api.main import create_app  # noqa: E402
from provenance.datasets.onboard import Paths  # noqa: E402

OUT = ROOT / "web" / "src" / "mock" / "datasets.json"
FIXTURES = ROOT / "tests" / "fixtures" / "datasets"
REVIEWER = "AJANYA"


def _llm_factory():
    from provenance.agent.llm import GroqLLM

    return GroqLLM.from_env()


def main() -> int:
    load_dotenv(ROOT / ".env")
    with tempfile.TemporaryDirectory(prefix="provenance_ds_mock_") as workdir:
        root = Path(workdir)
        paths = Paths(root / "provenance.duckdb", root / "datasets", root / "quarantine")
        client = TestClient(
            create_app(paths.db_path, mapping_llm_factory=_llm_factory, paths=paths)
        )

        def ok(response):
            assert response.status_code == 200, (response.status_code, response.text)
            return response.json()

        def upload(path: Path, name: str, filename: str) -> dict:
            with path.open("rb") as handle:
                return ok(
                    client.post(
                        "/api/datasets/upload",
                        files={"file": (filename, handle, "text/csv")},
                        data={"name": name},
                    )
                )

        synthetic = make_shipments_csv(
            root / "northwind_regional_shipments.csv", country=False, cancelled_share=0.03
        )
        uploads = {
            "northwind": upload(synthetic, "Northwind regional shipments", synthetic.name),
            "clean": upload(
                FIXTURES / "shipments_clean.csv", "Clean shipments", "shipments_clean.csv"
            ),
            "marks": upload(FIXTURES / "student_marks.csv", "Student marks", "student_marks.csv"),
            "dirty": upload(
                FIXTURES / "shipments_dirty.csv", "Dirty shipments", "shipments_dirty.csv"
            ),
        }
        ids = {key: report["dataset_id"] for key, report in uploads.items()}
        gated_details = {
            key: ok(client.get(f"/api/datasets/{ids[key]}"))
            for key in ("northwind", "clean", "marks")
        }

        proposals = {
            key: ok(client.post(f"/api/datasets/{ids[key]}/mapping/propose", json={"by": REVIEWER}))
            for key in ("northwind", "clean", "marks")
        }
        # The synthetic file's cancelled rows are a reviewer's call, not the mapper's.
        northwind = ids["northwind"]
        edited = ok(
            client.post(
                f"/api/datasets/{northwind}/mapping/{proposals['northwind']['mapping_id']}/edit",
                json={
                    "by": REVIEWER,
                    "set": {"cancelled.col": "status", "cancelled.value": "Cancelled"},
                },
            )
        )
        invalid = client.post(
            f"/api/datasets/{northwind}/mapping/{edited['mapping_id']}/edit",
            json={"by": REVIEWER, "set": {"entity": "planned_hours"}},
        ).json()
        ok(
            client.post(
                f"/api/datasets/{northwind}/mapping/{edited['mapping_id']}/approve",
                json={"by": REVIEWER, "reason": "Hours columns and cancelled rule checked."},
            )
        )
        run = ok(client.post(f"/api/datasets/{northwind}/run"))
        deadline = time.monotonic() + 180
        while True:
            state = ok(client.get(f"/api/runs/{run['run_id']}"))
            if state["status"] in ("succeeded", "failed") or time.monotonic() > deadline:
                break
            time.sleep(0.5)
        assert state["status"] == "succeeded", state

        results = ok(client.get(f"/api/datasets/{northwind}/results"))
        routes: dict[str, object] = {
            f"/api/datasets/{northwind}/mapping": ok(
                client.get(f"/api/datasets/{northwind}/mapping")
            ),
            f"/api/flags?dataset={northwind}": ok(client.get(f"/api/flags?dataset={northwind}")),
        }
        for flag in results["flags"]:
            event = quote(flag["event_id"], safe="")
            key, quarter = flag["supplier_key"], flag["quarter"]
            routes[f"/api/flags/{event}"] = ok(client.get(f"/api/flags/{event}"))
            for url in (
                f"/api/suppliers/{key}/history?dataset={northwind}",
                f"/api/suppliers/{key}/score/{quarter}?dataset={northwind}",
            ):
                routes[url] = ok(client.get(url))

        fixtures = {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "ids": ids,
            "uploads": uploads,
            "gated_details": gated_details,
            "scored_detail": ok(client.get(f"/api/datasets/{northwind}")),
            "final_details": {key: ok(client.get(f"/api/datasets/{ids[key]}")) for key in ids},
            "datasets": ok(client.get("/api/datasets"))["items"],
            "proposals": proposals,
            "edited": edited,
            "invalid_edit": invalid,
            "run_final": state,
            "results": results,
            "routes": routes,
        }
    OUT.write_text(json.dumps(fixtures, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}: {len(routes)} routes, flags {len(results['flags'])}")
    print("decided by:", {key: proposal["decided_by"] for key, proposal in proposals.items()})
    return 0


if __name__ == "__main__":
    sys.exit(main())
