"""Score an approved dataset: python -m provenance.datasets.score <dataset_id>.

    python -m provenance.datasets.score <dataset_id> [--bounds-mode dataset|flagship]
        [--refreeze] [--min-country-match 0.8] [--db-path PATH]

Refuses unless the dataset is 'mapped' (an approved mapping) and that mapping
is scoreable, and while a flagship restore point exists (a dbt build is mid-way
or failed and has not been restored). Everything else runs in one transaction
on one read-write connection, so a failure leaves the previous silver, mart,
scores and events in place:

    bronze_raw -> silver_shipments (+ silver_rejects, country_map)
               -> quarter_metrics -> mart_risk          (flagship definitions)
               -> risk_score, risk_flags, agent.events  (flagship scorer)

Normalisation bounds are the 5th and 95th percentile of each metric over the
dataset's eligible rows, frozen into meta.dataset_rules at the first scoring;
later scorings reuse them (so scores stay comparable across runs) until
--refreeze. bounds_mode='flagship' uses rules.yaml's bounds instead. Scores are
comparable within one dataset only. A factor the dataset cannot provide (no
usable country, or no delay for a late-flag-only outcome) is left out: the
remaining weights are renormalised, confidence is 'low' and contributing_factors
says why.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import duckdb
from dotenv import load_dotenv

from provenance.agent.store import append_audit, ensure_agent_tables
from provenance.data_quality.gate import ensure_agent_events_table
from provenance.datasets import country as country_module
from provenance.datasets import mapping_store, registry
from provenance.datasets.build_silver import SilverReport, build_silver
from provenance.datasets.contract import MIN_ELIGIBLE_QUARTERS
from provenance.datasets.identifiers import DATASET_ID_PATTERN, quote_schema, schema_for
from provenance.datasets.metrics import build_mart, build_quarter_metrics, lpi_available
from provenance.datasets.onboard import Paths
from provenance.risk_engine.scorer import (
    DEFAULT_RULES_PATH,
    FlagTarget,
    current_quarter_for,
    load_rules,
    score_rows,
    write_scores_and_flags,
)

ACTOR = "dataset_risk_engine"
EVENT_SOURCE = "dataset_risk_engine"
BOUNDS_MODES = ("dataset", "flagship")
LOW_PERCENTILE, HIGH_PERCENTILE = 0.05, 0.95
RESTORE_POINT_SCHEMA = "_restore_point"  # provenance.dags.restore_point.SCHEMA
FACTOR_ORDER = ("late_rate", "delay_stddev", "country_risk")


class ScoringRefused(RuntimeError):
    """The dataset cannot be scored now; nothing was written."""


@dataclass
class DatasetScoring:
    dataset_id: str
    rules_version: str
    bounds_mode: str
    rules_frozen_now: bool
    silver: SilverReport
    metrics_rows: int
    mart_rows: int
    eligible_quarters: int
    entities: int
    scores_written: int
    flags_added: int
    events_added: int
    events_superseded: int
    flags_resolved: int
    quarter: Any
    excluded_factors: dict[str, str] = field(default_factory=dict)
    weights: dict[str, float] = field(default_factory=dict)
    bounds: dict[str, dict[str, float]] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "rules_version": self.rules_version,
            "bounds_mode": self.bounds_mode,
            "rules_frozen_now": self.rules_frozen_now,
            "silver": self.silver.as_dict(),
            "metrics_rows": self.metrics_rows,
            "mart_rows": self.mart_rows,
            "eligible_quarters": self.eligible_quarters,
            "entities": self.entities,
            "scores_written": self.scores_written,
            "flags_added": self.flags_added,
            "events_added": self.events_added,
            "events_superseded": self.events_superseded,
            "flags_resolved": self.flags_resolved,
            "quarter": str(self.quarter),
            "excluded_factors": self.excluded_factors,
            "weights": self.weights,
            "bounds": self.bounds,
        }


def target_for(dataset_id: str) -> FlagTarget:
    schema = quote_schema(schema_for(dataset_id))
    return FlagTarget(
        scores_table=f"{schema}.risk_score",
        flags_table=f"{schema}.risk_flags",
        key_column="entity_key",
        event_prefix=f"ds_{dataset_id}:",
        source=EVENT_SOURCE,
        dataset_id=dataset_id,
    )


def _create_score_tables(connection: duckdb.DuckDBPyConnection, schema: str) -> None:
    target = quote_schema(schema)
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {target}.risk_score (
            entity_key VARCHAR NOT NULL,
            quarter DATE NOT NULL,
            score DOUBLE NOT NULL,
            tier VARCHAR NOT NULL,
            contributing_factors JSON NOT NULL,
            confidence VARCHAR NOT NULL,
            rules_version VARCHAR NOT NULL,
            PRIMARY KEY (entity_key, quarter)
        )
        """
    )
    # No PRIMARY KEY on risk_flags: a refreeze deletes the old version's flag rows and
    # may insert the same (entity, quarter) again in the same transaction, which
    # DuckDB's over-eager index check refuses. The scorer keeps one row per key
    # itself (it looks a flag up before choosing UPDATE or INSERT).
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {target}.risk_flags (
            entity_key VARCHAR NOT NULL,
            quarter DATE NOT NULL,
            old_tier VARCHAR,
            new_tier VARCHAR NOT NULL,
            score DOUBLE NOT NULL,
            top_contributing_factor JSON NOT NULL,
            confidence VARCHAR NOT NULL,
            event_id VARCHAR,
            status VARCHAR DEFAULT 'active',
            resolved_at TIMESTAMPTZ,
            resolution VARCHAR
        )
        """
    )


def _approved_mapping(db_path: str | Path, dataset_id: str) -> dict[str, Any]:
    dataset = registry.get_dataset(db_path, dataset_id)
    if dataset is None:
        raise ScoringRefused(f"Unknown dataset {dataset_id!r}.")
    if dataset["status"] != "mapped":
        raise ScoringRefused(
            f"Dataset {dataset_id} is {dataset['status']}; only a dataset with an approved "
            "mapping ('mapped') can be scored."
        )
    approved = [
        m for m in mapping_store.list_mappings(db_path, dataset_id) if m["status"] == "approved"
    ]
    if not approved:
        raise ScoringRefused(f"Dataset {dataset_id} has no approved mapping.")
    record = approved[-1]
    if not record["scoreable"]:
        raise ScoringRefused(
            f"The approved mapping is not scoreable: {record['reasons']['scoreable']}."
        )
    return {"dataset": dataset, "mapping": record}


def _percentile_bounds(
    connection: duckdb.DuckDBPyConnection, schema: str, factor: str
) -> dict[str, float]:
    """5th/95th percentile over the mart's eligible rows; widened if the metric is flat."""
    column = {
        "late_rate": "late_rate",
        "delay_stddev": "delay_stddev",
        "country_risk": "country_risk",
    }[factor]
    low, high = connection.execute(
        f"SELECT quantile_cont({column}, ?), quantile_cont({column}, ?) "
        f"FROM {quote_schema(schema)}.mart_risk WHERE {column} IS NOT NULL",
        [LOW_PERCENTILE, HIGH_PERCENTILE],
    ).fetchone()
    low, high = float(low), float(high)
    if high - low < 1e-12:
        # A metric that does not vary normalises to 0.5 rather than dividing by zero.
        pad = max(abs(low) * 0.05, 1e-6)
        low, high = low - pad, high + pad
    return {"min": low, "max": high}


