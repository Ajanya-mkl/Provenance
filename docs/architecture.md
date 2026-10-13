# Architecture: Phase 1

The local platform runs PostgreSQL as both the `provenance` warehouse and the
separate `airflow` metadata database. PostgreSQL initialization creates the
warehouse schemas `bronze`, `silver`, `gold`, and `agent`; later phases add
tables. MinIO stores object data in the four layer buckets. Airflow uses the
LocalExecutor and discovers DAGs under `src/provenance/dags`.

Postgres 16.4 Alpine and dated MinIO server/client releases are pinned to avoid
floating image tags; the `mc` release is from the same release period as the
server image for compatibility.

The Airflow image is pinned to Apache Airflow 2.9.3 on Python 3.11. Airflow
provider installation uses the official constraints file matching that exact
Airflow and Python version. Project runtime dependencies are installed in
`/opt/venvs/provenance`, separately from Airflow's Python environment: dbt,
Great Expectations, and the LangGraph stack have dependency constraints that
can conflict with Airflow, and must not alter Airflow's constrained packages.
Future DAG tasks that need those libraries should invoke the isolated
environment explicitly.

Great Expectations 1.23.2 is paired with pandas 2.2.3 because earlier 1.x
releases such as 1.3.2 required pandas below 2.2. dbt-core 1.8.8 is pinned
alongside dbt-postgres 1.8.2 because that adapter's broad core requirement can
otherwise select a dbt 2.0 release candidate. The dependency pins keep the
application install deterministic; `requirements.txt` is also used as the
source of package dependencies by `pyproject.toml`. The
container services persist PostgreSQL, MinIO, and Airflow logs with named
volumes. The repository `logs/` directory is separately bind-mounted for
application logs.

## Phase 4: silver layer (dbt-duckdb)

The dbt project lives in `src/provenance/transform/` and writes to the `silver`
schema of `data/provenance.duckdb`. Bronze tables are dbt sources. Every staging
model reads only batches whose `bronze.dq_results.status` is in
`var('approved_gate_statuses')`, which is `validated` or `passed_with_warnings`.
A quarantined batch never reaches silver, even if its rows are still in bronze.

Model flow:

```
bronze.* (sources) ──► stg_dataco_order_items ──► fact_shipments, fact_purchase_orders
                   │                          └─► int_supplier_quarter_metrics ──► dim_supplier_scd2
                   ├─► stg_usaspending_awards   (date_valid flag; not linked to suppliers)
                   └─► stg_worldbank_lpi        (economies only; aggregates removed via seed)
seeds: country_iso3_map (DataCo Spanish names → ISO3), wb_country_reference (World Bank list)
```

In Dagster, `silver_dbt_assets` (dagster-dbt) maps each bronze source to its
Phase 3 gate asset. The `gates_and_silver` job therefore runs dbt only after
all three gates succeed, and a gate failure skips silver.

### Why dim_supplier_scd2 is built in SQL, not with dbt snapshots

A dbt snapshot compares the current state of a table with its previous
snapshot each time it runs, and stamps changes with the run time. It can only
record history from the first run onward. Our history is in the past: DataCo
order dates run from 2015 to January 2018, and the supplier attributes are
derived per quarter from those dates. A snapshot taken today would collapse
the whole series into one version dated today, and it cannot back-date
versions to 2015Q3 or 2017Q4.

So `dim_supplier_scd2` is rebuilt deterministically from the quarterly series
on every run, using the gaps-and-islands pattern:

1. Rate each supplier-quarter: `delivery_band` from the late rate and
   `volume_band` from the shipment count, with thresholds from dbt vars.
2. A low-volume or partial quarter gets no rating; it carries the previous
   bands forward and so can never open a version.
3. Hash the two bands (`row_hash`). A version starts wherever the hash differs
   from the previous quarter's.
4. `effective_from` is the first quarter of the version. `effective_to` is the
   next version's `effective_from` (exclusive), and is null on the current row.

Because the build is a pure function of the gated bronze data and the vars,
re-running gives identical history, and changing a threshold reproducibly
re-derives all past versions. Singular dbt tests enforce the SCD2 invariants:
one current row per supplier, `effective_to` null exactly when current, no
gaps or overlaps, and consecutive versions always differ.

## Orchestration (Phase 6)

### One job

`provenance_full_pipeline` (in `src/provenance/dags/definitions.py`) selects
every asset. Asset dependencies, not a hand-written op list, fix the order:

