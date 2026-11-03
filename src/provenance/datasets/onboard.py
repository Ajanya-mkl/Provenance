"""Onboard any CSV: pre-checks -> profile -> generated suite -> gate -> bronze.

    python -m provenance.datasets.onboard path\\to\\file.csv --name "My data"
    python -m provenance.datasets.onboard --list

Nothing is written until the file passes the pre-checks. The same file (by
SHA-256) is registered once; a second upload is reported as already registered.
A failed gate moves the batch to data/quarantine/datasets/<id>/, records a
dq_failure event and an audit-log entry, and leaves no bronze table.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from provenance.datasets import bronze, registry
from provenance.datasets.gen_suite import (
    GateOutcome,
    Rule,
    build_rules,
    describe_rules,
    report,
    run_rules,
)
from provenance.datasets.identifiers import dataset_id_from_sha, schema_for
from provenance.datasets.profile import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_COLUMNS,
    DatasetProfile,
    DatasetRejected,
    profile_csv,
    read_csv_safely,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class Paths:
    db_path: Path
    data_root: Path
    quarantine_root: Path

    @classmethod
    def from_env(cls) -> Paths:
        return cls(
            db_path=Path(
                os.environ.get(
                    "PROVENANCE_DUCKDB_PATH", PROJECT_ROOT / "data" / "provenance.duckdb"
                )
            ),
            data_root=Path(
                os.environ.get("PROVENANCE_DATASETS_ROOT", PROJECT_ROOT / "data" / "datasets")
            ),
            quarantine_root=Path(
                os.environ.get(
                    "PROVENANCE_DATASETS_QUARANTINE_ROOT",
                    PROJECT_ROOT / "data" / "quarantine" / "datasets",
                )
            ),
        )


@dataclass
class OnboardResult:
    status: str  # rejected | already_registered | gated | quarantined | failed
    message: str
    dataset_id: str | None = None
    schema_name: str | None = None
    profile: DatasetProfile | None = None
    rules: list[Rule] = field(default_factory=list)
    outcome: GateOutcome | None = None
    problems: list[str] = field(default_factory=list)
    rows_loaded: int = 0


def onboard(
    csv_path: str | Path,
    name: str,
    *,
    paths: Paths,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_columns: int = DEFAULT_MAX_COLUMNS,
    log: Callable[[str], None] = lambda _message: None,
) -> OnboardResult:
    # 1. Pre-checks: nothing is written for a rejected file.
    try:
        name = registry.clean_name(name)
        csv_file = read_csv_safely(csv_path, max_bytes=max_bytes, max_columns=max_columns)
    except DatasetRejected as error:
        return OnboardResult("rejected", "The file was not onboarded.", problems=error.problems)
    except ValueError as error:
        return OnboardResult("rejected", "The file was not onboarded.", problems=[str(error)])
    log(f"pre-checks passed: {csv_file.encoding}, delimiter {csv_file.delimiter!r}")

    existing = registry.find_by_hash(paths.db_path, csv_file.file_sha256)
    if existing and existing["status"] in registry.FINAL_STATUSES:
        return OnboardResult(
            "already_registered",
            f"This file is already registered as {existing['name']!r} "
            f"(dataset {existing['dataset_id']}, status {existing['status']}); nothing changed.",
            dataset_id=existing["dataset_id"],
            schema_name=existing["schema_name"],
        )

    dataset_id = dataset_id_from_sha(csv_file.file_sha256)
    schema = schema_for(dataset_id)
    batch_id = csv_file.file_sha256

    # 2-3. Profile and generate the suite (pure).
    profile = profile_csv(csv_file)
    rules = build_rules(profile)
    log(f"profiled {profile.row_count} rows x {profile.column_count} columns; {len(rules)} rules")

    with registry.writer(paths.db_path) as connection:
        registry.register(
            connection,
            dataset_id=dataset_id,
            name=name,
            source_filename=csv_file.source_filename,
            file_sha256=csv_file.file_sha256,
            schema_name=schema,
        )
    try:
        batch_dir = bronze.stage_batch(
            csv_file.frame,
            dataset_id=dataset_id,
            batch_id=batch_id,
            source_filename=csv_file.source_filename,
            data_root=paths.data_root,
        )
        with registry.writer(paths.db_path) as connection:
            registry.record_profile(connection, dataset_id, profile, rules)

        # 4. Gate.
        outcome = run_rules(rules, csv_file.frame, profile, suite_name=f"ds_{dataset_id}")
        gate_report = {"report": report(outcome), **outcome.as_dict()}
        summary = {
            "rules": len(outcome.results),
            "hard_failures": [r.rule.rule_id for r in outcome.hard_failures],
            "warnings": [r.rule.rule_id for r in outcome.warnings],
        }
        log(report(outcome).splitlines()[0])

        # 5. Bronze, or quarantine.
        if outcome.passed:
            with registry.writer(paths.db_path) as connection:
                rows = bronze.load_bronze(
                    connection,
                    dataset_id=dataset_id,
                    batch_dir=batch_dir,
                    expected_columns=profile.column_names,
                )
                registry.set_status(connection, dataset_id, "gated", gate_report=gate_report)
                bronze.record_success_audit(
                    connection, dataset_id=dataset_id, batch_id=batch_id, rows=rows,
                    summary=summary,
                )  # fmt: skip
            return OnboardResult(
                "gated",
                f"Loaded {rows} rows into {schema}.bronze_raw.",
                dataset_id=dataset_id,
                schema_name=schema,
                profile=profile,
                rules=rules,
                outcome=outcome,
                rows_loaded=rows,
            )

        bronze.quarantine_batch(
            batch_dir,
            dataset_id=dataset_id,
            batch_id=batch_id,
            quarantine_root=paths.quarantine_root,
            report=gate_report,
        )
        with registry.writer(paths.db_path) as connection:
            registry.set_status(
                connection,
                dataset_id,
                "quarantined",
                reason=f"{len(outcome.hard_failures)} hard rule(s) failed",
                gate_report=gate_report,
            )
            bronze.record_failure_event(
                connection, dataset_id=dataset_id, batch_id=batch_id, name=name, summary=summary
            )
        return OnboardResult(
            "quarantined",
            f"Quarantined: {len(outcome.hard_failures)} hard rule(s) failed. "
            f"The batch was moved to {paths.quarantine_root / dataset_id}.",
            dataset_id=dataset_id,
            schema_name=schema,
            profile=profile,
            rules=rules,
            outcome=outcome,
        )
    except Exception as error:
        with registry.writer(paths.db_path) as connection:
            registry.set_status(
                connection, dataset_id, "failed", reason=f"{type(error).__name__}: {error}"[:300]
            )
        raise


def render(result: OnboardResult) -> str:
    lines = [f"Status: {result.status.upper()}. {result.message}"]
    for problem in result.problems:
        lines.append(f"  - {problem}")
    profile = result.profile
    if profile:
        lines += [
            "",
            f"Profile: {profile.row_count} rows, {profile.column_count} columns, "
            f"{profile.encoding}, delimiter {profile.delimiter!r}, dataset {result.dataset_id}",
        ]
        for column in profile.columns:
            flags = [
                label
                for label, on in (
                    ("candidate key", column.is_candidate_key),
                    (f"date {column.date_format}", column.looks_like_date),
                    ("MIXED", column.is_mixed_type),
                    ("MOSTLY EMPTY", column.is_mostly_null),
                )
                if on
            ]
            span = (
                f" range {column.min_value}..{column.max_value}"
                if column.min_value is not None
                else ""
            )
            lines.append(
                f"  {column.column_name:<22} {column.inferred_type:<6} nulls {column.null_pct:>5}%"
                f" distinct {column.distinct_count:>4}{span}"
                + (f"  [{', '.join(flags)}]" if flags else "")
            )
        for warning in profile.warnings:
            lines.append(f"  ! {warning}")
    if result.rules:
        lines += ["", "Generated rules:"]
        lines += [f"  {line}" for line in describe_rules(result.rules)]
    if result.outcome:
        lines += ["", report(result.outcome)]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("csv_path", nargs="?", help="CSV file to onboard")
    parser.add_argument("--name", help="display name for the dataset")
    parser.add_argument("--list", action="store_true", help="list registered datasets")
    parser.add_argument("--db-path", type=Path, default=None)
    parser.add_argument("--data-root", type=Path, default=None)
    parser.add_argument("--quarantine-root", type=Path, default=None)
    parser.add_argument("--max-mb", type=float, default=DEFAULT_MAX_BYTES / 1_048_576)
    parser.add_argument("--max-columns", type=int, default=DEFAULT_MAX_COLUMNS)
    args = parser.parse_args(argv)

    defaults = Paths.from_env()
    paths = Paths(
        db_path=args.db_path or defaults.db_path,
        data_root=args.data_root or defaults.data_root,
        quarantine_root=args.quarantine_root or defaults.quarantine_root,
    )
    if args.list:
        datasets = registry.list_datasets(paths.db_path)
        if not datasets:
            print("No datasets registered.")
        for item in datasets:
            print(
                f"{item['dataset_id']}  {item['status']:<11} {item['row_count'] or 0:>7} rows  "
                f"{item['column_count'] or 0:>3} cols  {item['schema_name']}  {item['name']}"
            )
        return 0
    if not args.csv_path or not args.name:
        parser.error("give a CSV path and --name, or --list")
    result = onboard(
        args.csv_path,
        args.name,
        paths=paths,
        max_bytes=int(args.max_mb * 1_048_576),
        max_columns=args.max_columns,
    )
    print(render(result))
    return {"gated": 0, "already_registered": 0, "quarantined": 1}.get(result.status, 2)


if __name__ == "__main__":
    sys.exit(main())
