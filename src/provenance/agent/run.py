"""Investigate one risk flag: python -m provenance.agent.run --event-id <id> | --next.

The agent only proposes. A grounded report creates one 'proposed' row in
agent.actions; nothing takes effect until a person approves it with
python -m provenance.agent.approve. An ungrounded report is stored as
'rejected_ungrounded' and creates no action. All writes go through
provenance.agent.store.writer().
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
from dotenv import load_dotenv

from provenance.agent.approval import AGENT_ACTOR, propose_action, requires_approval_setting
from provenance.agent.grounding import GroundingResult, check_grounding
from provenance.agent.llm import GroqLLM
from provenance.agent.loop import MAX_STEPS, InvestigationResult, investigate
from provenance.agent.store import append_audit, now, table_exists, writer
from provenance.agent.tools.investigation_tools import InvestigationTools
from provenance.risk_engine.scorer import DEFAULT_DB_PATH

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class RecordedInvestigation:
    investigation_id: str
    status: str
    action_id: str | None


def _database_path() -> Path:
    return Path(os.environ.get("PROVENANCE_DUCKDB_PATH", DEFAULT_DB_PATH))


def next_queued_event(db_path: str | Path) -> str | None:
    """Oldest queued risk_flag that has no proposed investigation yet."""
    with duckdb.connect(str(db_path), read_only=True) as connection:
        done_filter = (
            "AND event_id NOT IN (SELECT event_id FROM agent.investigations "
            "WHERE status = 'proposed')"
            if table_exists(connection, "agent", "investigations")
            else ""
        )
        row = connection.execute(
            f"""
            SELECT event_id FROM agent.events
            WHERE event_type = 'risk_flag' AND investigation_status = 'queued'
            {done_filter}
            ORDER BY created_at, event_id
            LIMIT 1
            """
        ).fetchone()
    return row[0] if row else None


def status_for(result: InvestigationResult, grounding: GroundingResult) -> str:
    if result.report is None:
        return "no_report"
    return "proposed" if grounding.grounded else "rejected_ungrounded"


def record_outcome(
    db_path: str | Path,
    result: InvestigationResult,
    grounding: GroundingResult,
    model: str,
    requires_approval: bool,
) -> RecordedInvestigation:
    """Store the investigation and, only when grounded, its proposed action."""
    investigation_id = str(uuid.uuid4())
    status = status_for(result, grounding)
    action_id = None
    with writer(db_path) as connection:
        connection.execute(
            """
            INSERT INTO agent.investigations
                (investigation_id, event_id, report, status, grounded, grounded_ratio,
                 ungrounded_numbers, tool_calls, steps, prompt_tokens, completion_tokens,
                 total_tokens, model, requires_approval, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                investigation_id,
                result.event_id,
                json.dumps(result.report) if result.report is not None else None,
                status,
                grounding.grounded,
                grounding.grounded_ratio,
                json.dumps(grounding.ungrounded_numbers),
                json.dumps(result.tool_calls),
                result.steps,
                result.prompt_tokens,
                result.completion_tokens,
                result.prompt_tokens + result.completion_tokens,
                model,
                requires_approval,
                now(),
            ],
        )
        append_audit(
            connection,
            AGENT_ACTOR,
            f"investigation_{status}",
            {
                "investigation_id": investigation_id,
                "event_id": result.event_id,
                "grounded_ratio": grounding.grounded_ratio,
                "ungrounded_numbers": grounding.ungrounded_numbers,
                "model": model,
            },
        )
        if status == "proposed":
            action_id = propose_action(
                connection, investigation_id, result.event_id, result.report["proposed_action"]
            )
        else:
            LOGGER.warning(
                "Investigation %s of %s is %s; no action created.",
                investigation_id,
                result.event_id,
                status,
            )
    return RecordedInvestigation(investigation_id, status, action_id)


def record_investigation(
    db_path: str | Path,
    result: InvestigationResult,
    grounding: GroundingResult,
    model: str,
    requires_approval: bool,
) -> str:
    return record_outcome(db_path, result, grounding, model, requires_approval).investigation_id


def run_investigation(
    event_id: str,
    db_path: str | Path,
    llm: Any,
    max_steps: int = MAX_STEPS,
) -> tuple[InvestigationResult, GroundingResult]:
    # The read-only connection must close before the writer opens: DuckDB will
    # not hold both configurations on one file in one process.
    with InvestigationTools(db_path) as toolbox:
        result = investigate(event_id, llm, toolbox, max_steps=max_steps)
    if result.report is None:
        grounding = GroundingResult(False, 0.0, 0)
    else:
        grounding = check_grounding(result.report, result.tool_calls)
    return result, grounding


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--event-id", help="agent.events event_id to investigate")
    target.add_argument("--next", action="store_true", help="take the oldest queued risk_flag")
    parser.add_argument("--db-path", type=Path, default=None)
    parser.add_argument("--max-steps", type=int, default=MAX_STEPS)
    args = parser.parse_args(argv)

    load_dotenv()
    db_path = args.db_path or _database_path()
    event_id = args.event_id or next_queued_event(db_path)
    if event_id is None:
        print("No queued risk_flag events without a proposed investigation.")
        return 0

    llm = GroqLLM.from_env()
    result, grounding = run_investigation(event_id, db_path, llm, args.max_steps)
    requires_approval = requires_approval_setting()
    recorded = record_outcome(db_path, result, grounding, llm.model, requires_approval)

    print(f"Event:           {event_id}")
    print(f"Model:           {llm.model}")
    print("Report:")
    print(json.dumps(result.report, indent=2) if result.report else "  (no report)")
    print("Grounding:")
    print(json.dumps(grounding.as_dict(), indent=2))
    print("Tool calls:")
    for call in result.tool_calls:
        print(f"  step {call['step']}: {call['name']}({call['arguments']})")
    print(f"Steps used:      {result.steps}/{args.max_steps} ({result.stop_reason})")
    print(
        f"Tokens:          prompt={result.prompt_tokens} "
        f"completion={result.completion_tokens} "
        f"total={result.prompt_tokens + result.completion_tokens}"
    )
    print(f"Investigation:   {recorded.investigation_id} ({recorded.status})")
    if recorded.action_id:
        print(f"Proposed action: {recorded.action_id} (awaiting human approval)")
        print(
            "  approve: python -m provenance.agent.approve "
            f"--approve {recorded.action_id} --by <name>"
        )
    else:
        print("Proposed action: none (report not accepted)")
    return 0 if recorded.status == "proposed" else 1


if __name__ == "__main__":
    sys.exit(main())
