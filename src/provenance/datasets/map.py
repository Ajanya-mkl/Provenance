"""Propose, review and decide a dataset's column mapping.

    python -m provenance.datasets.map <dataset_id> [--heuristic-only] [--sample-values]
    python -m provenance.datasets.map <dataset_id> --show
    python -m provenance.datasets.map --edit <mapping_id> --set outcome.late_when=false --by NAME
    python -m provenance.datasets.map --approve <mapping_id> --by NAME [--reason TEXT]
    python -m provenance.datasets.map --reject <mapping_id> --by NAME --reason TEXT

The agent sees column names, types and statistics, never text values (unless
--sample-values). Its proposal is validated deterministically; a failed retry
falls back to the heuristic mapper.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import duckdb
from dotenv import load_dotenv

from provenance.datasets import mapping_store
from provenance.datasets.mapping_agent import propose_mapping
from provenance.datasets.onboard import Paths
from provenance.datasets.validator import MappingInvalid, load_context


def render_mapping(record: dict[str, Any]) -> str:
    mapping = record["proposal"]
    lines = [
        f"Mapping {record['mapping_id']} (version {record['version']}, source "
        f"{record['source']}, status {record['status']})",
        f"  confidence {mapping.get('confidence')}"
        + (
            f"; tokens {record['prompt_tokens']}+{record['completion_tokens']}"
            if record.get("prompt_tokens")
            else ""
        ),
    ]
    for field, value in mapping_store.required_fields_summary(mapping).items():
        lines.append(f"  {field:<11} -> {value if value is not None else '(not mapped)'}")
    evidence = mapping.get("evidence") or {}
    for field, facts in evidence.items():
        lines.append(f"  evidence {field}: {'; '.join(str(fact) for fact in facts)}")
    if mapping.get("notes"):
        lines.append(f"  notes: {mapping['notes']}")
    validation = record["validation"]
    if validation.get("fallback_reason"):
        lines.append(f"  fell back to the heuristic: {validation['fallback_reason']}")
    for errors in validation.get("agent_attempt_errors") or []:
        lines.append(f"  agent attempt rejected: {'; '.join(errors)}")
    reasons = record["reasons"]
    lines.append(f"  mappable:  {'YES' if record['mappable'] else 'NO'} - {reasons['mappable']}")
    lines.append(f"  scoreable: {'YES' if record['scoreable'] else 'NO'} - {reasons['scoreable']}")
    if record.get("decided_by"):
        lines.append(f"  decided by {record['decided_by']}: {record.get('reason') or ''}")
    return "\n".join(lines)


def _llm_factory():
    from provenance.agent.llm import GroqLLM

    return GroqLLM.from_env()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("dataset_id", nargs="?")
    parser.add_argument("--heuristic-only", action="store_true")
    parser.add_argument("--sample-values", action="store_true", help="let the agent see 5 values")
    parser.add_argument("--show", action="store_true", help="list this dataset's mappings")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--approve", metavar="MAPPING_ID")
    action.add_argument("--reject", metavar="MAPPING_ID")
    action.add_argument("--edit", metavar="MAPPING_ID")
    parser.add_argument("--set", action="append", default=[], metavar="FIELD=VALUE")
    parser.add_argument("--by")
    parser.add_argument("--reason")
    parser.add_argument("--db-path", type=Path, default=None)
    args = parser.parse_args(argv)
    load_dotenv()
    db_path = args.db_path or Paths.from_env().db_path

    try:
        if args.approve:
            print(mapping_store.approve(db_path, args.approve, by=args.by, reason=args.reason))
            return 0
        if args.reject:
            print(mapping_store.reject(db_path, args.reject, by=args.by, reason=args.reason))
            return 0
        if args.edit:
            if not args.set:
                parser.error("--edit needs at least one --set FIELD=VALUE")
            new_id, _ = mapping_store.edit(
                db_path, args.edit, mapping_store.parse_sets(args.set), by=args.by
            )
            print(render_mapping(mapping_store.get_mapping(db_path, new_id)))
            return 0
        if not args.dataset_id:
            parser.error("give a dataset id, or --approve/--reject/--edit")
        if args.show:
            for record in mapping_store.list_mappings(db_path, args.dataset_id):
                print(render_mapping(record))
            return 0
        context = load_context(db_path, args.dataset_id)
        proposal = propose_mapping(
            context,
            llm_factory=None if args.heuristic_only else _llm_factory,
            heuristic_only=args.heuristic_only,
            sample_values=args.sample_values,
        )
        mapping_id = mapping_store.record_proposal(
            db_path, args.dataset_id, proposal, created_by=proposal.source
        )
        print(render_mapping(mapping_store.get_mapping(db_path, mapping_id)))
        return 0
    except MappingInvalid as error:
        print("Refused: the edited mapping is not valid:", file=sys.stderr)
        for problem in error.errors:
            print(f"  - {problem}", file=sys.stderr)
        return 2
    except (mapping_store.DecisionError, LookupError) as error:
        print(f"Refused: {error}", file=sys.stderr)
        return 2
    except duckdb.IOException:
        print("The warehouse is locked by another writer; try again shortly.", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
