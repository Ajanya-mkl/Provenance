"""Read any CSV safely and profile it.

Reading refuses anything that is not a small, text, delimited file with a usable
header: wrong extension, over the size or column limits, binary content,
duplicate or blank header names. Every value is read as text; types are only
inferred, and every stored statistic is rounded to a fixed precision, so
profiling the same bytes always gives the same result. Nothing here writes.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

DEFAULT_MAX_BYTES = 50 * 1024 * 1024
DEFAULT_MAX_COLUMNS = 200
ALLOWED_SUFFIXES = (".csv", ".tsv", ".txt")
DELIMITERS = ",;\t|"
SNIFF_BYTES = 64 * 1024
MAX_NAME_LENGTH = 128
PREVIEW_ROWS = 5
PREVIEW_CHARS = 40
STAT_CHARS = 64
TYPE_MAJORITY = 0.8
MOSTLY_NULL_PCT = 50.0
NULL_TOKENS = frozenset({"", "na", "n/a", "null", "none", "nan"})
BOOL_TOKENS = frozenset({"true", "false", "yes", "no", "t", "f", "y", "n"})
INT_PATTERN = r"^[+-]?(0|[1-9]\d*)$"
FLOAT_PATTERN = r"^[+-]?((0|[1-9]\d*)(\.\d+)?([eE][+-]?\d+)?|\.\d+)$"
BOOL_PATTERN = r"(?i)^(true|false|yes|no|t|f|y|n)$"
# Ordered: on a tie the earlier format wins (ISO first, then day-first).
DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y/%m/%d",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%d/%m/%Y %H:%M",
    "%m/%d/%Y %H:%M",
    "%d-%m-%Y",
    "%d.%m.%Y",
    "%d %b %Y",
    "%d %B %Y",
    "%b %d %Y",
)
KEY_NAME = re.compile(r"(^|[_\s\-])(id|key|code|sku|uuid|ref)$|[a-z0-9](Id|ID)$", re.IGNORECASE)
BINARY_SIGNATURES = (b"PK\x03\x04", b"%PDF", b"\x89PNG", b"\xd0\xcf\x11\xe0", b"\x1f\x8b")


class DatasetRejected(ValueError):
    """The file cannot be onboarded; `problems` says why, in plain language."""

    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class CsvFile:
    """A validated CSV: every cell as text, exactly as written."""

    frame: pd.DataFrame
    file_sha256: str
    size_bytes: int
    encoding: str
    delimiter: str
    source_filename: str


@dataclass
class ColumnProfile:
    column_name: str
    ordinal: int
    inferred_type: str
    type_share: float
    null_pct: float
    distinct_count: int
    min_value: str | None
    max_value: str | None
    is_candidate_key: bool
    looks_like_date: bool
    date_format: str | None
    is_mixed_type: bool
    is_mostly_null: bool
    key_like_name: bool


@dataclass
class DatasetProfile:
    file_sha256: str
    source_filename: str
    size_bytes: int
    encoding: str
    delimiter: str
    row_count: int
    column_count: int
    columns: list[ColumnProfile]
    warnings: list[str] = field(default_factory=list)
    preview: list[dict[str, str]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def column_names(self) -> list[str]:
        return [column.column_name for column in self.columns]


# --------------------------------------------------------------------- reading


def _decode(raw: bytes) -> tuple[str, str]:
    try:
        return raw.decode("utf-8-sig"), "utf-8"
    except UnicodeDecodeError:
        return raw.decode("latin-1"), "latin-1"


def _looks_binary(raw: bytes) -> bool:
    head = raw[:SNIFF_BYTES]
    if b"\x00" in head or head.startswith(BINARY_SIGNATURES):
        return True
    control = sum(1 for byte in head if byte < 32 and byte not in (9, 10, 13))
    return bool(head) and control / len(head) > 0.01


def _delimiter(text: str) -> str:
    """The delimiter that splits the first lines into a consistent column count.

    Checked before csv.Sniffer, which can be fooled by delimiter characters
    inside quoted header names.
    """
    sample = text[:SNIFF_BYTES]
    lines = sample.splitlines()[:50]
    if len(lines) > 1 and not sample.endswith(("\n", "\r")):
        lines = lines[:-1]  # the last line may be cut off by the sample size
    best, best_columns = None, 1
    for delimiter in DELIMITERS:
        counts = {len(row) for row in csv.reader(lines, delimiter=delimiter) if row}
        if len(counts) == 1 and (columns := counts.pop()) > best_columns:
            best, best_columns = delimiter, columns
    if best is not None:
        return best
    try:
        return csv.Sniffer().sniff(sample, delimiters=DELIMITERS).delimiter
    except csv.Error:
        return ","


def read_csv_safely(
    path: str | Path,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_columns: int = DEFAULT_MAX_COLUMNS,
) -> CsvFile:
    """Validate and read a CSV without writing anything; raises DatasetRejected."""
    source = Path(path)
    if not source.is_file():
        raise DatasetRejected([f"File not found: {source.name}"])
    if source.suffix.lower() not in ALLOWED_SUFFIXES:
        raise DatasetRejected(
            [f"Only {', '.join(ALLOWED_SUFFIXES)} files can be onboarded, not {source.suffix!r}."]
        )
    size = source.stat().st_size
    if size == 0:
        raise DatasetRejected(["The file is empty."])
    if size > max_bytes:
        raise DatasetRejected(
            [f"The file is {size / 1_048_576:.1f} MB; the limit is {max_bytes / 1_048_576:.1f} MB."]
        )
    raw = source.read_bytes()
    if _looks_binary(raw):
        raise DatasetRejected(["The file looks binary (for example a spreadsheet), not CSV text."])

    text, encoding = _decode(raw)
    delimiter = _delimiter(text)
    try:
        header = next(csv.reader(io.StringIO(text), delimiter=delimiter))
    except StopIteration:
        raise DatasetRejected(["The file has no header row."]) from None

    problems = []
    if len(header) > max_columns:
        problems.append(f"The file has {len(header)} columns; the limit is {max_columns}.")
    if len(header) < 1 or all(not name.strip() for name in header):
        problems.append("The header row is empty.")
    blank = [index + 1 for index, name in enumerate(header) if not name.strip()]
    if blank:
        problems.append(f"Header columns {blank} have no name.")
    seen, duplicates = set(), set()
    for name in header:
        (duplicates if name in seen else seen).add(name)
    if duplicates:
        problems.append(f"Duplicate column names: {sorted(duplicates)}.")
    if any(len(name) > MAX_NAME_LENGTH for name in header):
        problems.append(f"Column names must be at most {MAX_NAME_LENGTH} characters.")
    if any(re.search(r"[\x00-\x1f\x7f]", name) for name in header):
        problems.append("Column names must not contain control characters.")
    if problems:
        raise DatasetRejected(problems)

    try:
        frame = pd.read_csv(
            io.StringIO(text),
            sep=delimiter,
            dtype=str,
            keep_default_na=False,
            na_filter=False,
            skip_blank_lines=True,
        )
    except (pd.errors.ParserError, ValueError) as error:
        message = str(error).splitlines()[0][:200]
        raise DatasetRejected([f"The rows do not line up with the header: {message}"]) from None
    if list(frame.columns) != header:
        raise DatasetRejected(["The header could not be read consistently."])
    if frame.empty:
        raise DatasetRejected(["The file has a header but no data rows."])
    return CsvFile(
        frame=frame,
        file_sha256=hashlib.sha256(raw).hexdigest(),
        size_bytes=size,
        encoding=encoding,
        delimiter=delimiter,
        source_filename=source.name,
    )


# ------------------------------------------------------------------- profiling


def normalized(series: pd.Series) -> pd.Series:
    """Stripped text with null tokens as missing; shared with the quality gate."""
    stripped = series.astype(str).str.strip()
    return stripped.where(~stripped.str.lower().isin(NULL_TOKENS))


def _number(value: float) -> str:
    return repr(round(float(value), 6))


def _best_date_format(values: pd.Series) -> tuple[str | None, float, bool]:
    best, best_share, shares = None, 0.0, {}
    for date_format in DATE_FORMATS:
        parsed = pd.to_datetime(values, format=date_format, errors="coerce")
        share = round(float(parsed.notna().mean()), 4)
        shares[date_format] = share
        if share > best_share:
            best, best_share = date_format, share
    # Day-first and month-first both parsing every value means the order is a guess.
    ambiguous = False
    for first, other in (("%d/%m", "%m/%d"), ("%m/%d", "%d/%m")):
        if best is not None and best.startswith(first):
            ambiguous = shares.get(other + best[len(first) :]) == best_share
    return best, best_share, ambiguous


def profile_column(name: str, ordinal: int, raw: pd.Series) -> tuple[ColumnProfile, list[str]]:
    values = normalized(raw)
    rows = len(values)
    present = values.dropna()
    nulls = rows - len(present)
    null_pct = round(100.0 * nulls / rows, 2) if rows else 0.0
    distinct = int(present.nunique())
    warnings: list[str] = []

    shares = {
        "bool": float(present.str.lower().isin(BOOL_TOKENS).mean()) if len(present) else 0.0,
        "int": float(present.str.fullmatch(INT_PATTERN).mean()) if len(present) else 0.0,
        "float": float(present.str.fullmatch(FLOAT_PATTERN).mean()) if len(present) else 0.0,
    }
    date_format, date_share, ambiguous = (
        _best_date_format(present) if len(present) else (None, 0.0, False)
    )
    shares["date"] = date_share
    shares = {kind: round(share, 4) for kind, share in shares.items()}

    inferred, share = "string", 1.0
    for kind in ("bool", "int", "float", "date"):
        if shares[kind] >= TYPE_MAJORITY:
            inferred, share = kind, shares[kind]
            break
    best_typed = max(shares.values()) if shares else 0.0
    mixed = (inferred != "string" and share < 1.0) or (
        inferred == "string" and 0.5 <= best_typed < TYPE_MAJORITY
    )

    min_value = max_value = None
    if len(present):
        if inferred in ("int", "float"):
            numbers = pd.to_numeric(present, errors="coerce").dropna()
            if len(numbers) and inferred == "int":
                min_value, max_value = str(int(numbers.min())), str(int(numbers.max()))
            elif len(numbers):
                min_value, max_value = _number(numbers.min()), _number(numbers.max())
        elif inferred == "date":
            dates = pd.to_datetime(present, format=date_format, errors="coerce").dropna()
            if len(dates):
                with_time = "%H" in (date_format or "")
                shape = "%Y-%m-%d %H:%M:%S" if with_time else "%Y-%m-%d"
                min_value, max_value = dates.min().strftime(shape), dates.max().strftime(shape)
        elif inferred == "string":
            ordered = sorted(present.unique())
            min_value, max_value = ordered[0][:STAT_CHARS], ordered[-1][:STAT_CHARS]

    is_candidate_key = (
        rows >= 2 and nulls == 0 and distinct == rows and inferred in ("int", "string")
    )
    looks_like_date = inferred == "date"
    mostly_null = null_pct > MOSTLY_NULL_PCT
    if mixed:
        warnings.append(
            f"Column {name!r} has mixed types: {round(100 * share, 1)}% of its values "
            f"look like {inferred if inferred != 'string' else 'numbers or dates'}."
        )
    if mostly_null:
        warnings.append(f"Column {name!r} is {null_pct}% empty.")
    if looks_like_date and ambiguous:
        warnings.append(
            f"Column {name!r}: day and month order is ambiguous; assumed {date_format}."
        )
    profile = ColumnProfile(
        column_name=name,
        ordinal=ordinal,
        inferred_type=inferred,
        type_share=share if inferred != "string" else 1.0,
        null_pct=null_pct,
        distinct_count=distinct,
        min_value=min_value,
        max_value=max_value,
        is_candidate_key=is_candidate_key,
        looks_like_date=looks_like_date,
        date_format=date_format if looks_like_date else None,
        is_mixed_type=mixed,
        is_mostly_null=mostly_null,
        key_like_name=bool(KEY_NAME.search(name)),
    )
    return profile, warnings


def profile_csv(csv_file: CsvFile) -> DatasetProfile:
    """Profile a validated CSV. Pure: the same bytes give the same profile."""
    frame = csv_file.frame
    columns, warnings = [], []
    for ordinal, name in enumerate(frame.columns, start=1):
        column, column_warnings = profile_column(name, ordinal, frame[name])
        columns.append(column)
        warnings.extend(column_warnings)
    preview = [
        {name: str(value)[:PREVIEW_CHARS] for name, value in row.items()}
        for row in frame.head(PREVIEW_ROWS).to_dict(orient="records")
    ]
    return DatasetProfile(
        file_sha256=csv_file.file_sha256,
        source_filename=csv_file.source_filename,
        size_bytes=csv_file.size_bytes,
        encoding=csv_file.encoding,
        delimiter=csv_file.delimiter,
        row_count=len(frame),
        column_count=len(frame.columns),
        columns=columns,
        warnings=warnings,
        preview=preview,
    )