def _effective_rules(
    connection: duckdb.DuckDBPyConnection,
    schema: str,
    base: dict[str, Any],
    *,
    bounds_mode: str,
    available: list[str],
    version: int,
) -> dict[str, Any]:
    weights = {name: float(base["weights"][name]) for name in available}
    total = sum(weights.values())
    rules = json.loads(json.dumps(base))
    rules["weights"] = {name: weight / total for name, weight in weights.items()}
    rules["normalization_bounds"] = {
        name: (
            {key: float(value) for key, value in base["normalization_bounds"][name].items()}
            if bounds_mode == "flagship"
            else _percentile_bounds(connection, schema, name)
        )
        for name in available
    }
    rules["rules_version"] = f"{base['rules_version']}-ds{version}"
    rules["flagship_rules_version"] = str(base["rules_version"])
    rules["bounds_mode"] = bounds_mode
    return rules


def score_dataset(
    db_path: str | Path,
    dataset_id: str,
    *,
    bounds_mode: str = "dataset",
    refreeze: bool = False,
    min_country_match: float = country_module.DEFAULT_MIN_MATCH_RATE,
    rules_path: str | Path = DEFAULT_RULES_PATH,
) -> DatasetScoring:
    if not DATASET_ID_PATTERN.match(str(dataset_id)):
        raise ScoringRefused("Dataset ids are 12 lowercase hex characters.")
    if bounds_mode not in BOUNDS_MODES:
        raise ScoringRefused(f"bounds_mode must be one of {BOUNDS_MODES}.")
    found = _approved_mapping(db_path, dataset_id)
    mapping_record = found["mapping"]
    mapping = mapping_record["proposal"]
    schema = schema_for(dataset_id)
    target = target_for(dataset_id)
    base_rules = load_rules(rules_path)
    profiled = [column["column_name"] for column in found["dataset"]["columns"]]

    with registry.writer(db_path) as connection:
        if connection.execute(
            "SELECT 1 FROM information_schema.schemata WHERE schema_name = ?",
            [RESTORE_POINT_SCHEMA],
        ).fetchone():
            raise ScoringRefused(
                "A flagship restore point exists (a dbt build is in progress or failed); "
                "score datasets once it has been restored or dropped."
            )
        ensure_agent_tables(connection)
        ensure_agent_events_table(connection)
        silver = build_silver(
            connection,
            schema=schema,
            profiled=profiled,
            mapping=mapping,
            min_country_match=min_country_match,
        )
        if silver.rows_out == 0:
            raise ScoringRefused("No row of the dataset parsed into the canonical table.")
        metrics_rows = build_quarter_metrics(connection, schema)

        excluded: dict[str, str] = {}
        use_country = silver.country_used
        if not use_country:
            excluded["country_risk"] = f"country risk not used: {silver.country_reason}"
        elif not lpi_available(connection):
            use_country = False
            excluded["country_risk"] = (
                "country risk not used: the warehouse has no World Bank LPI "
                "(silver.stg_worldbank_lpi)"
            )
        if mapping["outcome"]["kind"] == "late_flag":
            excluded["delay_stddev"] = (
                "delay variability not used: a late flag records lateness but not delay"
            )
        mart_rows = build_mart(connection, schema, use_country=use_country)

        quoted = quote_schema(schema)
        eligible_quarters, entities = connection.execute(
            f"SELECT count(DISTINCT quarter), count(DISTINCT entity_key) FROM {quoted}.mart_risk"
        ).fetchone()
        if eligible_quarters < MIN_ELIGIBLE_QUARTERS:
            raise ScoringRefused(
                f"Not scoreable: only {eligible_quarters} eligible quarter(s) after the "
                f"flagship's rules (at least {MIN_ELIGIBLE_QUARTERS} needed; a quarter is "
                "eligible when it is complete and an entity has 50 or more rows in it)."
            )

        available = [name for name in FACTOR_ORDER if name not in excluded]
        frozen = registry.frozen_rules(connection, dataset_id)
        rules_frozen_now = False
        if frozen and not refreeze:
            if frozen["mapping_id"] != mapping_record["mapping_id"]:
                raise ScoringRefused(
                    "The approved mapping changed since the scoring rules were frozen; "
                    "re-run with --refreeze to freeze new bounds."
                )
            if frozen["bounds_mode"] != bounds_mode:
                raise ScoringRefused(
                    f"The frozen rules use bounds_mode={frozen['bounds_mode']!r}; re-run with "
                    "that mode, or with --refreeze."
                )
            if set(frozen["rules"]["weights"]) != set(available):
                raise ScoringRefused(
                    "The factors available differ from those frozen "
                    f"({sorted(frozen['rules']['weights'])} vs {available}); re-run with "
                    "--refreeze."
                )
            rules = frozen["rules"]
        else:
            version = (frozen["version"] + 1) if frozen else 1
            rules = _effective_rules(
                connection,
                schema,
                base_rules,
                bounds_mode=bounds_mode,
                available=available,
                version=version,
            )
            rules["excluded_factors"] = excluded
            registry.freeze_rules(
                connection,
                dataset_id,
                version=version,
                rules_version=rules["rules_version"],
                bounds_mode=bounds_mode,
                mapping_id=mapping_record["mapping_id"],
                rules=rules,
            )
            rules_frozen_now = True

        cursor = connection.execute(
            f"""
            SELECT entity_key AS supplier_key, quarter, late_rate, delay_stddev,
                   country_risk, eligible_quarters_to_date, country_coverage
            FROM {quoted}.mart_risk
            ORDER BY quarter, entity_key
            """
        )
        names = [description[0] for description in cursor.description]
        rows = [dict(zip(names, values, strict=True)) for values in cursor.fetchall()]
        if "delay_stddev" in available:
            # A quarter whose shipments are all but one cancelled has no variability.
            rows = [row for row in rows if row["delay_stddev"] is not None]
        for row in rows:
            if "country_risk" not in available:
                row["country_coverage"] = 0.0
        scores = score_rows(rows, rules)
        if excluded:
            for score in scores:
                score["confidence"] = "low"
        quarter = current_quarter_for(scores, rules)

        _create_score_tables(connection, schema)
        flags_added, events_added, superseded, resolved = write_scores_and_flags(
            connection, scores, rules, target, excluded_factors=excluded
        )
        result = DatasetScoring(
            dataset_id=dataset_id,
            rules_version=rules["rules_version"],
            bounds_mode=bounds_mode,
            rules_frozen_now=rules_frozen_now,
            silver=silver,
            metrics_rows=metrics_rows,
            mart_rows=mart_rows,
            eligible_quarters=eligible_quarters,
            entities=entities,
            scores_written=len(scores),
            flags_added=flags_added,
            events_added=events_added,
            events_superseded=superseded,
            flags_resolved=resolved,
            quarter=quarter,
            excluded_factors=excluded,
            weights=rules["weights"],
            bounds=rules["normalization_bounds"],
        )
        append_audit(
            connection,
            ACTOR,
            "dataset_scored",
            {
                key: value
                for key, value in result.as_dict().items()
                if key not in ("silver", "bounds", "weights")
            }
            | {"rows_in": silver.rows_in, "rows_rejected": sum(silver.rejected.values())},
        )
    return result


