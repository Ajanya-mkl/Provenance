import os
from collections.abc import Callable
from pathlib import Path

from dagster import (
    AssetExecutionContext,
    AssetKey,
    Backoff,
    Failure,
    MaterializeResult,
    MetadataValue,
    RetryPolicy,
    asset,
)

from provenance.data_quality.gate import validate_source_batches
from provenance.ingestion.sources.dataco_shipments import load_dataco_shipments
from provenance.ingestion.sources.usaspending_contracts import load_usaspending_contracts
from provenance.ingestion.sources.worldbank_lpi import load_worldbank_lpi

API_MAX_RETRIES = 3
PROJECT_ROOT = Path(__file__).resolve().parents[3]
API_RETRY_POLICY = RetryPolicy(
    max_retries=API_MAX_RETRIES,
    delay=1,
    backoff=Backoff.EXPONENTIAL,
)


def _pipeline_paths() -> tuple[Path, Path, Path]:
    database = Path(
        os.environ.get(
            "PROVENANCE_DUCKDB_PATH",
            PROJECT_ROOT / "data" / "provenance.duckdb",
        )
    )
    bronze = Path(
        os.environ.get(
            "PROVENANCE_BRONZE_ROOT",
            PROJECT_ROOT / "data" / "bronze",
        )
    )
    quarantine = Path(
        os.environ.get(
            "PROVENANCE_QUARANTINE_ROOT",
            PROJECT_ROOT / "data" / "quarantine",
        )
    )
    return database, bronze, quarantine


def _run_quality_gate(source: str) -> MaterializeResult:
    database, bronze, quarantine = _pipeline_paths()
    results = validate_source_batches(
        source,
        bronze_root=bronze,
        db_path=database,
        quarantine_root=quarantine,
    )
    failed = next((result for result in results if not result.passed), None)
    if failed is not None:
        raise Failure(
            description=(
                f"Bronze DQ gate quarantined {source} batch {failed.batch_id}; "
                f"{failed.failed_expectations} expectation(s) failed."
            ),
            metadata={
                "source": source,
                "batch_id": failed.batch_id,
                "failed_expectations": failed.failed_expectations,
                "failed_details": MetadataValue.json(failed.failed_details),
            },
        )
    return MaterializeResult(
        metadata={
            "source": source,
            "batches_validated": MetadataValue.int(len(results)),
            "passed_expectations": MetadataValue.int(
                sum(result.passed_expectations for result in results)
            ),
            "failed_expectations": MetadataValue.int(0),
            "warnings": MetadataValue.json(
                [
                    {
                        "batch_id": result.batch_id,
                        "warnings": result.warnings,
                    }
                    for result in results
                    if result.warnings
                ]
            ),
            "batch_results": MetadataValue.json(
                [
                    {
                        "batch_id": result.batch_id,
                        "passed_expectations": result.passed_expectations,
                        "failed_expectations": result.failed_expectations,
                    }
                    for result in results
                ]
            ),
        }
    )


# DuckDB is single-writer, so the asset chain serializes writes to its file.
@asset
def dataco_shipments() -> MaterializeResult:
    database, bronze, _quarantine = _pipeline_paths()
    row_count = load_dataco_shipments(
        csv_path=os.environ.get(
            "PROVENANCE_DATACO_CSV_PATH",
            PROJECT_ROOT / "data" / "raw" / "DataCoSupplyChainDataset.csv",
        ),
        db_path=database,
        bronze_root=bronze,
    )
    return MaterializeResult(metadata={"row_count": MetadataValue.int(row_count)})


def _fallback_metadata(fallbacks: list[dict[str, str]]) -> dict[str, object]:
    if not fallbacks:
        return {
            "cache_fallback_used": MetadataValue.bool(False),
        }
    return {
        "cache_fallback_used": MetadataValue.bool(True),
        "cache_fallback_source": MetadataValue.text(fallbacks[-1]["source"]),
        "cache_fallback_reason": MetadataValue.text(fallbacks[-1]["reason"]),
    }


def _record_fallback(fallbacks: list[dict[str, str]]) -> Callable[[str, str], None]:
    def record(source: str, reason: str) -> None:
        fallbacks.append({"source": source, "reason": reason})

    return record


@asset(
    deps=[AssetKey("dataco_shipments")],
    retry_policy=API_RETRY_POLICY,
)
def usaspending_contracts(context: AssetExecutionContext) -> MaterializeResult:
    database, bronze, _quarantine = _pipeline_paths()
    fallbacks: list[dict[str, str]] = []
    row_count = load_usaspending_contracts(
        db_path=database,
        bronze_root=bronze,
        allow_cached_fallback=context.retry_number >= API_MAX_RETRIES,
        on_cached_fallback=_record_fallback(fallbacks),
    )
    return MaterializeResult(
        metadata={
            "row_count": MetadataValue.int(row_count),
            **_fallback_metadata(fallbacks),
        }
    )


@asset(
    deps=[AssetKey("usaspending_contracts")],
    retry_policy=API_RETRY_POLICY,
)
def worldbank_lpi(context: AssetExecutionContext) -> MaterializeResult:
    database, bronze, _quarantine = _pipeline_paths()
    fallbacks: list[dict[str, str]] = []
    row_count = load_worldbank_lpi(
        db_path=database,
        bronze_root=bronze,
        allow_cached_fallback=context.retry_number >= API_MAX_RETRIES,
        on_cached_fallback=_record_fallback(fallbacks),
    )
    return MaterializeResult(
        metadata={
            "row_count": MetadataValue.int(row_count),
            **_fallback_metadata(fallbacks),
        }
    )


@asset(
    deps=[
        AssetKey("dataco_shipments"),
        AssetKey("usaspending_contracts"),
        AssetKey("worldbank_lpi"),
    ]
)
def gate_dataco_shipments() -> MaterializeResult:
    return _run_quality_gate("dataco_shipments")


@asset(deps=[AssetKey("usaspending_contracts"), AssetKey("gate_dataco_shipments")])
def gate_usaspending_contracts() -> MaterializeResult:
    return _run_quality_gate("usaspending_contracts")


@asset(deps=[AssetKey("worldbank_lpi"), AssetKey("gate_usaspending_contracts")])
def gate_worldbank_lpi() -> MaterializeResult:
    return _run_quality_gate("worldbank_lpi")
