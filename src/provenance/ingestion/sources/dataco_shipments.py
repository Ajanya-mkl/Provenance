from __future__ import annotations

from pathlib import Path

import pandas as pd

from provenance.ingestion.common import (
    DEFAULT_BRONZE_ROOT,
    DEFAULT_DB_PATH,
    file_sha256,
    write_bronze_dataframe,
)

DEFAULT_CSV_PATH = Path("data") / "raw" / "DataCoSupplyChainDataset.csv"
SOURCE = "dataco_shipments"


def load_dataco_shipments(
    *,
    csv_path: str | Path = DEFAULT_CSV_PATH,
    db_path: str | Path = DEFAULT_DB_PATH,
    bronze_root: str | Path = DEFAULT_BRONZE_ROOT,
) -> int:
    """Load the DataCo CSV to bronze without altering its source columns or values."""
    input_path = Path(csv_path)
    batch_id = file_sha256(input_path)
    frame = pd.read_csv(input_path, encoding="latin-1", low_memory=False)
    return write_bronze_dataframe(
        frame,
        source=SOURCE,
        batch_id=batch_id,
        source_file=input_path,
        db_path=db_path,
        bronze_root=bronze_root,
    )