from __future__ import annotations

import json
import logging
import shutil
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import great_expectations as gx
import pandas as pd
import yaml

from provenance.db import SCHEMAS

LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_BRONZE_ROOT = PROJECT_ROOT / "data" / "bronze"
DEFAULT_QUARANTINE_ROOT = PROJECT_ROOT / "data" / "quarantine"
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "provenance.duckdb"
SUITE_PATH = Path(__file__).parent / "expectations" / "bronze_suite.json"
CHECKPOINT_PATH = Path(__file__).parent / "checkpoints" / "bronze_checkpoint.yml"
SUPPORTED_SOURCES = {
    "dataco_shipments",
    "usaspending_contracts",
    "worldbank_lpi",
}
EVENT_RESULT_KEYS = (
    "element_count",
    "unexpected_count",
    "unexpected_percent",
    "observed_value",
)
EVENT_PARTIAL_LIST_LIMIT = 5


@dataclass(frozen=True)
class GateResult:
    passed: bool
    batch_id: str
    source: str
    passed_expectations: int
    failed_expectations: int
    failed_details: list[dict[str, Any]]
    result_json: dict[str, Any]
    warnings: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _load_config(
    suite_path: str | Path = SUITE_PATH,
    checkpoint_path: str | Path = CHECKPOINT_PATH,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    suite_config = json.loads(Path(suite_path).read_text(encoding="utf-8"))
    checkpoint_config = yaml.safe_load(Path(checkpoint_path).read_text(encoding="utf-8"))
    suites = suite_config.get("suites")
    checkpoints = checkpoint_config.get("checkpoints")
    if not isinstance(suites, dict):
        raise ValueError("Bronze expectation config must contain a suites mapping")
    if not isinstance(checkpoints, dict):
        raise ValueError("Bronze checkpoint config must contain a checkpoints mapping")
    return suites, checkpoints, str(checkpoint_config.get("result_format", "SUMMARY"))


def _temporal_view(source: str, dataframe: pd.DataFrame) -> pd.DataFrame:
    result = dataframe.copy()
    if source == "usaspending_contracts":
        for column in ("Start Date", "End Date"):
            result[column] = pd.to_datetime(result[column], errors="coerce")
    elif source == "worldbank_lpi":
        result["date"] = pd.to_numeric(result["date"], errors="coerce")
    return result


def _as_json(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


def _date_order_warning_rows(dataframe: pd.DataFrame) -> list[dict[str, Any]]:
    start_dates = pd.to_datetime(dataframe["Start Date"], errors="coerce")
    end_dates = pd.to_datetime(dataframe["End Date"], errors="coerce")
    reversed_dates = start_dates.notna() & end_dates.notna() & start_dates.gt(end_dates)
    columns = ["generated_internal_id", "Start Date", "End Date"]
    rows = dataframe.loc[reversed_dates, columns].copy()
    rows["Start Date"] = start_dates.loc[reversed_dates].dt.strftime("%Y-%m-%d")
    rows["End Date"] = end_dates.loc[reversed_dates].dt.strftime("%Y-%m-%d")
    return rows.to_dict(orient="records")


def evaluate_bronze_dataframe(
    dataframe: pd.DataFrame,
    *,
    source: str,
    batch_id: str,
    suite_path: str | Path = SUITE_PATH,
    checkpoint_path: str | Path = CHECKPOINT_PATH,
) -> GateResult:
    """Evaluate one bronze DataFrame with Great Expectations in an ephemeral context."""
    if source not in SUPPORTED_SOURCES:
        raise ValueError(f"Unsupported bronze source: {source}")

    suites, checkpoints, result_format = _load_config(suite_path, checkpoint_path)
    if source not in checkpoints:
        raise ValueError(f"No bronze checkpoint is configured for {source}")

    context = gx.get_context(mode="ephemeral")
    datasource = context.data_sources.add_pandas(name="bronze_pandas")
    passed_count = 0
    failed_count = 0
    failed_details: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    checkpoint_results: list[dict[str, Any]] = []
    current_year = datetime.now(UTC).year

    for index, config in enumerate(checkpoints[source]):
        suite_name = config["suite"]
        suite = gx.ExpectationSuite(**suites[suite_name])
        context.suites.add(suite)

        view_name = config["dataframe"]
        view = dataframe if view_name == "source" else _temporal_view(source, dataframe)
        asset_name = f"{source}_{view_name}_{index}"
        asset = datasource.add_dataframe_asset(name=asset_name)
        batch_definition = asset.add_batch_definition_whole_dataframe(
            name=f"{asset_name}_batch"
        )
        validation = gx.ValidationDefinition(
            name=f"{config['name']}_validation",
            data=batch_definition,
            suite=suite,
        )
        context.validation_definitions.add(validation)
        checkpoint = gx.Checkpoint(
            name=config["name"],
            validation_definitions=[validation],
            actions=[],
            result_format=result_format,
        )
        context.checkpoints.add(checkpoint)
        checkpoint_result = checkpoint.run(
            batch_parameters={"dataframe": view},
            expectation_parameters={"current_year": current_year},
        )
        validation_result = next(iter(checkpoint_result.run_results.values()))
        passed_count += validation_result.statistics["successful_expectations"]
        failed_count += validation_result.statistics["unsuccessful_expectations"]
        checkpoint_results.append(
            {
                "checkpoint": config["name"],
                "suite": suite_name,
                "dataframe": view_name,
                "validation_result": validation_result.to_json_dict(),
            }
        )
        for expectation_result in validation_result.results:
            expectation_kwargs = expectation_result.expectation_config.kwargs
            unexpected_count = expectation_result.result.get("unexpected_count", 0)
            expectation_type = expectation_result.expectation_config.type
            if (
                expectation_kwargs.get("mostly") is not None
                and unexpected_count
                and expectation_type != "expect_column_values_to_not_be_null"
            ):
                warning: dict[str, Any] = {
                    "checkpoint": config["name"],
                    "expectation_type": expectation_type,
                    "unexpected_count": unexpected_count,
                }
                if (
                    source == "usaspending_contracts"
                    and expectation_type == "expect_column_pair_values_a_to_be_greater_than_b"
                ):
                    warning["details"] = _date_order_warning_rows(dataframe)
                else:
                    warning["details"] = {
                        "partial_unexpected_list": expectation_result.result.get(
                            "partial_unexpected_list", []
                        )
                    }
                warnings.append(warning)
            if expectation_result.success:
                continue
            failed_details.append(
                {
                    "checkpoint": config["name"],
                    "suite": suite_name,
                    "expectation_type": expectation_result.expectation_config.type,
                    "kwargs": expectation_result.expectation_config.kwargs,
                    "result": expectation_result.result,
                    "exception_info": expectation_result.exception_info,
                }
            )

    passed = failed_count == 0
    result_json = _as_json(
        {
            "source": source,
            "batch_id": batch_id,
            "passed": passed,
            "passed_expectations": passed_count,
            "failed_expectations": failed_count,
            "failed_details": failed_details,
            "warnings": warnings,
            "checkpoint_results": checkpoint_results,
        }
    )
    return GateResult(
        passed=passed,
        batch_id=batch_id,
        source=source,
        passed_expectations=passed_count,
        failed_expectations=failed_count,
        failed_details=result_json["failed_details"],
        result_json=result_json,
        warnings=result_json["warnings"],
    )


def gate_status(passed: bool, warnings: list[dict[str, Any]]) -> str:
    if not passed:
        return "quarantined"
    return "passed_with_warnings" if warnings else "validated"


def _relative_result_path(result_path: Path | None) -> str | None:
    if result_path is None:
        return None
    try:
        return result_path.resolve().relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return result_path.resolve().as_posix()


def build_event_payload(
    result_json: dict[str, Any],
    *,
    result_path: Path | None,
) -> dict[str, Any]:
    """Summarise a gate result for agent.events; the full result lives elsewhere."""
    failed_details = []
    for detail in result_json.get("failed_details", []):
        kwargs = {
            key: value
            for key, value in detail.get("kwargs", {}).items()
            if key != "batch_id"
        }
        result = detail.get("result") or {}
        trimmed: dict[str, Any] = {
            "checkpoint": detail.get("checkpoint"),
            "expectation_type": detail.get("expectation_type"),
            "kwargs": kwargs,
        }
        trimmed.update({key: result[key] for key in EVENT_RESULT_KEYS if key in result})
        if "partial_unexpected_list" in result:
            trimmed["partial_unexpected_list"] = result["partial_unexpected_list"][
                :EVENT_PARTIAL_LIST_LIMIT
            ]
        exception_info = detail.get("exception_info") or {}
        if exception_info.get("raised_exception"):
            trimmed["exception_message"] = exception_info.get("exception_message")
        failed_details.append(trimmed)
    return _as_json(
        {
            "source": result_json["source"],
            "batch_id": result_json["batch_id"],
            "status": gate_status(
                bool(result_json["passed"]), result_json.get("warnings", [])
            ),
            "passed_expectations": result_json["passed_expectations"],
            "failed_expectations": result_json["failed_expectations"],
            "failed_details": failed_details,
            "result_path": _relative_result_path(result_path),
        }
    )


def ensure_agent_events_table(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS agent.events (
            event_id VARCHAR PRIMARY KEY,
            event_type VARCHAR NOT NULL,
            source VARCHAR NOT NULL,
            batch_id VARCHAR NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            payload JSON NOT NULL,
            resolved_at TIMESTAMPTZ,
            resolution VARCHAR,
            investigation_status VARCHAR NOT NULL DEFAULT 'open'
                CHECK (investigation_status IN ('open', 'queued', 'investigated'))
        )
        """
    )
    connection.execute(
        "ALTER TABLE agent.events ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMPTZ"
    )
    connection.execute(
        "ALTER TABLE agent.events ADD COLUMN IF NOT EXISTS resolution VARCHAR"
    )
    connection.execute(
        """
        ALTER TABLE agent.events
        ADD COLUMN IF NOT EXISTS investigation_status VARCHAR DEFAULT 'open'
        """
    )
    # Tables created before Phase 6 gain the column; backfill rows it left null.
    connection.execute(
        "UPDATE agent.events SET investigation_status = 'open' "
        "WHERE investigation_status IS NULL"
    )


def _record_result(
    result: GateResult,
    *,
    db_path: str | Path,
    checked_at: datetime,
    result_path: Path | None = None,
) -> None:
    database = Path(db_path)
    database.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(database)) as connection:
        for schema in SCHEMAS:
            connection.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS bronze.dq_results (
                batch_id VARCHAR NOT NULL,
                source VARCHAR NOT NULL,
                status VARCHAR NOT NULL,
                passed_expectations INTEGER NOT NULL,
                failed_expectations INTEGER NOT NULL,
                checked_at TIMESTAMPTZ NOT NULL,
                warnings JSON,
                result JSON NOT NULL,
                PRIMARY KEY (source, batch_id)
            )
            """
        )
        connection.execute(
            "ALTER TABLE bronze.dq_results ADD COLUMN IF NOT EXISTS warnings JSON"
        )
        ensure_agent_events_table(connection)
        status = gate_status(result.passed, result.warnings)
        serialized_result = json.dumps(result.result_json, default=str)
        serialized_warnings = json.dumps(result.warnings, default=str)
        connection.execute(
            """
            INSERT INTO bronze.dq_results
                (batch_id, source, status, passed_expectations,
                 failed_expectations, checked_at, warnings, result)
            VALUES (?, ?, ?, ?, ?, ?, ?::JSON, ?::JSON)
            ON CONFLICT (source, batch_id) DO UPDATE SET
                status = excluded.status,
                passed_expectations = excluded.passed_expectations,
                failed_expectations = excluded.failed_expectations,
                checked_at = excluded.checked_at,
                warnings = excluded.warnings,
                result = excluded.result
            """,
            [
                result.batch_id,
                result.source,
                status,
                result.passed_expectations,
                result.failed_expectations,
                checked_at,
                serialized_warnings,
                serialized_result,
            ],
        )
        if not result.passed:
            payload = build_event_payload(result.result_json, result_path=result_path)
            connection.execute(
                """
                INSERT INTO agent.events
                    (event_id, event_type, source, batch_id, created_at, payload,
                     resolved_at, resolution)
                VALUES (?, 'dq_failure', ?, ?, ?, ?::JSON, NULL, NULL)
                ON CONFLICT (event_id) DO UPDATE SET
                    created_at = excluded.created_at,
                    payload = excluded.payload,
                    resolved_at = NULL,
                    resolution = NULL,
                    -- A repeat failure reopens a resolved event, but does not
                    -- requeue one the agent already has.
                    investigation_status = CASE
                        WHEN resolved_at IS NOT NULL THEN 'open'
                        ELSE investigation_status
                    END
                """,
                [
                    f"dq_failure:{result.source}:{result.batch_id}",
                    result.source,
                    result.batch_id,
                    checked_at,
                    json.dumps(payload, default=str),
                ],
            )
        else:
            # Seeded demo events stay open so the agent can investigate them.
            connection.execute(
                """
                UPDATE agent.events
                SET resolved_at = ?, resolution = ?
                WHERE source = ?
                  AND event_type = 'dq_failure'
                  AND resolved_at IS NULL
                  AND created_at <= ?
                  AND coalesce(json_extract(payload, '$.demo')::BOOLEAN, false) = false
                """,
                [
                    checked_at,
                    f"superseded: batch {result.batch_id} passed as {status}",
                    result.source,
                    checked_at,
                ],
            )


def validate_bronze_parquet(
    parquet_path: str | Path,
    *,
    source: str,
    batch_id: str,
    db_path: str | Path | None = None,
    quarantine_root: str | Path | None = None,
) -> GateResult:
    """Read one parquet batch, validate it, and persist its gate outcome."""
    source_path = Path(parquet_path)
    batch_folder = source_path.parent
    if batch_folder.name != f"batch_id={batch_id}":
        raise ValueError(
            f"Batch id {batch_id} does not match parquet folder {batch_folder.name}"
        )
    parquet_files = sorted(batch_folder.glob("*.parquet"))
    if not parquet_files:
        raise FileNotFoundError(f"No parquet files found in bronze batch {batch_folder}")
    dataframe = pd.concat(
        [pd.read_parquet(path) for path in parquet_files],
        ignore_index=True,
    )
    result = evaluate_bronze_dataframe(dataframe, source=source, batch_id=batch_id)
    checked_at = datetime.now(UTC)
    result_path: Path | None = None

    if not result.passed:
        if not batch_folder.is_dir():
            raise FileNotFoundError(f"Bronze batch folder does not exist: {batch_folder}")
        root = (
            Path(quarantine_root)
            if quarantine_root is not None
            else DEFAULT_QUARANTINE_ROOT
        )
        target = root / source / f"batch_id={batch_id}"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(batch_folder, target, dirs_exist_ok=True)
        result_path = target / "validation_result.json"
        result_path.write_text(
            json.dumps(result.result_json, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        LOGGER.error(
            "Quarantined bronze.%s batch %s after %d failed expectations at %s",
            source,
            batch_id,
            result.failed_expectations,
            target,
        )

    _record_result(
        result,
        db_path=db_path if db_path is not None else DEFAULT_DB_PATH,
        checked_at=checked_at,
        result_path=result_path,
    )
    return result


def validate_source_batches(
    source: str,
    *,
    bronze_root: str | Path | None = None,
    db_path: str | Path | None = None,
    quarantine_root: str | Path | None = None,
) -> list[GateResult]:
    """Validate source parquet batches in order, stopping at the first failure."""
    if source not in SUPPORTED_SOURCES:
        raise ValueError(f"Unsupported bronze source: {source}")
    root = Path(bronze_root) if bronze_root is not None else DEFAULT_BRONZE_ROOT
    source_root = root / source
    parquet_files = sorted(source_root.rglob("*.parquet"))
    if not parquet_files:
        raise FileNotFoundError(f"No bronze parquet batches found for {source_root}")

    results: list[GateResult] = []
    batch_folders = sorted({parquet_path.parent for parquet_path in parquet_files})
    for batch_folder in batch_folders:
        folder_name = batch_folder.name
        if not folder_name.startswith("batch_id="):
            raise ValueError(f"Expected batch_id=<id> parent folder, got {batch_folder}")
        batch_id = folder_name.removeprefix("batch_id=")
        result = validate_bronze_parquet(
            sorted(batch_folder.glob("*.parquet"))[0],
            source=source,
            batch_id=batch_id,
            db_path=db_path,
            quarantine_root=quarantine_root,
        )
        results.append(result)
        if not result.passed:
            break
    return results
