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
