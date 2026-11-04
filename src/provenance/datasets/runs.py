"""Background scoring runs for the console: python -m provenance.datasets.runs <run_id>.

The API never scores inside its own process. It records a 'queued' row in
meta.runs (through registry.writer) and starts this module as a subprocess,
which scores the dataset through datasets.score.

DuckDB is single-writer: while the subprocess holds the warehouse nobody can
read it, not even the API. So the live stage lives in a small JSON status file
(data/datasets/runs/<run_id>.json) that the API can always read; the meta.runs
row gets its final state from score_dataset itself (succeeded, in the same
transaction as the scores) or from this runner afterwards (failed, with a
plain-language error). Only one writer runs at a time: `busy_reason` says why
not, and the API answers 409 'busy'.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from provenance.datasets import registry
from provenance.datasets.onboard import PROJECT_ROOT
from provenance.datasets.score import ScoringRefused, score_dataset
from provenance.db import connect_read_only

STAGES = registry.RUN_STAGES
FINAL = ("succeeded", "failed")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def runs_dir(data_root: Path) -> Path:
    return Path(data_root) / "runs"


def status_path(data_root: Path, run_id: str) -> Path:
    uuid.UUID(run_id)  # run ids are generated UUIDs; anything else is refused
    return runs_dir(data_root) / f"{run_id}.json"


def write_status(data_root: Path, run_id: str, **fields: Any) -> dict[str, Any]:
    path = status_path(data_root, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    current = read_status(data_root, run_id) or {}
    current.update(fields, updated_at=_now())
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(current, default=str), encoding="utf-8")
    os.replace(tmp, path)  # atomic, so a reader never sees half a file
    return current


def read_status(data_root: Path, run_id: str) -> dict[str, Any] | None:
    try:
        return json.loads(status_path(data_root, run_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def pid_alive(pid: int | None) -> bool:
    """Whether a process is still running (Windows-safe: os.kill(pid, 0) would kill it)."""
    if not pid:
        return False
    if os.name == "nt":
        import ctypes

        process_query_limited_information, still_active = 0x1000, 259
        handle = ctypes.windll.kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            return code.value == still_active
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def stage_view(status: str, stage: str | None, error: str | None = None) -> list[dict[str, str]]:
    """Each stage as pending | running | done | failed."""
    reached = STAGES.index(stage) if stage in STAGES else -1
    view = []
    for index, name in enumerate(STAGES):
        if status == "succeeded" or index < reached:
            state = "done"
        elif index == reached:
            state = "failed" if status == "failed" else "running"
        else:
            state = "pending"
        view.append({"name": name, "status": state})
    if status == "failed" and reached < 0:
        view[0]["status"] = "failed"
    return view


def run_state(db_path: Path, data_root: Path, run_id: str) -> dict[str, Any] | None:
    """The run as the console shows it: status file first, meta.runs when readable."""
    live = read_status(data_root, run_id)
    row = None
    try:
        row = registry.get_run(db_path, run_id)
    except duckdb.IOException:
        row = None  # a run holds the writer; the status file is the live truth
    if live is None and row is None:
        return None
    state: dict[str, Any] = {"run_id": run_id}
    if row:
        state.update(
            dataset_id=row["dataset_id"],
            status=row["status"],
            stage=row["stage"],
            error=row["error"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
        )
    if live and (not row or row["status"] not in FINAL):
        state.update({k: live.get(k) for k in ("dataset_id", "status", "stage", "error")})
        state.setdefault("started_at", live.get("started_at"))
        if state["status"] not in FINAL and not pid_alive(live.get("pid")):
            state["status"] = "failed"
            state["error"] = state.get("error") or (
                "The scoring process stopped unexpectedly; nothing was changed. Run it again."
            )
    state["stages"] = stage_view(state["status"], state.get("stage"), state.get("error"))
    return state


def busy_reason(db_path: Path, data_root: Path) -> str | None:
    """Why a new writer run must not start now, or None."""
    for status_file in runs_dir(data_root).glob("*.json") if runs_dir(data_root).exists() else []:
        try:
            live = json.loads(status_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if live.get("status") not in FINAL and pid_alive(live.get("pid")):
            return f"scoring run {status_file.stem} is still running"
    from provenance.agent.run import writer_activity

    return writer_activity(db_path)


def start_run(db_path: Path, data_root: Path, dataset_id: str) -> str:
    """Record a queued run and start the scoring subprocess; returns the run id."""
    run_id = str(uuid.uuid4())
    with registry.writer(db_path) as connection:
        registry.create_run(connection, run_id, dataset_id, trigger="api")
    write_status(data_root, run_id, dataset_id=dataset_id, status="queued", stage=None,
                 started_at=_now(), pid=None)  # fmt: skip
    log = runs_dir(data_root) / f"{run_id}.log"
    with log.open("ab") as handle:
        process = subprocess.Popen(  # noqa: S603 - fixed module, generated arguments
            [
                sys.executable,
                "-m",
                "provenance.datasets.runs",
                run_id,
                "--dataset-id",
                dataset_id,
                "--db-path",
                str(db_path),
                "--data-root",
                str(data_root),
            ],
            cwd=PROJECT_ROOT,
            stdout=handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    write_status(data_root, run_id, pid=process.pid)
    return run_id


def _plain_error(error: BaseException) -> str:
    if isinstance(error, ScoringRefused):
        return str(error)
    if isinstance(error, duckdb.IOException):
        return (
            "The warehouse was busy (another writer held it); nothing was changed. "
            "Run it again when that finishes."
        )
    return f"Scoring failed unexpectedly ({type(error).__name__}); nothing was changed."


def execute(run_id: str, dataset_id: str, db_path: Path, data_root: Path) -> int:
    """The subprocess body: score, report stages, record a failure."""
    write_status(data_root, run_id, status="running", stage=None, pid=os.getpid())

    def progress(stage: str) -> None:
        write_status(data_root, run_id, status="running", stage=stage)

    try:
        score_dataset(db_path, dataset_id, progress=progress, run_id=run_id, trigger="api")
    except Exception as error:  # noqa: BLE001 - every failure is reported, plainly
        message = _plain_error(error)
        stage = (read_status(data_root, run_id) or {}).get("stage")
        write_status(data_root, run_id, status="failed", error=message)
        try:
            with registry.writer(db_path) as connection:
                registry.finish_run(connection, run_id, status="failed", stage=stage, error=message)
        except duckdb.Error:
            pass  # the status file still carries the failure
        print(f"run {run_id} failed: {message}", file=sys.stderr)
        return 1
    write_status(data_root, run_id, status="succeeded", stage="flags")
    return 0


def latest_summary(db_path: Path, dataset_id: str) -> dict[str, Any] | None:
    with connect_read_only(db_path) as connection:
        run = registry.latest_run(connection, dataset_id, succeeded=True)
    return run["summary"] if run else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run_id")
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--db-path", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    args = parser.parse_args(argv)
    return execute(args.run_id, args.dataset_id, args.db_path, args.data_root)


if __name__ == "__main__":
    sys.exit(main())