def render(result: DatasetScoring) -> str:
    silver = result.silver.as_dict()
    lines = [
        f"Dataset {result.dataset_id} scored with rules {result.rules_version} "
        f"(bounds: {result.bounds_mode}, "
        f"{'frozen now' if result.rules_frozen_now else 'frozen earlier'})",
        f"  silver: {silver['rows_out']} of {silver['rows_in']} rows; "
        f"{silver['rows_rejected']} rejected",
    ]
    for reason, count in silver["rejected_by_reason"].items():
        lines.append(f"    - {count} rows: {reason}")
    if silver["country"]:
        country = silver["country"]
        lines.append(
            f"  country: match rate {country['match_rate']} "
            f"({country['unmatched_distinct']} unmatched distinct values)"
        )
    lines.append(
        f"  country used: {'yes' if silver['country_used'] else 'no'} - {silver['country_reason']}"
    )
    lines.append(
        f"  metrics: {result.metrics_rows} entity-quarters; mart: {result.mart_rows} eligible "
        f"rows over {result.eligible_quarters} quarters and {result.entities} entities"
    )
    lines.append(
        "  weights: "
        + ", ".join(f"{name} {round(weight, 4)}" for name, weight in result.weights.items())
    )
    for name, bounds in result.bounds.items():
        lines.append(f"  bounds {name}: {round(bounds['min'], 4)} .. {round(bounds['max'], 4)}")
    for name, reason in result.excluded_factors.items():
        lines.append(f"  left out {name}: {reason} (confidence lowered)")
    lines.append(
        f"  scores: {result.scores_written} through {result.quarter}; flags added "
        f"{result.flags_added}; events added {result.events_added}, superseded "
        f"{result.events_superseded}; flags resolved {result.flags_resolved}"
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("dataset_id")
    parser.add_argument("--bounds-mode", choices=BOUNDS_MODES, default="dataset")
    parser.add_argument(
        "--refreeze", action="store_true", help="freeze new bounds (new rules version)"
    )
    parser.add_argument(
        "--min-country-match",
        type=float,
        default=country_module.DEFAULT_MIN_MATCH_RATE,
        help="below this share of matched country rows, score without country risk",
    )
    parser.add_argument("--db-path", type=Path, default=None)
    args = parser.parse_args(argv)
    load_dotenv()
    db_path = args.db_path or Paths.from_env().db_path
    try:
        result = score_dataset(
            db_path,
            args.dataset_id,
            bounds_mode=args.bounds_mode,
            refreeze=args.refreeze,
            min_country_match=args.min_country_match,
        )
    except ScoringRefused as error:
        print(f"Refused: {error}", file=sys.stderr)
        return 2
    except duckdb.IOException:
        print("The warehouse is locked by another writer; try again shortly.", file=sys.stderr)
        return 3
    print(render(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
