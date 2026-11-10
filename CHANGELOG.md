# Changelog

One entry per phase tag, newest first. Test counts are `def test_` functions in `tests/` at that tag (`git grep`), so parametrised cases count once; where a phase's collected total was measured it is given as well. Phase 8 had not been tagged when this was written.

## 1.0.0: Phase 8, documentation, CI and clean-up

No product behaviour changes.

- Rewrote `README.md` and `docs/architecture.md`; added `docs/agent_design.md`, `data_dictionary.md`, `user_guide.md`, `limitations.md`, `testing.md`, `demo_script.md` and this file.
- Added CI: `.github/workflows/ci.yml`, with Python and web jobs.
- Removed unused scaffolding: empty agent, tool and DAG stubs, empty prompts and templates, empty test files, `deploy/` (Docker, Postgres, MinIO), `config/logging.yaml`, `profiles.example.yml`.
- Removed unused dependencies: `langgraph`, `langchain-core`, `anthropic`, `pytest-mock`. `httpx` is now pinned in `requirements-dev.txt`, because tests import it.
- Moved `demo_queries.py` to `scripts/`. `.env.example` lists only variables in use. Version set to 1.0.0.
- Two tests relied on local conditions and were fixed: one needed the DataCo CSV, and the full-flow test's polling could fail its own run.
- **Found and documented, not fixed, since this phase changes no behaviour** (see [limitations](docs/limitations.md#running-it)): console polling can fail a scoring run (4 of 12 when measured); the first pipeline run fails at silver; a `rules_version` change fails on a recurring flag.
- **Fresh-clone check (Windows, Python 3.12.1, Node 24.11.1):** no CSV, warehouse or real `.env`, with a guard blocking non-loopback connections: 297 tests passed, 0 connections blocked; ruff, `npm ci`, lint, 16 web tests and the build passed. Then, from an empty warehouse: first run stopped at silver as documented, `dbt build` passed 88 of 88, and the pipeline succeeded online and offline, giving 211 scores (61 low, 130 medium, 20 high), 20 flags and 56 SCD2 versions; the integration test passed 5 of 5, largest difference 2.33e-15.

## `phase-10d` (2026-11-06): the agent and onboarding made visible

- "Investigate with agent" on a flag: `POST /api/investigations/{event_id}/run` starts a background subprocess; live status; 409 when busy, 400 with no key. The agent still only proposes.
- Overview: New dataset button and Agent card (`GET /api/agent/summary`). "Datasets & pipelines"; agent entries labelled in Recent activity; four-step explainer.
- Every model failure in the mapping agent now falls back to the heuristic with a note, where a connection error used to escape as a 500.
- `tests/conftest.py`: no test can read the real `.env` or reach the model service.
- 246 test functions; 297 tests collected and passing by default, plus 5 integration.

## `phase-10c` (2026-11-04): console flow for datasets (10c-2)

- Upload → Profile → Mapping → Run → Results in the console, plus a dataset selector for Overview and Flags.
- `POST /api/datasets/upload`, background runs with `meta.runs` and status files, `GET /api/datasets/{id}/results`, `?dataset=`.
- API bound to localhost and refusing other clients.
- Country found by its values (German, French and others via Babel), `YYYYMMDD` dates, order-date preference, a full factor report, the `integration` marker deselected by default.
- Mapping evaluation: every field right heuristic 9/11, agent 10/11, heuristic-first 10/11.
- 227 test functions; 269 collected.

## `phase-10c-1` (2026-11-03): scoring any approved dataset

- Canonical silver with rejects, country normaliser, the flagship's metrics and mart over the canonical table, frozen per-dataset bounds in `meta.dataset_rules`, `score_dataset` job and CLI.
- `FlagTarget` generalised the scorer; dataset events prefixed `ds_<id>:`. Investigation tools accept a dataset.
- Heuristic-first mapping; the contract's optional `cancelled` field.
- Fixed a 10a bug: the "no empty values" rule used the rounded null percentage, so DataCo quarantined itself.
- **Equivalence test:** DataCo through the generic path gives 211 of 211 scores within 2.3e-15 and the same 20 flags.
- 208 test functions; 250 collected.

## `phase-10b` (2026-11-03): column mapping

- The contract, heuristic mapper, mapping agent with read-only tools and no text values, deterministic validator, one retry then heuristic fallback.
- `meta.mappings` versions with edit, approve and reject, audit entries, CLI and API.
- First mapping evaluation.
- 175 test functions; 207 collected.

## `phase-10a` (2026-11-03): dataset onboarding

- Any CSV: pre-checks, profile, generated Great Expectations rules (hard and tolerance), gate, generic bronze in `ds_<id>`, registry in `meta`.
- `onboard_dataset` Dagster job and CLI.
- 155 test functions; 175 collected.

## `phase-9b` (2026-11-03): float-stable payload hash, event repair

- The payload hash rounds floats to 10 decimals, because a gold rebuild moved scores by about 1e-16 and falsely superseded events.
- One-off `agent/repair_noise_events.py` reopened originals and resolved duplicates, deleting nothing.
- 143 test functions.

## `phase-9a` (2026-10-31): safe re-runs and new data

- Quarantine moves batches and each batch is gated once, so a bad batch no longer blocks later runs. Cross-batch duplicate check.
- Append-only flag events; flags resolved, not deleted.
- All-or-nothing silver and gold via a restore point; lock handling; early CSV pre-checks.
- Top factor follows the direction of the change (`cat_boxing_mma` 2017-Q3 is now `delay_stddev`, +0.216).
- 140 test functions (same commit as `phase-7c`).

## `phase-7c` (2026-10-31): API and console

- Read-only FastAPI and the React console: Overview, Flags, Investigations, Approvals, Pipeline, with mock mode and number-level receipts.

## `phase-7b` (2026-10-20): approval guardrail, memory, evaluation

- The investigation agent (read-only tools, loop, grounding) with its approval guardrail in code, hash-chained audit log and recall memory.
- Evaluation on 18 cases: top factor 0.938 of 16 (1.0 after the `phase-9a` fix for `cat_boxing_mma`), grounded 0.944 of 18.
- 103 test functions.

## `phase-6-done` (2026-10-14): orchestration

- `provenance_full_pipeline`, the single-writer run queue, schedule and sensor (stopped by default), plain runner, offline mode for USASpending.
- 54 test functions.

## `phase-5-done` (2026-10-13): gold and risk engine

- Gold marts, explainable scores, flag replay.
- Trailing country risk and hysteresis took flags from 43 to 20 (61 low, 130 medium, 20 high over 211 supplier-quarters).
- 39 test functions.

## `phase-4-done` (2026-10-13): silver and SCD2

- dbt staging and facts, `dim_supplier_scd2` from quarterly metrics. Delivery-band tracking with two-quarter confirmation took versions from 119 to 56 across 50 suppliers.
- 29 test functions.

## `phase-3-done` (2026-10-13): quality gate

- Great Expectations gate, `bronze.dq_results`, quarantine, `dq_failure` events.
- 19 test functions.

## `phase-2-done` (2026-10-11): foundation and bronze

- Python project, DuckDB warehouse, idempotent bronze loaders for DataCo, USASpending and World Bank.
- DataCo profile: 180,519 rows, 53 columns. Decision to use category as the supplier proxy.
- 8 test functions.
