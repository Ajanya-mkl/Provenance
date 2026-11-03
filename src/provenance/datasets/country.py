"""Country values -> ISO3: codes, English names and the flagship's Spanish seed.

Lookup order for each distinct value: the flagship seed's Spanish name exactly
as DataCo spells it (so the generic path agrees with the flagship), then an ISO3
code, an ISO2 code, and finally a name (Spanish seed names, the seed's English
names, World Bank names and a few common aliases) compared without case, accents
or punctuation. World Bank aggregates (regions, income groups) never match.
Everything is read from the dbt seed CSVs; nothing here touches the warehouse.
"""

from __future__ import annotations

import csv
import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

SEEDS = Path(__file__).resolve().parents[1] / "transform" / "seeds"
DEFAULT_MIN_MATCH_RATE = 0.8

# English forms the World Bank spells differently.
ALIASES = {
    "usa": "USA",
    "us": "USA",
    "united states of america": "USA",
    "america": "USA",
    "uk": "GBR",
    "great britain": "GBR",
    "britain": "GBR",
    "england": "GBR",
    "russia": "RUS",
    "south korea": "KOR",
    "korea": "KOR",
    "north korea": "PRK",
    "vietnam": "VNM",
    "iran": "IRN",
    "egypt": "EGY",
    "turkey": "TUR",
    "venezuela": "VEN",
    "syria": "SYR",
    "laos": "LAO",
    "czech republic": "CZE",
    "slovakia": "SVK",
    "ivory coast": "CIV",
    "cote d ivoire": "CIV",
    "democratic republic of the congo": "COD",
    "dr congo": "COD",
    "republic of the congo": "COG",
    "hong kong": "HKG",
    "macau": "MAC",
    "yemen": "YEM",
    "gambia": "GMB",
    "bahamas": "BHS",
    "kyrgyzstan": "KGZ",
    "micronesia": "FSM",
    "taiwan": "TWN",
}


def name_key(value: str) -> str:
    text = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


@dataclass(frozen=True)
class CountryLookup:
    seed_exact: Mapping[str, str]
    iso3: frozenset[str]
    iso2: Mapping[str, str]
    names: Mapping[str, str]

    def to_iso3(self, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        if not text:
            return None
        if text in self.seed_exact:
            return self.seed_exact[text]
        upper = text.upper()
        if len(upper) == 3 and upper in self.iso3:
            return upper
        if len(upper) == 2 and upper in self.iso2:
            return self.iso2[upper]
        return self.names.get(name_key(text))


@lru_cache(maxsize=1)
def lookup() -> CountryLookup:
    seed_exact: dict[str, str] = {}
    names: dict[str, str] = {}
    with (SEEDS / "country_iso3_map.csv").open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            iso3 = (row["iso3"] or "").strip()
            if not iso3:
                continue
            seed_exact[row["order_country_es"]] = iso3
            names.setdefault(name_key(row["order_country_es"]), iso3)
            if row.get("country_name_en"):
                names.setdefault(name_key(row["country_name_en"]), iso3)
    iso3_codes: set[str] = set()
    iso2: dict[str, str] = {}
    with (SEEDS / "wb_country_reference.csv").open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["is_aggregate"].strip().lower() == "true":
                continue
            iso3_codes.add(row["iso3"])
            if row["iso2"]:
                iso2[row["iso2"]] = row["iso3"]
            names.setdefault(name_key(row["country_name"]), row["iso3"])
            # "Korea, Rep." -> also "korea rep"; the comma form is the WB style.
            names.setdefault(name_key(row["country_name"].split(",")[0]), row["iso3"])
    for alias, iso3 in ALIASES.items():
        names.setdefault(name_key(alias), iso3)
    return CountryLookup(seed_exact, frozenset(iso3_codes), iso2, names)


@dataclass
class CountryReport:
    """How well a column's values matched; counts are rows, not distinct values."""

    mapping: dict[str, str | None] = field(default_factory=dict)
    rows: int = 0
    matched_rows: int = 0
    unmatched: dict[str, int] = field(default_factory=dict)

    @property
    def match_rate(self) -> float:
        return self.matched_rows / self.rows if self.rows else 0.0

    def as_dict(self, top: int = 10) -> dict:
        worst = sorted(self.unmatched.items(), key=lambda item: (-item[1], item[0]))[:top]
        return {
            "rows_with_country": self.rows,
            "matched_rows": self.matched_rows,
            "match_rate": round(self.match_rate, 4),
            "unmatched_distinct": len(self.unmatched),
            "top_unmatched": [{"value": value, "rows": rows} for value, rows in worst],
        }


def normalise(value_counts: Iterable[tuple[str | None, int]]) -> CountryReport:
    """Map each distinct (value, row count) to ISO3 and report the match rate."""
    table = lookup()
    report = CountryReport()
    for value, rows in value_counts:
        if value is None or not str(value).strip():
            continue
        iso3 = table.to_iso3(str(value))
        report.mapping[str(value)] = iso3
        report.rows += int(rows)
        if iso3:
            report.matched_rows += int(rows)
        else:
            report.unmatched[str(value)] = report.unmatched.get(str(value), 0) + int(rows)
    return report
