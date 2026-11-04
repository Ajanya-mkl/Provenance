"""Dagster job `score_dataset`: silver, metrics, mart and scores for one dataset.

Run config: {"ops": {"score_dataset_op": {"config": {"dataset_id": "<12 hex>"}}}}
(optional: "bounds_mode": "dataset" | "flagship", "refreeze": bool).

It refuses (fails without writing) unless the dataset is 'mapped' and its
approved mapping is scoreable, and while a flagship restore point exists. It
carries the duckdb_writer tag (one writer run at a time), turns a DuckDB lock
conflict into a retry, and never touches DataCo's tables: it is an op job, not
an asset selection.
"""

# No `from __future__ import annotations`: Dagster inspects the real type of
# the `context` annotation on the op function.
import duckdb
from dagster import (
    Failure,
    Field,
    MetadataValue,
    OpExecutionContext,
    Output,
    RetryRequested,
    in_process_executor,
    job,
    op,
)

from provenance.dags.restore_point import has_restore_point
from provenance.datasets.onboard import Paths
from provenance.datasets.score import BOUNDS_MODES, ScoringRefused, render, score_dataset

DUCKDB_WRITER_TAGS = {"dagster/concurrency_key": "duckdb_writer"}
LOCK_RETRIES = 3
LOCK_RETRY_SECONDS = 5


@op(
    config_schema={
        "dataset_id": str,
        "bounds_mode": Field(str, default_value="dataset", is_required=False),
        "refreeze": Field(bool, default_value=False, is_required=False),
    }
)
def score_dataset_op(context: OpExecutionContext) -> Output:
    config = context.op_config
    if config["bounds_mode"] not in BOUNDS_MODES:
        raise Failure(description=f"bounds_mode must be one of {BOUNDS_MODES}")
    db_path = Paths.from_env().db_path
    try:
        # Restore-point guard: a flagship dbt build is mid-way or failed; wait for it.
        if has_restore_point(db_path):
            raise Failure(
                description="A flagship restore point exists; score datasets once the "
                "flagship build has finished or been restored."
            )
        result = score_dataset(
            db_path,
            config["dataset_id"],
            bounds_mode=config["bounds_mode"],
            refreeze=config["refreeze"],
            trigger="dagster",
        )
    except duckdb.IOException as error:
        # The scoring run is one transaction, so nothing was half-written.
        raise RetryRequested(
            max_retries=LOCK_RETRIES, seconds_to_wait=LOCK_RETRY_SECONDS
        ) from error
    except ScoringRefused as error:
        raise Failure(description=f"Refused: {error}") from error

    context.log.info(render(result))
    summary = result.as_dict()
    return Output(
        result.rules_version,
        metadata={
            "dataset_id": MetadataValue.text(result.dataset_id),
            "rules_version": MetadataValue.text(result.rules_version),
            "scores_written": MetadataValue.int(result.scores_written),
            "events_added": MetadataValue.int(result.events_added),
            "rows_rejected": MetadataValue.int(summary["silver"]["rows_rejected"]),
            "summary": MetadataValue.md(f"```\n{render(result)}\n```"),
        },
    )


@job(name="score_dataset", tags=DUCKDB_WRITER_TAGS, executor_def=in_process_executor)
def score_dataset_job() -> None:
    score_dataset_op()
