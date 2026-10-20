"""Review agent actions: python -m provenance.agent.approve --list | --approve | --reject.

--list                          proposed actions awaiting a decision (--all for every one)
--approve <id> --by <name>      approve, then apply (watchlist / review request)
--reject <id> --by <name> --reason <text>
--audit [N]                     last N audit-log entries and the hash-chain check
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import duckdb
from dotenv import load_dotenv

from provenance.agent.approval import (
    ApprovalError,
    approve,
    list_actions,
    read_audit_log,
    reject,
)
from provenance.agent.store import table_exists, verify_audit_log
from provenance.risk_engine.scorer import DEFAULT_DB_PATH


def _database_path() -> Path:
    return Path(os.environ.get("PROVENANCE_DUCKDB_PATH", DEFAULT_DB_PATH))


def _print_actions(db_path: Path, include_decided: bool) -> None:
    actions = list_actions(db_path, include_decided)
    if not actions:
        print("No actions." if include_decided else "No proposed actions awaiting a decision.")
        return
    print(f"{'action_id':<36}  {'status':<9}  {'action':<14}  {'supplier':<26}  quarter")
    for action in actions:
        quarter = action["quarter"].isoformat() if action["quarter"] else "-"
        print(
            f"{action['action_id']:<36}  {action['status']:<9}  {action['action_type']:<14}  "
            f"{action['supplier_key'] or '-':<26}  {quarter}"
        )


def _print_audit(db_path: Path, limit: int) -> None:
    for entry in read_audit_log(db_path, limit):
        print(
            f"#{entry['seq']:<4} {entry['ts'].isoformat(timespec='seconds')}  "
            f"{entry['actor']:<12} {entry['event']:<36} "
            f"{json.dumps(entry['detail'], sort_keys=True)}"
        )
    with duckdb.connect(str(db_path), read_only=True) as connection:
        if table_exists(connection, "agent", "audit_log"):
            broken = verify_audit_log(connection)
            print("Audit chain: intact" if not broken else f"Audit chain BROKEN at {broken}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0], formatter_class=argparse.RawTextHelpFormatter
    )
    command = parser.add_mutually_exclusive_group(required=True)
    command.add_argument("--list", action="store_true", help="list proposed actions")
    command.add_argument("--approve", metavar="ACTION_ID")
    command.add_argument("--reject", metavar="ACTION_ID")
    command.add_argument("--audit", nargs="?", const=20, type=int, metavar="N")
    parser.add_argument("--all", action="store_true", help="with --list: include decided")
    parser.add_argument("--by", help="name of the person deciding")
    parser.add_argument("--reason", help="reason (required for --reject)")
    parser.add_argument("--db-path", type=Path, default=None)
    args = parser.parse_args(argv)

    load_dotenv()
    db_path = args.db_path or _database_path()
    try:
        if args.list:
            _print_actions(db_path, args.all)
        elif args.audit is not None:
            _print_audit(db_path, args.audit)
        elif args.approve:
            if not args.by:
                parser.error("--approve needs --by <name>")
            result = approve(db_path, args.approve, args.by, args.reason)
            effect = result["effect"] or "no effect (no_action)"
            print(f"Approved and applied {result['action_id']}: {effect}")
        else:
            if not args.by or not args.reason:
                parser.error("--reject needs --by <name> and --reason <text>")
            action = reject(db_path, args.reject, args.by, args.reason)
            print(f"Rejected {action.action_id}; nothing was applied.")
    except ApprovalError as error:
        print(f"Refused: {error}", file=sys.stderr)
        return 2
    except duckdb.IOException as error:
        print(f"Warehouse is locked by another writer; try again later ({error}).", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
