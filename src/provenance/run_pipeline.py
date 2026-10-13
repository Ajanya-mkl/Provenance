"""Run provenance_full_pipeline in-process and print a step summary.

With DAGSTER_HOME set, the run is recorded in that instance (so it shows in the
Dagster UI) and the runner refuses to start while another DuckDB writer run is
in progress. Without it, Dagster uses a temporary instance.
"""

from __future__ import annotations

import os
from collections import OrderedDict
from typing import Any

from dagster import DagsterEvent, DagsterInstance, DagsterRunStatus, RunsFilter

JOB_NAME = "provenance_full_pipeline"
WRITER_TAG = ("dagster/concurrency_key", "duckdb_writer")
ACTIVE_STATUSES = [
    DagsterRunStatus.QUEUED,
    DagsterRunStatus.NOT_STARTED,
    DagsterRunStatus.STARTING,
    DagsterRunStatus.STARTED,
    DagsterRunStatus.CANCELING,
]
STATE_LABELS = {
    "STEP_START": "started",
    "STEP_SUCCESS": "succeeded",
    "STEP_FAILURE": "failed",
    "STEP_SKIPPED": "skipped",
}


def _instance() -> DagsterInstance | None:
    return DagsterInstance.get() if os.environ.get("DAGSTER_HOME") else None


def _active_writer_runs(instance: DagsterInstance) -> list[str]:
    runs = instance.get_runs(
        filters=RunsFilter(statuses=ACTIVE_STATUSES, tags=dict([WRITER_TAG]))
    )
    return [run.run_id for run in runs]


def _step_states(events: list[Any]) -> OrderedDict[str, str]:
    states: OrderedDict[str, str] = OrderedDict()
    for event in events:
        if (
            isinstance(event, DagsterEvent)
            and event.step_key
            and event.event_type_value in STATE_LABELS
        ):
            states[event.step_key] = STATE_LABELS[event.event_type_value]
    return states


def main() -> int:
    from provenance.dags import definitions

    instance = _instance()
    if instance is not None:
        active = _active_writer_runs(instance)
        if active:
            print(f"Refusing to start: DuckDB writer run(s) in progress: {', '.join(active)}")
            return 2

    print(f"Running {JOB_NAME}")
    job = definitions.defs.resolve_job_def(JOB_NAME)
    if instance is None:
        result = job.execute_in_process(raise_on_error=False)
    else:
        result = job.execute_in_process(raise_on_error=False, instance=instance)

    print("Step summary:")
    for step_key, state in _step_states(result.all_events).items():
        print(f"  {step_key}: {state}")
    print(f"Pipeline {'SUCCEEDED' if result.success else 'FAILED'}")
    return 0 if result.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
