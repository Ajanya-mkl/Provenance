# Provenance — Project Playbook (local, no Docker)

Working document for the whole build. Read it once top to bottom, then use the phase sections as your checklist. Keep this file in the repo root next to `README.md`.

**Stack decision:** everything runs locally from the VS Code terminal on Windows. There is no Docker and no deployment. Airflow cannot run natively on Windows, so it is replaced by Dagster. Postgres is replaced by DuckDB, and MinIO by plain parquet folders.

Assumed layout: the empty scaffold (`provenance-structure`) with code under `src/provenance/`.

---

## 1. What we are building

**Provenance** is a medallion data pipeline (bronze → silver → gold) for supply chain risk, with a tool-using LLM agent on top.

- The pipeline ingests three public sources, validates them through a quality gate, models supplier history with SCD Type 2, and produces an explainable rule-based risk score.
- The agent wakes up when a supplier is flagged high-risk or a batch fails validation. It gathers evidence with tools, writes an investigation report, and proposes an action.
- Any action that changes something (alerts, re-runs, quarantine) stops at a human-approval gate.

The rule-based score is the source of truth. The agent explains it and never overrides it.

## 2. Stack

| Layer | Tool | Why |
|---|---|---|
| Warehouse | DuckDB (`data/provenance.duckdb`) | Single file, no server, schemas `bronze`, `silver`, `gold`, `agent` |
| File storage | Parquet files under `data/bronze`, `data/silver`, `data/gold`, `data/quarantine` | Replaces MinIO |
| Orchestration | Dagster (`dagster`, `dagster-webserver`) | Runs natively on Windows, has a visual run graph |
| Transform | dbt with `dbt-duckdb` | SCD2 via snapshots, tests |
| Data quality | Great Expectations 1.x | Validation gate before silver |
| Agent | LangGraph + an LLM API (`anthropic`) | Tool-using investigation loop |
| Tests / lint | pytest, ruff | |

## 3. Architecture at a glance

```
Sources            BRONZE        DQ GATE          SILVER          GOLD         RISK SCORE
DataCo        ──►  raw     ──►   Great      ──►   conformed  ──►  marts   ──►  rule engine
USASpending        parquet       Expectations     facts + SCD2                  (rules.yaml)
World Bank LPI                       │ fail                                        │ new high-risk flag
                                     ▼                                             ▼
                                QUARANTINE  ─────────────────────────►   PROVENANCE AGENT
                                                                    perceive → reason → act → remember
                                                                                   │
                                                              report + proposed action ──► human approval
```

## 4. Repo map

| Path | Purpose | Built in |
|---|---|---|
| `config/` | Non-secret settings, logging config | Phase 1 |
| `src/provenance/db.py` | Creates the DuckDB file and schemas | Phase 1 |
| `src/provenance/ingestion/` | Bronze loaders and source schemas | Phase 2 |
| `src/provenance/data_quality/` | Expectation suites, checkpoints | Phase 3 |
| `src/provenance/transform/` | dbt project (silver and gold) | Phases 4–5 |
| `src/provenance/risk_engine/` | Rules and scorer | Phase 5 |
| `src/provenance/dags/` | Dagster assets, jobs, and schedules (folder name kept from the scaffold) | Phases 2, 3, 6 |
| `src/provenance/agent/` | Tools, prompts, memory, guardrails, orchestrator | Phase 7 |
| `src/provenance/reports/templates/` | Investigation report template | Phase 7 |
| `tests/` | Unit and integration tests | every phase |
| `docs/` | Architecture, agent design, data dictionary | Phase 8 |
| `data/` | Local data, git-ignored except `.gitkeep` | Phase 2 |
| `logs/` | Application logs, git-ignored | Phase 1 |
| `deploy/` | **Unused.** Left over from the Docker plan; leave empty | — |
| `.github/workflows/ci.yml` | CI | Phase 8 |

## 5. Working rules

1. Build in phase order. Do not start the agent before the pipeline runs reliably end to end.
2. Each phase ends with a passing verification, a commit, and a git tag (`phase-1-done`, and so on).
3. Do not fill placeholders ahead of their phase. Empty files are fine until their phase arrives.
4. Secrets live only in `.env`, which is git-ignored. `.env.example` holds placeholders.
5. The agent may read freely. Every write action goes through `agent/guardrails.py`.
6. Every phase has a "Done when" check. If it does not pass, the phase is not done.
7. Keep it Windows-safe: LF line endings, no `make` dependency, raw commands in the README.
8. DuckDB allows one writer at a time. Run the pipeline first, then the agent; do not run them at the same moment.

---

## 6. Phases

### Phase 0 — Scaffold (done)
Empty folder structure and placeholder files.

### Phase 1 — Foundation
**Goal:** a clean Python environment, configuration, and an initialised DuckDB file. No pipeline logic.

**Build**
- Root files: `.gitignore`, `.gitattributes`, `.env.example`, `requirements.txt`, `requirements-dev.txt`, `pyproject.toml`, `pytest.ini`, `README.md`, `LICENSE`
- `config/config.example.yaml`, `config/logging.yaml`
- `src/provenance/__init__.py` and `src/provenance/db.py` (creates the DuckDB file and four schemas, idempotent)
- Smoke tests for the package and for the database init

