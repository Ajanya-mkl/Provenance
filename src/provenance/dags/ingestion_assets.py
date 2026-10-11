from dagster import AssetKey, MaterializeResult, MetadataValue, asset

from provenance.ingestion.sources.dataco_shipments import load_dataco_shipments
from provenance.ingestion.sources.usaspending_contracts import load_usaspending_contracts
from provenance.ingestion.sources.worldbank_lpi import load_worldbank_lpi


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
