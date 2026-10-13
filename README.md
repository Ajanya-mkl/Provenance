# Provenance

Provenance is a local supply-chain risk data pipeline. It ingests supplier and shipment data into a medallion warehouse, validates it before it reaches silver, and provides a rule-based risk score with a tool-using LLM investigator layered on top.

This repository follows the local-only design described in [project.md](project.md): no Docker, no Postgres, no MinIO, and no Airflow. Phase 1 sets up the Python environment and DuckDB warehouse. Phase 2 loads raw source batches into the bronze schema.

The `deploy/` directory is intentionally left untouched and unused for this local build.

## Windows quickstart

Run these commands from the repository root in PowerShell:

```powershell
Copy-Item .env.example .env
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
pip install -e .
pytest
ruff check .
```

When working from a freshly created environment, create `.env` from `.env.example` and replace the placeholder values with your local settings before running the pipeline.

## Phase 2: Bronze ingestion

From the repository root, initialize the warehouse and start Dagster:

```powershell
python -c "from provenance.db import init_db; init_db()"
dagster dev -m provenance.dags.definitions
```

Materialize the `ingest_all_sources` job from the Dagster UI. Each source asset
writes an idempotent Parquet batch under `data/bronze/` and refreshes its
`bronze` DuckDB table. USAspending requests contracts from the most recent
completed calendar month, paginates through that period, and caches the first
successful response in `data/raw/usaspending_sample.json` for offline fallback.
World Bank LPI loads all pages for `LP.LPI.OVRL.XQ`. The DataCo profile and
supplier-linkage decision are documented in [docs/data_profile.md](docs/data_profile.md).
The selected approach is option A: any future DataCo-to-USAspending mapping
must be explicitly simulated, not presented as a source-backed supplier join.

## Phase 4: Silver layer (dbt)

Stop `dagster dev` first; DuckDB allows only one writer. From the repository
root, with the virtual environment active:

```powershell
dbt build --project-dir src/provenance/transform --profiles-dir src/provenance/transform
```

`profiles.yml` reads
`PROVENANCE_DUCKDB_PATH` and falls back to `data/provenance.duckdb`, which is
relative to the folder you run dbt from. To run from another folder, set an
absolute path first:

```powershell
$env:PROVENANCE_DUCKDB_PATH = "$PWD\data\provenance.duckdb"
```

`dbt build` loads the seeds, builds the staging, intermediate and silver models
in the `silver` schema, and runs every test. To run the gates and silver
together in Dagster, use the `gates_and_silver` job. Design notes are in
[docs/architecture.md](docs/architecture.md); thresholds and the country mapping
are in [docs/data_profile.md](docs/data_profile.md).

## Repository layout

- `config/`: non-secret application and logging config.
- `data/`: local DuckDB file and parquet storage; ignored by Git except `.gitkeep` placeholders.
- `logs/`: rotating application logs; ignored by Git except `.gitkeep`.
- `src/provenance/`: Python package, DuckDB initializer, bronze loaders, and Dagster definitions.
- `tests/`: smoke and unit tests.
- `docs/data_profile.md`: DataCo dataset profile and supplier-linkage decision.
- `project.md`: [full project playbook and phase checklist](project.md).

## Current Phase 2 scope

Bronze ingestion is implemented for the three Phase 2 sources. Future phases
add validation, transformations, risk scoring, and the agent.
