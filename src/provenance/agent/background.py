"""Run one agent investigation in the background: python -m provenance.agent.background <run_id>.

The console's "Investigate with agent" button starts this module as a
subprocess; the API process never runs the model. It is the same investigation
as `python -m provenance.agent.run --event-id ...`: read-only tools, the
grounding check, then provenance.agent.run.record_outcome, which stores the
report and, only when it is grounded, a *proposed* action. Nothing is approved
or applied here: that still needs a named human in the Approvals screen.

Progress goes to the same status files as dataset runs (datasets.runs), with
kind "investigation", so GET /api/runs/{run_id} reports it and a running
investigation counts as a writer for the busy check.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import groq
from dotenv import load_dotenv

from provenance.agent.approval import requires_approval_setting
from provenance.agent.llm import LLMError
from provenance.agent.run import record_outcome, run_investigation
from provenance.datasets.onboard import PROJECT_ROOT
from provenance.datasets.runs import runs_dir, write_status

KIND = "investigation"
STAGES = ("investigating", "recording")


def llm_configured() -> bool:
    """Whether LLM_API_KEY is set (in the environment or .env). The key is never read out."""
    load_dotenv(PROJECT_ROOT / ".env")
    return bool(os.environ.get("LLM_API_KEY", "").strip())


def start(db_path: Path, data_root: Path, event_id: str) -> str:
    """Start the investigation subprocess; returns its run id."""
    run_id = str(uuid.uuid4())
    write_status(
        data_root,
        run_id,
        kind=KIND,
        event_id=event_id,
        status="queued",
        stage=None,
        started_at=datetime.now(UTC).isoformat(timespec="seconds"),
        pid=None,
    )
    log = runs_dir(data_root) / f"{run_id}.log"
    with log.open("ab") as handle:
        process = subprocess.Popen(  # noqa: S603 - fixed module, generated arguments
            [
                sys.executable,
                "-m",
                "provenance.agent.background",
                run_id,
                "--event-id",
                event_id,
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
    if isinstance(error, LLMError):
        # Our own messages (missing key, rate limit, bad response); never the key itself.
        return f"The model could not complete the investigation: {str(error)[:200]}"
    if isinstance(error, groq.AuthenticationError):
        return "The model service refused LLM_API_KEY; check it in .env. Nothing was recorded."
    if isinstance(error, groq.GroqError):
        # The type only: an SDK message is not ours to pass on.
        return f"The model could not be reached ({type(error).__name__}); nothing was recorded."
    if isinstance(error, duckdb.IOException):
        return (
            "The warehouse was busy (another writer held it); nothing was recorded. "
            "Try again when it finishes."
        )
    return f"The investigation failed unexpectedly ({type(error).__name__}); nothing was recorded."


def _default_llm() -> Any:
    from provenance.agent.llm import GroqLLM

    return GroqLLM.from_env()


def execute(
    run_id: str,
    event_id: str,
    db_path: Path,
    data_root: Path,
    llm_factory: Callable[[], Any] = _default_llm,
) -> int:
    """The subprocess body: investigate, record (propose only), report the outcome."""
    write_status(
        data_root,
        run_id,
        kind=KIND,
        event_id=event_id,
        status="running",
        stage="investigating",
        pid=os.getpid(),
    )
    try:
        llm = llm_factory()
        result, grounding = run_investigation(event_id, db_path, llm)
        write_status(data_root, run_id, stage="recording")
        recorded = record_outcome(
            db_path, result, grounding, llm.model, requires_approval_setting()
        )
    except Exception as error:  # noqa: BLE001 - every failure is reported, plainly
        message = _plain_error(error)
        write_status(data_root, run_id, status="failed", error=message)
        print(f"investigation {run_id} failed: {message}", file=sys.stderr)
        return 1
    write_status(
        data_root,
        run_id,
        status="succeeded",
        stage="recording",
        investigation_id=recorded.investigation_id,
        investigation_status=recorded.status,
        grounded=grounding.grounded,
        action_id=recorded.action_id,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run_id")
    parser.add_argument("--event-id", required=True)
    parser.add_argument("--db-path", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    args = parser.parse_args(argv)
    load_dotenv(PROJECT_ROOT / ".env")
    return execute(args.run_id, args.event_id, args.db_path, args.data_root)


if __name__ == "__main__":
    sys.exit(main())
