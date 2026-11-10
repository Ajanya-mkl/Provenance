# Testing

## Strategy

- **Temporary databases only.** Unit tests build their own warehouse under pytest's `tmp_path`, or a copy of a template. They never open `data/provenance.duckdb`.
- **No network, no model.** The LLM is scripted, and nothing reaches Groq (see [LLM isolation](#llm-isolation)).
- **No local data.** `pytest -q` needs no DataCo CSV, warehouse or `.env`. Fixtures are in the repository or generated.
- **Real subprocesses where it matters.** Scoring runs and the investigation run path start real background processes against temporary databases.
- **Tests watch a run's process, not its files.** Polling a run during its start can make it fail ([limitations](limitations.md#running-it)), so `_wait` in `test_console_api.py` waits for the process to exit and then reads the API once.
- **The expensive proof is separate.** The DataCo equivalence test is marked `integration` and deselected by default.
- **dbt models are tested by dbt**, at pipeline time (schema tests and the singular tests in `transform/tests/`), and the restore point turns a failing test into a clean rollback.

## Running

```bat
pytest -q
pytest -m integration -s
ruff check .
cd web
npm run lint
npm test
npm run build
```

`pyproject.toml` sets `addopts = "-m 'not integration'"`. A later `-m` overrides it, so `pytest -m integration` runs only those tests. `-s` shows the equivalence figures. The integration test takes about 5 minutes and skips cleanly without the CSV and warehouse.

## Python tests

297 pass by default, plus 5 integration tests: 302 collected.

| File | Tests | Covers |
|---|---:|---|
| `test_smoke.py` | 1 | The package imports; the version |
| `test_db.py` | 1 | Warehouse initialisation |
| `test_ingestion.py` | 11 | Source loaders, idempotent batches, API retries, cache fallback |
| `test_data_quality_gate.py` | 14 | The gate: pass, warn, quarantine; events; the migration |
| `test_silver_assets.py` | 2 | dbt silver assets and gate dependencies |
| `test_silver_scd2.py` | 8 | SCD2 bands, confirmation, carried quarters |
| `test_risk_engine.py` | 10 | Rules validation, scoring, tiers, hysteresis, gold assets |
| `test_orchestration.py` | 10 | Jobs, tags, schedule, sensor, offline mode, runner exit codes |
| `test_phase9a_hardening.py` | 24 | Top factor direction, quarantine moves, duplicates, append-only events, float-stable hash, locks, restore point, pre-checks |
| `test_agent_tools.py` | 20 | Each tool's output, bad arguments, read-only behaviour, recall |
| `test_agent_reasoning.py` | 18 | The loop, step limit, retries, grounding, recording outcomes, the LLM client |
| `test_agent_guardrails.py` | 16 | Allowed actions, approval required, `apply_action`, the audit chain and tampering |
| `test_agent_eval.py` | 5 | Evaluation scoring and caching |
| `test_agent_console_run.py` | 10 | Run endpoint: busy, missing key, unknown or resolved event; only proposes under both approval settings; ungrounded creates no action; the real subprocess fails plainly; the Agent card summary |
| `test_api.py` | 18 | DataCo endpoints, secrets never returned, approve and reject, locks, `?dataset=dataco` |
| `test_datasets.py` | 18 | Pre-checks, profile, generated rules, gate, bronze, identifier safety, the Dagster op |
| `test_mapping.py` | 69 | Validator, polarity, heuristic on fixtures, country by values, order-date preference, agent loop, heuristic-first, privacy, every model failure falling back, approval and versioning, CLI, API |
| `test_dataset_scoring.py` | 24 | Outcome kinds and polarity, hours to days, rejects, cancelled, country, excluded factors, not scoreable, frozen bounds, prefixed events, flagship untouched, re-scoring, Dagster, tools |
| `test_console_api.py` | 13 | Upload checks, duplicates, quarantine report, heuristic-first propose, run refusals, busy, the full flow with a real subprocess, failed runs, localhost only |
| `test_llm_isolation.py` | 5 | The guards below |
| `tests/integration/test_dataco_equivalence.py` | 5 | DataCo through the generic path equals the flagship |

### The equivalence test

It copies the warehouse into a temporary folder (the file keeps its name, because dbt views name their catalog after it), onboards the 96 MB CSV as an ordinary dataset, maps it through the contract, approves it, and scores it with `bounds_mode='flagship'`. Then it checks: silver per supplier and day; the mart metrics; scores within 1e-9, tiers and confidence; the flag set; and that the flagship's tables are unchanged. Last result: 211 scores on each side, largest difference 2.44e-15, 0 tier or confidence mismatches, the same 20 active flags.

## Web tests

16 tests in 5 files, in vitest with jsdom and Testing Library, against mock mode:

| File | Tests | Covers |
|---|---:|---|
| `overview.test.tsx` | 1 | Overview renders from fixtures |
| `receipts.test.tsx` | 2 | Number-to-tool receipts in reports |
| `stale.test.tsx` | 1 | Last good data and the banner on a 409 |
| `datasets.test.tsx` | 5 | Upload (type check, passing, quarantined), Mapping review (invalid edit, edit, approve, agent proposal not approvable), Results, busy |
| `agent.test.tsx` | 7 | The Investigate button through to a grounded report; no button when grounded; Agent card; sidebar and activity labels; Datasets explainer; busy; missing key |

## Fixtures

| Where | What |
|---|---|
| `tests/unit/agent_fixtures.py`, `conftest.py` | A small flagship warehouse (`warehouse` fixture) with one flag, `cat_widgets` |
| `tests/unit/scoring_fixtures.py` | `make_shipments_csv`: scoreable synthetic CSVs by outcome kind, country, quarters, cancelled share and bad rows, where one supplier deteriorates; `add_lpi` for a small LPI table |
| `tests/fixtures/datasets/` | `shipments_clean.csv`, `shipments_dirty.csv`, `student_marks.csv` |
| `tests/fixtures/datasets/mapping/` | Eleven-case evaluation fixtures `a`–`e`, `h`–`k` (the other two are the clean and marks files) |
| `web/src/mock/fixtures.json`, `datasets.json` | Generated from the real API on copies and temporary warehouses by `web/scripts/` |

## LLM isolation

`tests/conftest.py` runs before any test or project module is imported:

- `LLM_API_KEY` is a dummy and `GROQ_BASE_URL` is a closed local port (`http://127.0.0.1:9`), so anything that builds a real client fails fast without leaving the machine. Subprocesses inherit both.
- `load_dotenv` is replaced with a no-op in the test process, so a CLI's `main()` or `GroqLLM.from_env()` cannot pull in the real `.env`, even after a test deletes the key.

`test_llm_isolation.py` checks all of this, including that a real client gets the dummy values and that a subprocess keeps the dummy key. The one known gap is in [limitations.md](limitations.md#tests).

`test_api_propose_survives_a_real_connection_failure` uses the real Groq client against that closed port, so the SDK raises its own connection error.

## CI

`.github/workflows/ci.yml`, on push and pull request, `ubuntu-latest`:

- **python:** Python 3.12 with pip cache, install, `ruff check .`, `pytest -q`. `LLM_API_KEY` is a dummy, `GROQ_BASE_URL` unreachable, `PROVENANCE_USASPENDING_OFFLINE=true`. There is no `.env`, CSV or warehouse.
- **web:** Node LTS with npm cache, `npm ci`, `npm run lint`, `npm test`, `npm run build`, in `web/`.

CI has not run yet, so the Ubuntu run is **to verify**. Locally, `pytest -q` was run from a fresh Windows clone with a guard that blocks every non-loopback connection; see the fresh-clone result in the [changelog](../CHANGELOG.md).
