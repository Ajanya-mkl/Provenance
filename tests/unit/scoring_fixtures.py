"""Synthetic shipment CSVs large enough to score (Phase 10c tests and the demo).

Every day of every quarter has rows, so no quarter is partial; each supplier has
about 70 rows a quarter (above the flagship's 50). One supplier deteriorates in
the later quarters, so a tier rise (and a flag) is expected.
"""

from __future__ import annotations

import csv
import random
from datetime import date, timedelta
from pathlib import Path

SUPPLIERS = ("Acme Parts", "Borealis Metals", "Cobalt Freight", "Delta Plastics", "Ember Textiles")
# Mixed spellings on purpose: ISO2, ISO3, English and the flagship seed's Spanish.
COUNTRIES = ("DE", "FRA", "Mexico", "Estados Unidos", "Japón", "Brazil", "India")
ROWS_PER_DAY = 4


def quarter_starts(first: date, quarters: int) -> list[date]:
    starts, current = [], first
    for _ in range(quarters):
        starts.append(current)
        month = current.month + 3
        current = date(current.year + (month > 12), (month - 1) % 12 + 1, 1)
    return starts


def make_shipments_csv(
    path: Path,
    *,
    outcome: str = "days_pair_hours",
    country: bool = True,
    quarters: int = 6,
    first: date = date(2023, 1, 1),
    bad_rows: int = 3,
    unmatched_country_share: float = 0.0,
    days_short: int = 0,
    cancelled_share: float = 0.0,
    seed: int = 7,
) -> Path:
    """outcome: days_pair_hours | days_pair_days | delay_days | late_flag_on_time | late_flag.

    days_short drops that many days from the end, so the last quarter is partial.
    """
    rng = random.Random(seed)
    end = quarter_starts(first, quarters + 1)[-1] - timedelta(days=days_short)
    header = ["shipment_id", "supplier", "order_date"]
    header += {
        "days_pair_hours": ["planned_hours", "actual_hours"],
        "days_pair_days": ["planned_days", "actual_days"],
        "delay_days": ["delay_days"],
        "late_flag_on_time": ["on_time"],
        "late_flag": ["is_late"],
    }[outcome]
    if country:
        header.append("destination_country")
    if cancelled_share:
        header.append("status")
    rows = []
    day, number = first, 0
    while day < end:
        progress = (day - first).days / max((end - first).days, 1)
        for slot in range(ROWS_PER_DAY):
            number += 1
            supplier = SUPPLIERS[number % len(SUPPLIERS)]
            # Ember Textiles gets steadily later and more erratic.
            worse = supplier == "Ember Textiles" and progress > 0.45
            late_chance = 0.75 if worse else 0.35 + 0.1 * (slot % 2)
            planned = rng.choice((2, 3, 4, 5))
            late = rng.random() < late_chance
            slip = (
                rng.choice((1, 2, 3, 6, 9) if worse else (1, 2)) if late else -rng.choice((0, 0, 1))
            )
            actual = max(0, planned + slip)
            row = [f"SH{number:06d}", supplier, day.isoformat()]
            if outcome == "days_pair_hours":
                row += [planned * 24, actual * 24]
            elif outcome == "days_pair_days":
                row += [planned, actual]
            elif outcome == "delay_days":
                row += [actual - planned]
            elif outcome == "late_flag_on_time":
                row += ["no" if actual > planned else "yes"]
            else:
                row += [int(actual > planned)]
            if country:
                value = rng.choice(COUNTRIES)
                if rng.random() < unmatched_country_share:
                    value = "Atlantis"
                row.append(value)
            if cancelled_share:
                row.append("Cancelled" if rng.random() < cancelled_share else "Delivered")
            rows.append(row)
        day += timedelta(days=1)
    # Rows the 10a gate tolerates (under 1% of a column) but silver must reject.
    # Unparseable dates are not among them: the gate quarantines those itself.
    for index in range(bad_rows):
        broken = list(rows[index * 7])
        broken[0] = f"BAD{index}"
        if index % 3 == 0:
            broken[1] = ""  # entity is empty
        elif index % 3 == 1:
            # outcome is not a number; a flag column is blanked instead, so it stays
            # boolean-like for the mapper
            broken[3] = "" if outcome.startswith("late_flag") else "unknown"
        else:
            broken[1] = "???"  # entity has no letters or digits
        rows.append(broken)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)
    return path


# A small World Bank LPI table, enough for the destination-risk formula.
LPI_SCORES = {"DEU": 4.1, "FRA": 3.6, "MEX": 2.9, "USA": 3.8, "JPN": 3.9, "BRA": 2.8, "IND": 3.2}


def add_lpi(db_path: Path) -> None:
    import duckdb

    with duckdb.connect(str(db_path)) as connection:
        connection.execute("CREATE SCHEMA IF NOT EXISTS silver")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS silver.stg_worldbank_lpi "
            "(country_iso3 VARCHAR, lpi_year INTEGER, lpi_score DOUBLE)"
        )
        connection.executemany(
            "INSERT INTO silver.stg_worldbank_lpi VALUES (?, ?, ?)",
            [(iso3, 2018, score) for iso3, score in LPI_SCORES.items()]
            + [(iso3, 2022, score - 0.1) for iso3, score in LPI_SCORES.items()],
        )
