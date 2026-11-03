"""Phase 10a: dataset onboarding. Every test uses a temporary database and folders."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import duckdb
import pytest
from dagster import RetryRequested, build_op_context

from provenance.agent.store import verify_audit_log
from provenance.datasets import onboard as onboard_module
from provenance.datasets.gen_suite import build_rules
from provenance.datasets.identifiers import UnsafeIdentifier, quote_column, quote_schema
from provenance.datasets.onboard import Paths, onboard
from provenance.datasets.profile import DatasetRejected, profile_csv, read_csv_safely

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "datasets"
CLEAN = FIXTURES / "shipments_clean.csv"
DIRTY = FIXTURES / "shipments_dirty.csv"
MARKS = FIXTURES / "student_marks.csv"


@pytest.fixture
def paths(tmp_path) -> Paths:
    return Paths(
        db_path=tmp_path / "warehouse.duckdb",
        data_root=tmp_path / "datasets",
        quarantine_root=tmp_path / "quarantine" / "datasets",
    )


def _query(paths: Paths, sql: str, params=None):
    with duckdb.connect(str(paths.db_path), read_only=True) as connection:
        return connection.execute(sql, params or []).fetchall()


def test_clean_csv_passes_and_lands_in_its_own_bronze_schema(paths):
    result = onboard(CLEAN, "Clean shipments", paths=paths)

    assert result.status == "gated", result.message
    assert result.outcome.passed and not result.outcome.warnings
    schema = result.schema_name
    assert schema == f"ds_{result.dataset_id}"
    rows = _query(paths, f'SELECT count(*), count(DISTINCT _batch_id) FROM "{schema}".bronze_raw')
    assert rows == [(200, 1)]
    types = _query(
        paths,
        "SELECT DISTINCT data_type FROM information_schema.columns "
        "WHERE table_schema = ? AND table_name = 'bronze_raw' AND column_name NOT LIKE '\\_%' "
        "ESCAPE '\\'",
        [schema],
    )
    assert types == [("VARCHAR",)]  # lossless text; inferred types live in meta
    meta = dict(
        _query(
            paths,
            "SELECT column_name, inferred_type FROM meta.dataset_columns WHERE dataset_id = ?",
            [result.dataset_id],
        )
    )
    assert meta["order_date"] == "date" and meta["quantity"] == "int"
    assert meta["unit_price"] == "float" and meta["on_time"] == "bool"
    status = _query(paths, "SELECT status, row_count, column_count FROM meta.datasets")
    assert status == [("gated", 200, 9)]
    assert list((paths.data_root / result.dataset_id).glob("batch_id=*/part-0.parquet"))


def test_dirty_csv_is_quarantined_with_a_readable_report(paths):
    result = onboard(DIRTY, "Dirty shipments", paths=paths)

    assert result.status == "quarantined"
    failed = {r.rule.check: r for r in result.outcome.hard_failures}
    assert failed["values are unique"].unexpected_count == 6
    assert failed["dates in format %Y-%m-%d"].unexpected_count == 3
    assert [r.rule.column for r in result.outcome.results if not r.success] == [
        "shipment_id",
        "order_date",
    ]
    text = onboard_module.render(result)
    assert "QUARANTINED" in text and "FAIL R03 [hard] shipment_id: values are unique" in text
    assert "why: 'shipment_id' is the row identifier" in text
    assert "MIXED" in text  # quantity holds 'twelve' and '??'

    # Moved, never copied; no bronze table; event and audit entry recorded.
    assert not (paths.data_root / result.dataset_id).exists()
    moved = paths.quarantine_root / result.dataset_id
    assert list(moved.glob("batch_id=*/part-0.parquet"))
    report = json.loads(next(moved.glob("batch_id=*/gate_report.json")).read_text("utf-8"))
    assert report["passed"] is False and "QUARANTINED" in report["report"]
    assert _query(paths, "SELECT status FROM meta.datasets") == [("quarantined",)]
    assert _query(
        paths, "SELECT count(*) FROM information_schema.tables WHERE table_schema = ?",
        [result.schema_name],
    ) == [(0,)]  # fmt: skip
    events = _query(paths, "SELECT event_type, source FROM agent.events")
    assert events == [("dq_failure", f"dataset:{result.dataset_id}")]
    with duckdb.connect(str(paths.db_path), read_only=True) as connection:
        assert verify_audit_log(connection) == []
        assert connection.execute("SELECT event FROM agent.audit_log").fetchall() == [
            ("dataset_quarantined",)
        ]


def test_non_supply_chain_file_gets_sensible_rules(paths):
    result = onboard(MARKS, "Student marks", paths=paths)

    assert result.status == "gated"
    checks = {(rule.column, rule.check) for rule in result.rules}
    # A student appears once per subject, so student_id is not unique: no key rule.
    assert not any(check == "values are unique" for _, check in checks)
    assert ("exam_date", "dates in format %d/%m/%Y") in checks
    assert ("passed", "values are true/false values") in checks
    # Identifiers are labels: no numeric range rule for student_id; marks get one.
    ranges = {column for column, check in checks if check.startswith("values between")}
    assert ranges == {"mark"}


def test_the_same_file_twice_is_registered_once(paths, tmp_path):
    first = onboard(CLEAN, "Clean shipments", paths=paths)
    copy = tmp_path / "renamed_copy.csv"
    copy.write_bytes(CLEAN.read_bytes())
    second = onboard(copy, "Another name", paths=paths)

    assert first.status == "gated"
    assert second.status == "already_registered"
    assert "already registered as 'Clean shipments'" in second.message
    assert _query(paths, "SELECT count(*) FROM meta.datasets") == [(1,)]


def test_hostile_column_names_cannot_inject_sql(paths, tmp_path):
    hostile = ['id"; DROP TABLE meta.datasets; --', "a;b", "x'); DELETE FROM meta.datasets;--"]
    path = tmp_path / "hostile.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(hostile)
        for index in range(5):
            writer.writerow([f"k{index}", "1", "2"])

    result = onboard(path, "Robert'); DROP TABLE meta.datasets;--", paths=paths)

    assert result.status == "gated"
    columns = [
        row[0]
        for row in _query(
            paths,
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = ? AND table_name = 'bronze_raw' ORDER BY ordinal_position",
            [result.schema_name],
        )
    ]
    assert columns[:3] == hostile  # stored verbatim as names, never executed
    assert _query(paths, "SELECT name FROM meta.datasets") == [
        ("Robert'); DROP TABLE meta.datasets;--",)
    ]
    assert quote_column(hostile[0], hostile) == '"id""; DROP TABLE meta.datasets; --"'
    with pytest.raises(UnsafeIdentifier):
        quote_column("not_profiled", hostile)
    with pytest.raises(UnsafeIdentifier):
        quote_schema('ds_x"; DROP SCHEMA meta CASCADE; --')


@pytest.mark.parametrize(
    ("name", "content", "kwargs", "message"),
    [
        ("big.csv", b"a,b\n" + b"1,2\n" * 2000, {"max_bytes": 1024}, "limit is"),
        ("binary.csv", b"PK\x03\x04\x00\x00binary", {}, "looks binary"),
        ("nul.csv", b"a,b\n1,\x002\n", {}, "looks binary"),
        ("sheet.xlsx", b"a,b\n1,2\n", {}, "Only .csv"),
        ("wide.csv", ",".join(f"c{i}" for i in range(6)).encode() + b"\n1,2,3,4,5,6\n",
         {"max_columns": 5}, "limit is 5"),
        ("dupes.csv", b"a,a\n1,2\n", {}, "Duplicate column names"),
        ("empty.csv", b"", {}, "empty"),
    ],
)  # fmt: skip
def test_unsafe_files_are_rejected_before_anything_is_written(
    paths, tmp_path, name, content, kwargs, message
):
    path = tmp_path / name
    path.write_bytes(content)

    with pytest.raises(DatasetRejected) as rejected:
        read_csv_safely(path, **kwargs)
    assert any(message in problem for problem in rejected.value.problems)

    result = onboard(path, "Rejected", paths=paths, **kwargs)
    assert result.status == "rejected"
    assert not paths.db_path.exists()
    assert not paths.data_root.exists()


def test_delimiter_and_latin1_encoding_are_detected(tmp_path):
    path = tmp_path / "semicolon.csv"
    path.write_bytes("ciudad;valor\nMontréal;1,5\nSão Paulo;2\n".encode("latin-1"))

    csv_file = read_csv_safely(path)

    assert csv_file.delimiter == ";" and csv_file.encoding == "latin-1"
    assert csv_file.frame["ciudad"].tolist() == ["Montréal", "São Paulo"]


def test_reprofiling_the_same_file_gives_identical_stats():
    first = profile_csv(read_csv_safely(DIRTY)).as_dict()
    second = profile_csv(read_csv_safely(DIRTY)).as_dict()

    assert first == second
    assert len(first["preview"]) == 5  # never more than a 5-row preview
    rules_first = build_rules(profile_csv(read_csv_safely(MARKS)))
    rules_second = build_rules(profile_csv(read_csv_safely(MARKS)))
    assert rules_first == rules_second


def test_profile_flags_mixed_and_mostly_empty_columns(tmp_path):
    path = tmp_path / "flags.csv"
    rows = ["id,amount,notes"] + [f"{i},{i * 10},{'x' if i < 3 else ''}" for i in range(18)]
    rows += ["18,twelve,", "19,lots,"]
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")

    profile = profile_csv(read_csv_safely(path))
    by_name = {column.column_name: column for column in profile.columns}

    assert by_name["amount"].inferred_type == "int" and by_name["amount"].is_mixed_type
    assert by_name["notes"].is_mostly_null and by_name["notes"].null_pct == 85.0
    assert by_name["id"].is_candidate_key
    assert any("mixed types" in warning for warning in profile.warnings)
    assert any("85.0% empty" in warning for warning in profile.warnings)


def test_cli_lists_datasets(paths, capsys):
    onboard(MARKS, "Student marks", paths=paths)

    code = onboard_module.main(["--list", "--db-path", str(paths.db_path)])

    assert code == 0
    assert "Student marks" in capsys.readouterr().out


def test_dagster_job_is_tagged_and_only_onboards(paths, monkeypatch):
    from provenance.dags.definitions import defs

    job = defs.resolve_job_def("onboard_dataset")
    assert job.tags.get("dagster/concurrency_key") == "duckdb_writer"
    assert [node.name for node in job.nodes_in_topological_order] == ["onboard_dataset_op"]

    monkeypatch.setenv("PROVENANCE_DUCKDB_PATH", str(paths.db_path))
    monkeypatch.setenv("PROVENANCE_DATASETS_ROOT", str(paths.data_root))
    monkeypatch.setenv("PROVENANCE_DATASETS_QUARANTINE_ROOT", str(paths.quarantine_root))
    result = job.execute_in_process(
        run_config={
            "ops": {
                "onboard_dataset_op": {
                    "config": {"csv_path": str(MARKS), "dataset_name": "Student marks"}
                }
            }
        }
    )
    assert result.success
    assert _query(paths, "SELECT status FROM meta.datasets") == [("gated",)]
    # Nothing from the flagship sources was touched.
    schemas = {
        row[0] for row in _query(paths, "SELECT schema_name FROM information_schema.schemata")
    }
    assert "bronze" not in schemas and "silver" not in schemas


def test_dagster_op_retries_on_a_lock(monkeypatch):
    from provenance.dags import onboarding

    def locked(*_args, **_kwargs):
        raise duckdb.IOException("IO Error: Could not set lock on file")

    monkeypatch.setattr(onboarding, "onboard", locked)
    context = build_op_context(op_config={"csv_path": str(CLEAN), "dataset_name": "x"})
    with pytest.raises(RetryRequested):
        onboarding.onboard_dataset_op(context)