```
dataco_shipments → usaspending_contracts → worldbank_lpi        (bronze)
  → gate_dataco_shipments → gate_usaspending_contracts → gate_worldbank_lpi
  → silver_dbt_assets (dbt build: seeds, staging, intermediate, silver)
  → gold_dbt_assets   (dbt build: gold marts and their tests)
  → supplier_risk_scoring (scores, flag replay, risk_flag events)
```

The bronze loaders and the gates are chained one after another on purpose, so
that only one step writes to DuckDB at a time. The job uses the in-process
executor. A gate that quarantines a batch raises `dagster.Failure`; Dagster
then skips every downstream step and marks the run failed. Because a gate
validates every batch folder under `data/bronze/<source>/`, the run keeps
failing until the bad batch is removed from bronze. It is already preserved
under `data/quarantine/`, so removing it from bronze is the recovery step.

Three smaller asset jobs stay because unit tests run them:
`ingest_all_sources` (test_ingestion), `bronze_quality_gates`
(test_data_quality_gate) and `gates_and_silver` (test_silver_assets). The
duplicate `ingest_and_validate_all_sources` and `phase5_risk_pipeline` jobs
were removed. The implicit `__ASSET_JOB`, which the UI uses for ad hoc
"Materialize" clicks, is redefined only to carry the writer tag.

### Retries and the offline cache

Only `usaspending_contracts` and `worldbank_lpi` call external APIs, so only
they have a `RetryPolicy`: 3 retries, exponential backoff from 1 s. Gates, dbt
and the scorer are deterministic: retrying them would repeat the same failure.
The first three attempts fail on a network error. The last retry may fall back
to the cache: the Phase 2 USASpending sample (`data/raw/usaspending_sample.json`)
or the latest World Bank bronze batch. The materialization then records
`cache_fallback_used`, `cache_fallback_source` and `cache_fallback_reason`. If
there is no cache, the run fails visibly.

USASpending pages are re-fetched for the last complete month on each run, so
overlapping monthly batches accumulate in bronze. `stg_usaspending_awards`
keeps one row per `generated_internal_id`, taken from the most recently loaded
approved batch. No silver, gold or SCD2 model reads USASpending.

### Reruns

Every stage is idempotent: bronze batch ids are content hashes, so an
unchanged source rewrites the same batch; dbt rebuilds tables from bronze; the
gate upserts `bronze.dq_results` by `(source, batch_id)`; and the scorer's flag
replay emits a flag or event only for a tier change it has not recorded
before. A rerun on unchanged inputs therefore adds no rows, flags or events.
New rows appear only if an API returns new content.

### Single writer

DuckDB allows one writer process. `DAGSTER_HOME` points to the repository's
`.dagster/` folder (as an absolute path, for example
`C:\Users\<you>\...\provenance-structure\.dagster`). Only its
`dagster.yaml` is committed; run history and schedule state are git-ignored. It
configures the `QueuedRunCoordinator` with a limit of one running run for the
tag `dagster/concurrency_key=duckdb_writer`. The full pipeline, the investigation job and
the implicit asset job all carry that tag, so runs from the UI, the schedule
and the sensor queue behind each other.

`python -m provenance.run_pipeline` runs in-process and so bypasses the queue.
Instead, it checks the instance for queued or running writer runs and refuses
to start (exit code 2) if it finds any. DuckDB's own file lock is the last line
of defence: a second writer process fails with a lock error rather than
corrupting the file.

### Schedule

`provenance_full_pipeline_weekly` takes its cron from
`config/config.example.yaml` (`orchestration.weekly_pipeline_cron`) and
runs in UTC. It is `STOPPED` by default, because this is a local project.

### Agent handoff

`agent.events` has `investigation_status` (`open` → `queued` →
`investigated`). The migration in `ensure_agent_events_table` adds the column
with default `open` to existing tables and backfills nulls. Statuses already
set are left alone. New tables also get a `CHECK` constraint on the three
values.

`agent_investigation_sensor` (stopped by default, 30 s interval) runs one
transaction per tick. It selects `risk_flag` and `dq_failure` events that are
`open` and have no `resolved_at` or `resolution`, and marks them `queued`. It
then requests one `agent_investigation_job` run with every case payload in the
run config. A second tick finds nothing open and skips. Resolved events are
never queued; seeded demo events are queued like any other. If a gate fails
again on an event that is already queued, the event stays queued and is not
requeued. If a pipeline run holds the DuckDB lock, the tick skips and retries
later. The Phase 6 job only logs the cases; Phase 7 replaces it with the agent
and sets `investigated`.
