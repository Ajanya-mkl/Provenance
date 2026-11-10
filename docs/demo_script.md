# Demo script

A 3-minute and a 10-minute demo, each in mock mode (no setup, no network) or real mode (on a copy of the warehouse). Prefer **mock mode for a talk** and real mode to show it is genuine.

All commands are for Windows cmd, from the repository root, after the [README](../README.md#install-and-check) install. Mock data is labelled in the sidebar, so say so.

## Setup

### Mock mode

```bat
cd web
npm run dev:mock
```

Open http://localhost:5173.

### Real mode, on a copy

The real warehouse is never touched. The copy must keep the file name `provenance.duckdb`, because dbt views name their catalog after it.

```bat
mkdir C:\temp\prov-demo
copy data\provenance.duckdb C:\temp\prov-demo\provenance.duckdb
set PROVENANCE_DUCKDB_PATH=C:\temp\prov-demo\provenance.duckdb
set PROVENANCE_DATASETS_ROOT=C:\temp\prov-demo\datasets
set PROVENANCE_DATASETS_QUARANTINE_ROOT=C:\temp\prov-demo\quarantine
.venv\Scripts\activate
python -c "from pathlib import Path; from tests.unit.scoring_fixtures import make_shipments_csv; make_shipments_csv(Path(r'C:\temp\prov-demo\northwind.csv'), country=False, cancelled_share=0.03)"
python -m provenance.api
```

In a second cmd window:

```bat
cd web
npm run dev
```

Open http://localhost:5173. The first command block needs an existing warehouse (see the [README](../README.md#run-the-pipeline)) and `LLM_API_KEY` in `.env` for the agent. Delete `C:\temp\prov-demo` afterwards, or reuse it: an uploaded file is recognised as already registered.

## 3-minute demo

| Time | Do | Say |
|---|---|---|
| 0:00 | Open **Overview** | "A supply-chain risk score that explains itself, and an agent that cannot act alone." |
| 0:20 | Point at the **Agent card** | "Every number the agent writes is checked against its tools. Nothing it proposes takes effect without a named person." |
| 0:35 | **Flags**, open a flag without an investigation. Mock: `/flags/risk_flag%3A1.2%3A2017-04-01%3Acat_cardio_equipment` | "The rule engine raised this; the score is the source of truth. A supplier here is a product category." |
| 0:50 | Click **Investigate with agent**; wait for **GROUNDED** | "It runs in the background using read-only tools. GROUNDED means every number traces to a tool result." |
| 1:15 | **Review in approvals**, approve with your name | "It only proposes. Approval needs my name, and it goes in a hash-chained audit log." |
| 1:35 | **Datasets & pipelines** → **Upload dataset**, upload a CSV. Mock: any `.csv`. Real: `C:\temp\prov-demo\northwind.csv` | "Now my own data: it is profiled, and quality rules are generated from it." |
| 1:55 | **Review mapping**: point at **Decided by: Heuristic**; add the cancelled rule (`status` = `Cancelled`), **Save edit**, **Approve** | "The heuristic maps it; the agent is asked only when it is unsure. A person approves." |
| 2:20 | **Run scoring** → **Start run** | "Same metrics and scorer as the flagship, in the background." |
| 2:35 | **See results**; point at **Factors and weights** | "This file has no country column, so country risk is left out, with the reason and the renormalised weights. Confidence drops, and it says so." |
| 2:50 | Close | "Running DataCo through this generic path gives the flagship's result: 211 of 211 scores, and the same 20 flags." |

## 10-minute demo

Everything above, more slowly, plus:

| Step | Do | Say |
|---|---|---|
| The problem | Open **Overview** | "Risk scores are often unexplained, and LLMs make things up. Both need bounds." |
| The pipeline | Show the README diagram; in real mode, `python scripts\demo_queries.py` (stop the API first) | "Bronze, a quality gate, silver with SCD2 history, gold, then a rule-based score." |
| Quality gate | **Pipeline** | "A bad batch is moved to quarantine, and the rest keep flowing. These are the gate's verdicts." |
| Audit | **Pipeline** → **Audit chain** | "Each row hashes the previous one, so an edited row breaks the chain." |
| Flag detail | Open a grounded flag, for example `/flags/risk_flag%3A1.2%3A2016-01-01%3Acat_hockey` | "Which factor moved, by how much. A rise flags only if it is large or holds for two quarters; that took us from 43 flags to 20." |
| Receipts | **Full report**; focus a number in the summary | "Each number links to the tool it came from." |
| Grounding failure | **Investigations**: open the trade-in report for 2016-Q3. Mock only; constructed example | "This report cites a number no tool returned, so it was rejected. No action exists." |
| Memory | A report with **Recalled cases** | "It recalls earlier cases and the human decision." |
| Refusals | Add `?mock=busy` and try a run; then `?mock=nokey` | "One writer at a time. It says so plainly and changes nothing." |
| Quarantine | Upload a file named `shipments_dirty.csv` (real: `tests\fixtures\datasets\shipments_dirty.csv`) | "Hard rule failed: here is the rule, why it exists, and where the file went." |
| Profile | **Profile** of a dataset | "Rules are generated from the file. Hard quarantines; tolerance warns." |
| Validator | In **Mapping**, set **Entity** to a price column, **Save edit** | "The validator refuses it with a reason. An LLM's answer goes through the same check." |
| Agent mapping | Mock: `/datasets/7717da472e79/mapping`. Real: upload `tests\fixtures\datasets\student_marks.csv`, then **Propose a mapping** | "Student marks. The heuristic was unsure, so the agent was asked. It sees statistics, never text values. Not mappable, with the reason." |
| Selector | **Showing data from** → the scored dataset; open **Overview**, **Flags** | "Same screens, its own data. Its scores are comparable only within it." |
| Evidence | Show [mapping_evaluation.md](mapping_evaluation.md), [agent_evaluation.md](agent_evaluation.md); `pytest -m integration -s` output | "Heuristic-first matched always asking, with 3 agent calls instead of 11. These sets are small; the limits are written down." |
| Honesty | Show [limitations.md](limitations.md) | "DataCo is likely synthetic, a supplier is a category, and there is no login." |

Have the equivalence output ready rather than running it live: it takes about 5 minutes.

## If Groq or the network is unavailable

| Situation | What to do |
|---|---|
| Before the demo | Run in **mock mode**: it needs no network, API or key, and the investigation completes after a few seconds. |
| Real mode, Groq down | Everything except the agent still works. Show existing reports under **Investigations**. Clicking the button fails with a plain error and records nothing: say "and that is the failure path". |
| Mapping without the model | Heuristic-first needs no model on most files. If the agent is needed and unreachable, the proposal falls back to the heuristic and its note says why. |
| Rate limited (429) | The client retries; a run can take longer. If it fails, it says so. |
| `The warehouse is busy` in real mode | Another process holds the file. Stop Dagster, or wait for the run. |
| A real scoring run fails with "The warehouse was busy" or "failed unexpectedly" | A known bug: the console's own polling can collide with a run starting (4 of 12 runs when measured; see [limitations](limitations.md#running-it)). Nothing was changed. Click **Run again**. Mock mode is not affected. |
| The API will not start, or the console says it can't reach the API | Check that `python -m provenance.api` is running and the `set` lines were in that window. Otherwise switch to mock mode: `?mock=1`. |
| Nothing works | Use the screenshots in `docs/images/` and the [README](../README.md). |
