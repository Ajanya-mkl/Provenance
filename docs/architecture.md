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