**Done when**
- Fresh venv: `pip install -r requirements-dev.txt` and `pip install -e .` succeed
- `pytest` passes and `ruff check .` is clean
- The init creates `data/provenance.duckdb` with schemas `bronze`, `silver`, `gold`, `agent`
- `dagster --version` and `dbt --version` both run

The paste-ready prompt for this phase is in section 7.

### Phase 2 — Bronze ingestion
**Goal:** raw data from all three sources lands in bronze, untouched, with lineage metadata.

**Build**
- Manual step: DataCo CSV already in `data/raw/` (git-ignored, never committed)
- Three loaders in `ingestion/sources/` that add `_batch_id`, `_source`, `_loaded_at`
- Write raw output as parquet under `data/bronze/` and register bronze tables in DuckDB
- A watermarks table for incremental loads (optional, see the 3-week scope note)
- Versioned source schemas in `ingestion/schemas/`
- Dagster assets for each loader
- Profile the data and settle the supplier-linkage decision (section 9, item 1)

**Done when:** the Dagster job runs green, bronze row counts match the source, and re-running does not create duplicates.

### Phase 3 — Data quality gate
**Goal:** nothing reaches silver without passing validation.

**Build**
- Great Expectations suites for bronze (schema, nulls, uniqueness, ranges)
- A gate step: pass continues to silver; fail writes the batch and its validation result to `data/quarantine/` and emits an event
- Store validation results so the agent can read them later

**Done when:** a deliberately corrupted file is quarantined and halts the pipeline, and a clean file passes.

### Phase 4 — Silver layer
**Goal:** conformed facts and a supplier dimension with full history.

**Build**
- dbt project wired to DuckDB (`dbt-duckdb`)
- `fact_shipments`, `fact_purchase_orders`, `dim_supplier_scd2` (dbt snapshot or hash-diff model)
- dbt tests, including SCD2 invariants (one current row per supplier; `end_date` null exactly when `is_current`)
- A schema-evolution test: an added upstream column does not break the build

**Done when:** `dbt build --select silver` is green, and a simulated attribute change creates a new version row and closes the old one.

### Phase 5 — Gold marts and risk engine
**Goal:** explainable supplier risk scores.

**Build**
- `mart_supplier_risk`, `mart_shipment_delay`
- `scorer.py` reads `rules.yaml`, writes `gold.supplier_risk_score` with a `contributing_factors` field
- New-flag detection: diff against the previous run
- Unit tests for tier boundaries and for factors summing to the score

**Done when:** every score can be explained from its stored factors, and new high-risk flags are recorded.

### Phase 6 — Orchestration end to end (Dagster)
**Goal:** one run goes from ingestion to flagged suppliers without manual steps.

**Build**
- One Dagster job chaining ingest → DQ gate → silver → gold → score → emit flag events
- Retries, idempotent reruns, a schedule or sensor
- A job or sensor that accepts a case payload and starts an agent investigation

**Done when:** `dagster dev` shows a full green run, and a forced failure is visible and recoverable in the UI.

**Fallback:** if Dagster gives trouble on your machine, replace it with a plain Python runner script that calls the same steps in order. The pipeline logic does not change.

### Phase 7 — Agent layer
**Goal:** an evidence-backed investigation with bounded autonomy.

**Build, in this order**
1. Read-only tools (`query_gold_layer`, `get_supplier_scd2_history`, `get_country_risk`) with tests against the real DuckDB file
2. Minimal LangGraph loop with one tool
3. Remaining tools (`rerun_dq_gate`, `send_alert`)
4. Investigation memory (`agent.investigations` table)
5. Approval guardrail: CLI prompt first, then a `pending_actions` table if time allows
6. Report generation from the template
7. A small evaluation set of 5–10 seeded cases with expected findings

**Done when**
- `python -m provenance.agent.orchestrator --supplier-id <id>` produces a report whose claims trace to tool results
- A write action cannot run without approval
- A repeat case references the earlier investigation

### Phase 8 — CI, tests, docs, demo
**Goal:** someone else can clone it, run it, and trust it.

**Build**
- GitHub Actions: lint and unit tests, plus `dbt build` against a small fixture DuckDB file (no service containers needed)
- End-to-end integration test on a small fixture dataset
- Finish `docs/architecture.md`, `docs/agent_design.md`, `docs/data_dictionary.md`
- README with screenshots; demo script (section 10); a backup screen recording

**Done when:** CI is green on `main`, and a fresh clone runs following only the README.

---

## 7. Phase 1 prompt (paste-ready)

Use this in Claude Code (or any assistant that can read and edit files in the open repo).

