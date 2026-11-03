"""Dagster job `onboard_dataset`: onboard one uploaded CSV, nothing else.

Run config: {"ops": {"onboard_dataset_op": {"config": {"csv_path": ..., "dataset_name": ...}}}}

It runs pre-checks -> profile -> suite -> gate -> bronze for that file only. It
is an op job, not an asset selection, so it never re-ingests DataCo,
USASpending or World Bank. It carries the duckdb_writer tag (one writer run at
a time) and turns a DuckDB lock conflict into a retry.
"""

# No `from __future__ import annotations`: Dagster inspects the real type of
# the `context` annotation on the op function.
import duckdb
from dagster import (
    Failure,
    MetadataValue,
    OpExecutionContext,
    Output,
    RetryRequested,
    in_process_executor,
    job,
    op,
)

from provenance.datasets.onboard import Paths, onboard, render

DUCKDB_WRITER_TAGS = {"dagster/concurrency_key": "duckdb_writer"}
LOCK_RETRIES = 3
LOCK_RETRY_SECONDS = 5


@op(config_schema={"csv_path": str, "dataset_name": str})
def onboard_dataset_op(context: OpExecutionContext) -> Output:
    config = context.op_config
    try:
        result = onboard(
            config["csv_path"],
            config["dataset_name"],
            paths=Paths.from_env(),
            log=context.log.info,
        )
    except duckdb.IOException as error:
        # Another process holds the warehouse; registration steps are resumable.
        raise RetryRequested(
            max_retries=LOCK_RETRIES, seconds_to_wait=LOCK_RETRY_SECONDS
        ) from error

    metadata = {
        "status": MetadataValue.text(result.status),
        "dataset_id": MetadataValue.text(result.dataset_id or ""),
        "summary": MetadataValue.md(f"```\n{render(result)}\n```"),
    }
    if result.status in ("rejected", "quarantined"):
        raise Failure(
            description=result.message + " " + "; ".join(result.problems), metadata=metadata
        )
    return Output(result.status, metadata=metadata)


@job(name="onboard_dataset", tags=DUCKDB_WRITER_TAGS, executor_def=in_process_executor)
def onboard_dataset_job() -> None:
    onboard_dataset_op()
