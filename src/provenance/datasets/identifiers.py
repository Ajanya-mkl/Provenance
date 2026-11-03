"""Identifier safety for onboarded datasets.

Dataset schemas are generated (`ds_` + 12 hex characters), never taken from user
text. Column names come from a user's CSV header, so SQL may only reference a
column that the profile recorded, always double-quoted with embedded quotes
doubled. Values always travel as bound parameters.
"""

from __future__ import annotations

import re
from collections.abc import Collection

DATASET_ID_PATTERN = re.compile(r"^[0-9a-f]{12}$")
SCHEMA_PATTERN = re.compile(r"^ds_[0-9a-f]{12}$")


class UnsafeIdentifier(ValueError):
    """An identifier that is not one we generated or profiled."""


def dataset_id_from_sha(file_sha256: str) -> str:
    dataset_id = file_sha256[:12].lower()
    if not DATASET_ID_PATTERN.match(dataset_id):
        raise UnsafeIdentifier("dataset ids are 12 lowercase hex characters")
    return dataset_id


def schema_for(dataset_id: str) -> str:
    if not DATASET_ID_PATTERN.match(dataset_id):
        raise UnsafeIdentifier("dataset ids are 12 lowercase hex characters")
    return f"ds_{dataset_id}"


def quote_schema(schema: str) -> str:
    if not SCHEMA_PATTERN.match(schema):
        raise UnsafeIdentifier("schema names must look like ds_<12 hex characters>")
    return f'"{schema}"'


def quote_column(name: str, profiled: Collection[str]) -> str:
    """Quote a column name, but only one the profile recorded for this dataset."""
    if name not in profiled:
        raise UnsafeIdentifier("column is not part of this dataset's profile")
    return '"' + name.replace('"', '""') + '"'
