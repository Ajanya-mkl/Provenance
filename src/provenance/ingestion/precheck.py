"""Pre-check a DataCo-format CSV before anything is written.

Catches, with a readable message, the problems that would otherwise fail late:
the gate's exact ordered columns, the strict date format staging parses, blanks
in integer columns (pandas would turn the column into floats and fail the gate),
and Order Country names missing from the country_iso3_map seed (which fail the
silver not_null test after silver has been rebuilt).
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SUITE_PATH = PACKAGE_ROOT / "data_quality" / "expectations" / "bronze_suite.json"
COUNTRY_MAP_PATH = PACKAGE_ROOT / "transform" / "seeds" / "country_iso3_map.csv"
LINEAGE_COLUMNS = ("_batch_id", "_source", "_source_file", "_loaded_at")
DATE_COLUMNS = ("order date (DateOrders)", "shipping date (DateOrders)")
DATE_FORMAT = "%m/%d/%Y %H:%M"
EXAMPLE_LIMIT = 5
ENCODING = "latin-1"


@dataclass
class PrecheckResult:
    passed: bool
    row_count: int
    problems: list[str] = field(default_factory=list)

    def summary(self) -> str:
        if self.passed:
            return f"Pre-check passed: {self.row_count} rows, columns and values look loadable."
        lines = [f"Pre-check failed with {len(self.problems)} problem(s):"]
        lines += [f"  - {problem}" for problem in self.problems]
        return "\n".join(lines)


def expected_columns(suite_path: Path = SUITE_PATH) -> list[str]:
    """The DataCo source columns the gate requires, in order (lineage excluded)."""
    suite = json.loads(Path(suite_path).read_text(encoding="utf-8"))
    for expectation in suite["suites"]["bronze_dataco_shipments"]["expectations"]:
        if expectation["type"] == "expect_table_columns_to_match_ordered_list":
            return [c for c in expectation["kwargs"]["column_list"] if c not in LINEAGE_COLUMNS]
    raise ValueError("The DataCo suite has no ordered column list")


def integer_columns(suite_path: Path = SUITE_PATH) -> list[str]:
    """Columns the gate requires to be int64 or that hold integer ids."""
    suite = json.loads(Path(suite_path).read_text(encoding="utf-8"))
    columns = []
    for expectation in suite["suites"]["bronze_dataco_shipments"]["expectations"]:
        kwargs = expectation["kwargs"]
        if expectation["type"] == "expect_column_values_to_be_in_type_list" and (
            "int64" in kwargs.get("type_list", [])
        ):
            columns.append(kwargs["column"])
    return columns


def known_countries(country_map_path: Path = COUNTRY_MAP_PATH) -> set[str]:
    with Path(country_map_path).open(encoding="utf-8", newline="") as handle:
        return {row["order_country_es"] for row in csv.DictReader(handle)}


def _examples(values: pd.Series) -> str:
    shown = [repr(value) for value in values.head(EXAMPLE_LIMIT).tolist()]
    more = len(values) - len(shown)
    return ", ".join(shown) + (f" and {more} more" if more > 0 else "")


def precheck_dataco_csv(
    csv_path: str | Path,
    *,
    suite_path: Path = SUITE_PATH,
    country_map_path: Path = COUNTRY_MAP_PATH,
) -> PrecheckResult:
    """Check a DataCo CSV without writing anything; returns every problem found."""
    path = Path(csv_path)
    if not path.is_file():
        return PrecheckResult(False, 0, [f"File not found: {path.name}"])
    try:
        # Everything as text: no type inference, so nothing is silently converted.
        frame = pd.read_csv(
            path, encoding=ENCODING, dtype=str, keep_default_na=False, low_memory=False
        )
    except (pd.errors.ParserError, UnicodeDecodeError, ValueError) as error:
        return PrecheckResult(False, 0, [f"Could not read the file as CSV: {error}"])

    problems: list[str] = []
    expected = expected_columns(suite_path)
    actual = list(frame.columns)
    if actual != expected:
        missing = [column for column in expected if column not in actual]
        extra = [column for column in actual if column not in expected]
        detail = []
        if missing:
            detail.append(f"missing {missing}")
        if extra:
            detail.append(f"unexpected {extra}")
        if not missing and not extra:
            first = next(i for i, (a, b) in enumerate(zip(actual, expected)) if a != b)
            detail.append(
                f"wrong order: column {first + 1} is {actual[first]!r}, "
                f"expected {expected[first]!r}"
            )
        problems.append(
            f"Columns must match the {len(expected)} DataCo columns exactly and in order: "
            + "; ".join(detail)
            + "."
        )

    for column in DATE_COLUMNS:
        if column not in frame.columns:
            continue
        values = frame[column].str.strip()
        parsed = pd.to_datetime(values, format=DATE_FORMAT, errors="coerce")
        bad = values[parsed.isna()]
        if len(bad):
            problems.append(
                f"{len(bad)} value(s) in {column!r} are not in the format "
                f"month/day/year hour:minute ({DATE_FORMAT}), e.g. {_examples(bad)}."
            )

    for column in integer_columns(suite_path):
        if column not in frame.columns:
            continue
        values = frame[column].str.strip()
        blanks = values == ""
        if blanks.any():
            problems.append(
                f"{int(blanks.sum())} blank value(s) in integer column {column!r}; "
                "blanks would turn the column into decimals and fail the gate."
            )
        not_integer = values[~blanks & ~values.str.fullmatch(r"-?\d+")]
        if len(not_integer):
            problems.append(
                f"{len(not_integer)} non-integer value(s) in {column!r}, "
                f"e.g. {_examples(not_integer)}."
            )

    if "Order Country" in frame.columns:
        countries = set(frame["Order Country"].str.strip())
        missing_countries = sorted(countries - known_countries(country_map_path))
        if missing_countries:
            problems.append(
                f"{len(missing_countries)} Order Country value(s) are not in "
                f"country_iso3_map: {', '.join(missing_countries)}. "
                "Add them to the seed (with their ISO3 code) before loading."
            )

    return PrecheckResult(not problems, len(frame), problems)
