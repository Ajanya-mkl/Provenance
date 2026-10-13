from dagster import AssetKey, Failure, MaterializeResult, MetadataValue, asset

from provenance.data_quality.gate import validate_source_batches
from provenance.ingestion.sources.dataco_shipments import load_dataco_shipments
from provenance.ingestion.sources.usaspending_contracts import load_usaspending_contracts
from provenance.ingestion.sources.worldbank_lpi import load_worldbank_lpi


def _run_quality_gate(source: str) -> MaterializeResult:
    results = validate_source_batches(source)
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
    row_count = load_dataco_shipments()
    return MaterializeResult(metadata={"row_count": MetadataValue.int(row_count)})


@asset(deps=[AssetKey("dataco_shipments")])
def usaspending_contracts() -> MaterializeResult:
    row_count = load_usaspending_contracts()
    return MaterializeResult(metadata={"row_count": MetadataValue.int(row_count)})


@asset(deps=[AssetKey("usaspending_contracts")])
def worldbank_lpi() -> MaterializeResult:
    row_count = load_worldbank_lpi()
    return MaterializeResult(metadata={"row_count": MetadataValue.int(row_count)})


@asset(deps=[AssetKey("dataco_shipments")])
def gate_dataco_shipments() -> MaterializeResult:
    return _run_quality_gate("dataco_shipments")


@asset(deps=[AssetKey("usaspending_contracts"), AssetKey("gate_dataco_shipments")])
def gate_usaspending_contracts() -> MaterializeResult:
    return _run_quality_gate("usaspending_contracts")


@asset(deps=[AssetKey("worldbank_lpi"), AssetKey("gate_usaspending_contracts")])
def gate_worldbank_lpi() -> MaterializeResult:
    return _run_quality_gate("worldbank_lpi")
