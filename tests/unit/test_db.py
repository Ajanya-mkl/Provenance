from __future__ import annotations

import duckdb

from provenance.db import SCHEMAS, init_db


def test_init_db_creates_expected_schemas(tmp_path) -> None:
    db_path = tmp_path / "provenance.duckdb"

    init_db(db_path)

    with duckdb.connect(str(db_path)) as conn:
        schemas = {
            row[0]
            for row in conn.execute(
                "SELECT schema_name FROM information_schema.schemata "
                "WHERE schema_name IN (?, ?, ?, ?)",
                SCHEMAS,
            ).fetchall()
        }

    assert schemas == set(SCHEMAS)