```
You are helping me build "Provenance", a supply chain risk data pipeline with an LLM agent on top. Read project.md for background. We are NOT using Docker, Postgres, MinIO, or Airflow. Everything runs locally from the VS Code terminal on Windows (native Python 3.11, PowerShell). Do Phase 1 only, using the stack below.

STACK
- Warehouse: DuckDB (single file at data/provenance.duckdb), schemas bronze, silver, gold, agent
- Storage: local parquet files under data/bronze, data/silver, data/gold, data/quarantine
- Orchestration: Dagster (dagster, dagster-webserver)
- Transform: dbt-duckdb; Data quality: great_expectations 1.x; Agent: langgraph, langchain-core, anthropic

TASKS
1. Fill the empty root files: .gitignore (Python, .venv, .env, dbt artifacts, GE uncommitted output, data/*.duckdb, contents of data/* and logs/* except .gitkeep), .env.example (LLM API key, model name, AGENT_REQUIRE_APPROVAL, DuckDB path), requirements.txt, requirements-dev.txt (-r requirements.txt + pytest, pytest-mock, ruff), pyproject.toml (src layout, package "provenance", ruff config), pytest.ini (testpaths + "integration" marker), README.md (overview, Windows quickstart using raw commands, repo layout, link to project.md), LICENSE (MIT, holder "Ajanya M"), and add .gitattributes enforcing LF line endings.
2. config/config.example.yaml (non-secret settings: DuckDB path, data folder paths, schema names) and config/logging.yaml (console + rotating file handler into logs/).
3. src/provenance/__init__.py with only __version__ = "0.1.0". Add src/provenance/db.py with a function that creates data/provenance.duckdb and the four schemas if missing (idempotent). Add tests/unit/test_smoke.py (imports the package, asserts version) and tests/unit/test_db.py (runs the init against a temp path and asserts the four schemas exist).
4. Leave deploy/ untouched and note in README that it is unused.
5. Leave every other placeholder empty. Leave .github/workflows/ci.yml empty until Phase 8.

CONSTRAINTS
- Do not delete, rename, or move existing files. New files are limited to .gitattributes, requirements-dev.txt, src/provenance/db.py, tests/unit/test_smoke.py, tests/unit/test_db.py.
- Do not touch data/raw/*.csv.
- No secrets in committed files. Pin versions; tell me about conflicts instead of silently downgrading.
- Ask before deviating from this prompt.

VERIFY (show output)
1. python -m venv .venv, activate, pip install -r requirements-dev.txt, pip install -e .
2. pytest passes and ruff check . is clean.
3. Run the DuckDB init and confirm the four schemas exist.
4. Confirm `dagster --version` and `dbt --version` run.

DELIVERABLE: files created or changed, pinned versions, deviations, anything unverified. Then stop. Do not start Phase 2.
```

## 8. Prompt template for Phases 2–8

```
Read project.md (especially section 5 rules and Phase N). Implement Phase N only.

CONTEXT: <what is done so far, decisions already made>
SCOPE: files under <paths>. Do not touch <other areas>.
TASKS: <copy the Build bullets for Phase N>
CONSTRAINTS: no new folders without asking; no secrets in code; Windows-safe; small logical commits.
VERIFY: run <commands from the "Done when" list> and show the output.
REPORT: files changed, decisions made, anything unverified. Stop after Phase N.
```

---

## 9. Open decisions and known risks

1. **Supplier linkage (decide by end of Phase 2).** As far as I know, the DataCo dataset has no true supplier identifier, and USASpending vendors do not join to it. Verify by profiling. Options: (A) build a bridge table that deterministically maps DataCo product category or department to USASpending vendors, documented openly as a simulated linkage; (B) define "supplier" as a DataCo dimension (department, market, or order region) and use USASpending only to demonstrate SCD2 mechanics. Either is defensible if you are upfront about it at the viva.
2. **Real SCD2 history from USASpending.** Confirm the API actually exposes attribute changes over time. If not, generate history by loading dated snapshots incrementally, and say so.
3. **World Bank LPI cadence.** The classic survey index is published every couple of years, not annually. Treat it as slowly changing reference data and check the latest year available.
4. **DataCo may be synthetic.** The Mendeley page attributes it to a company; at least one paper describes it as synthetic. Do not describe it as "real" in your report; call it a public benchmark dataset.
5. **Dependency conflicts.** Dagster, dbt-duckdb, Great Expectations 1.x, and the LangGraph stack share one venv now. If pip reports conflicts, tell the assistant instead of letting it downgrade silently; the fallback is a second venv for dbt.
6. **DuckDB single writer.** Do not run a pipeline job and an agent investigation at the same time.
7. **Dagster on Windows.** If it misbehaves, use the plain Python runner fallback in Phase 6.
8. **Approval mechanism.** Start with the CLI prompt.
9. **LLM cost and control.** Cap tokens per investigation, cache tool results, log every call.
10. **Agent evaluation.** Seed a few known cases so "it works" is measurable rather than anecdotal.

## 10. Demo script

1. Show the Dagster run graph with a full green run.
2. Drop in a corrupted file and show it land in `data/quarantine/` while the pipeline halts.
3. Show a supplier's SCD2 history (old version closed, new version current).
4. Show a risk score with its contributing factors.
5. Trigger the agent on a flagged supplier and show its tool calls and the generated report.
6. Show the approval gate blocking an alert, then approving it.
7. Re-run on the same supplier and show the agent referencing the earlier investigation.