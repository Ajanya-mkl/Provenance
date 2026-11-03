"""Generate a Great Expectations suite from a dataset profile, run it, explain it.

Every rule is either hard (a failure quarantines the batch) or tolerance (a
failure is a warning), and carries a plain-language reason. Rules describe what
the profile shows the data is meant to be, so a clean file passes and a file
with duplicated identifiers, impossible dates or missing columns fails:

- the profiled columns must be present (as a set; extra columns are allowed)
- columns with no empty values must stay complete (hard); up to 1% empty is a
  tolerance
- a column whose values are all numbers, booleans or dates keeps that type
  (hard); a mostly-typed (mixed) column only warns
- a column whose name marks it as an identifier, or the first unique column,
  must be unique (hard)
- numeric values stay near the profiled range, padded by 10% (tolerance)
- dates parse with the detected format (hard)
"""

from __future__ import annotations

import logging
import math
from dataclasses import asdict, dataclass, field
from typing import Any

import great_expectations as gx
import pandas as pd
from great_expectations import expectations as gxe

from provenance.datasets.profile import (
    BOOL_PATTERN,
    FLOAT_PATTERN,
    INT_PATTERN,
    ColumnProfile,
    DatasetProfile,
    normalized,
)

LOGGER = logging.getLogger(__name__)
HARD = "hard"
TOLERANCE = "tolerance"
NEAR_KEY_RATIO = 0.9
RANGE_PAD = 0.1
RANGE_MOSTLY = 0.99
MIXED_TYPE_MOSTLY = 0.95
SPARSE_NULL_MOSTLY = 0.99
TYPE_PATTERNS = {"int": INT_PATTERN, "float": FLOAT_PATTERN, "bool": BOOL_PATTERN}
TYPE_WORDS = {"int": "whole numbers", "float": "numbers", "bool": "true/false values"}


@dataclass(frozen=True)
class Rule:
    rule_id: str
    column: str | None
    check: str
    expectation: str
    kwargs: dict[str, Any]
    severity: str
    why: str
    frame: str = "text"  # "text" (normalized strings) or "typed" (numbers)


@dataclass
class RuleResult:
    rule: Rule
    success: bool
    unexpected_count: int | None
    element_count: int | None
    detail: str


@dataclass
class GateOutcome:
    passed: bool
    results: list[RuleResult] = field(default_factory=list)

    @property
    def hard_failures(self) -> list[RuleResult]:
        return [r for r in self.results if not r.success and r.rule.severity == HARD]

    @property
    def warnings(self) -> list[RuleResult]:
        return [r for r in self.results if not r.success and r.rule.severity == TOLERANCE]

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "hard_failures": len(self.hard_failures),
            "warnings": len(self.warnings),
            "results": [
                {
                    **asdict(result.rule),
                    "success": result.success,
                    "unexpected_count": result.unexpected_count,
                    "element_count": result.element_count,
                    "detail": result.detail,
                }
                for result in self.results
            ],
        }


def _key_column(profile: DatasetProfile) -> tuple[ColumnProfile, str] | None:
    rows = profile.row_count
    for column in profile.columns:
        if column.key_like_name and rows and column.distinct_count / rows >= NEAR_KEY_RATIO:
            return column, (
                f"its name marks it as an identifier and "
                f"{round(100 * column.distinct_count / rows, 1)}% of its values are distinct"
            )
    for column in profile.columns:
        if column.is_candidate_key:
            return column, "every value is present and distinct, so it identifies a row"
    return None


def _padded_range(column: ColumnProfile) -> tuple[float, float] | None:
    if column.min_value is None or column.max_value is None:
        return None
    low, high = float(column.min_value), float(column.max_value)
    span = high - low or abs(high) or 1.0
    pad = span * RANGE_PAD
    never_negative = low >= 0
    low, high = low - pad, high + pad
    if never_negative:
        low = max(low, 0.0)
    if column.inferred_type == "int":
        return float(math.floor(low)), float(math.ceil(high))
    return round(low, 6), round(high, 6)


def build_rules(profile: DatasetProfile) -> list[Rule]:
    rules: list[Rule] = []

    def add(**kwargs: Any) -> None:
        rules.append(Rule(rule_id=f"R{len(rules) + 1:02d}", **kwargs))

    add(
        column=None,
        check="columns present",
        expectation="ExpectTableColumnsToMatchSet",
        kwargs={"column_set": sorted(profile.column_names), "exact_match": False},
        severity=HARD,
        why=f"the profile found these {profile.column_count} columns; any missing column "
        "would break every later step",
    )
    add(
        column=None,
        check="has rows",
        expectation="ExpectTableRowCountToBeBetween",
        kwargs={"min_value": 1},
        severity=HARD,
        why="an empty batch carries no data",
    )

    key = _key_column(profile)
    if key:
        column, reason = key
        add(
            column=column.column_name,
            check="values are unique",
            expectation="ExpectColumnValuesToBeUnique",
            kwargs={"column": column.column_name},
            severity=HARD,
            why=f"{column.column_name!r} is the row identifier: {reason}",
        )

    for column in profile.columns:
        name = column.column_name
        if column.null_count == 0:
            add(
                column=name,
                check="no empty values",
                expectation="ExpectColumnValuesToNotBeNull",
                kwargs={"column": name},
                severity=HARD,
                why="every value was filled in",
            )
        elif column.null_pct <= 1:
            add(
                column=name,
                check="almost no empty values",
                expectation="ExpectColumnValuesToNotBeNull",
                kwargs={"column": name, "mostly": SPARSE_NULL_MOSTLY},
                severity=TOLERANCE,
                why=f"only {column.null_pct}% of values were empty",
            )

        if column.inferred_type in TYPE_PATTERNS:
            hard = not column.is_mixed_type
            add(
                column=name,
                check=f"values are {TYPE_WORDS[column.inferred_type]}",
                expectation="ExpectColumnValuesToMatchRegex",
                kwargs={
                    "column": name,
                    "regex": TYPE_PATTERNS[column.inferred_type],
                    **({} if hard else {"mostly": MIXED_TYPE_MOSTLY}),
                },
                severity=HARD if hard else TOLERANCE,
                why=(
                    f"every value was {TYPE_WORDS[column.inferred_type]}"
                    if hard
                    else f"{round(100 * column.type_share, 1)}% of values were "
                    f"{TYPE_WORDS[column.inferred_type]} (mixed column)"
                ),
            )

        # Identifiers are labels, not measures: no range rule for them.
        if column.inferred_type in ("int", "float") and not column.key_like_name:
            bounds = _padded_range(column)
            if bounds:
                add(
                    column=name,
                    check=f"values between {bounds[0]:g} and {bounds[1]:g}",
                    expectation="ExpectColumnValuesToBeBetween",
                    kwargs={
                        "column": name,
                        "min_value": bounds[0],
                        "max_value": bounds[1],
                        "mostly": RANGE_MOSTLY,
                    },
                    severity=TOLERANCE,
                    why=f"the profiled range was {column.min_value} to {column.max_value}, "
                    "padded by 10%" + (" and never below 0" if bounds[0] == 0 else ""),
                    frame="typed",
                )

        if column.looks_like_date and column.date_format:
            add(
                column=name,
                check=f"dates in format {column.date_format}",
                expectation="ExpectColumnValuesToMatchStrftimeFormat",
                kwargs={"column": name, "strftime_format": column.date_format},
                severity=HARD,
                why=f"{round(100 * column.type_share, 1)}% of values were dates in this "
                "format; a value that does not parse is not a real date",
            )
    return rules


def build_suite(name: str, rules: list[Rule]) -> gx.ExpectationSuite:
    """The rules as a Great Expectations suite (rule id and severity in meta)."""
    expectations = [
        getattr(gxe, rule.expectation)(
            **rule.kwargs, meta={"rule_id": rule.rule_id, "severity": rule.severity}
        )
        for rule in rules
    ]
    return gx.ExpectationSuite(name=name, expectations=expectations)


def validation_frames(frame: pd.DataFrame, profile: DatasetProfile) -> dict[str, pd.DataFrame]:
    """Text view (stripped, null tokens missing) and a numeric view for ranges."""
    text = pd.DataFrame({name: normalized(frame[name]) for name in frame.columns})
    numeric = [c.column_name for c in profile.columns if c.inferred_type in ("int", "float")]
    typed = pd.DataFrame(
        {name: pd.to_numeric(text[name], errors="coerce") for name in numeric if name in text}
    )
    return {"text": text, "typed": typed if numeric else text}


def _detail(rule: Rule, result: dict[str, Any], success: bool) -> str:
    if rule.expectation == "ExpectTableColumnsToMatchSet":
        missing = sorted(set(rule.kwargs["column_set"]) - set(result.get("observed_value") or []))
        return "all present" if success else f"missing: {missing}"
    if rule.expectation == "ExpectTableRowCountToBeBetween":
        return f"{result.get('observed_value')} rows"
    unexpected = result.get("unexpected_count")
    if unexpected is None:
        return "ok" if success else "could not be checked"
    total = result.get("element_count")
    noun = "duplicate" if rule.expectation == "ExpectColumnValuesToBeUnique" else "failing"
    tolerated = " (within tolerance)" if success and unexpected else ""
    return f"{unexpected} {noun} of {total} values{tolerated}"


def run_rules(
    rules: list[Rule], frame: pd.DataFrame, profile: DatasetProfile, suite_name: str
) -> GateOutcome:
    context = gx.get_context(mode="ephemeral")
    try:
        context.variables.progress_bars = {"globally": False}
    except Exception:  # noqa: BLE001 - cosmetic only
        pass
    source = context.data_sources.add_pandas(name="onboarding")
    frames = validation_frames(frame, profile)
    by_id: dict[str, RuleResult] = {}
    for frame_name, data in frames.items():
        selected = [rule for rule in rules if rule.frame == frame_name]
        if not selected:
            continue
        asset = source.add_dataframe_asset(name=f"{suite_name}_{frame_name}")
        batch = asset.add_batch_definition_whole_dataframe(name=f"{frame_name}_batch").get_batch(
            batch_parameters={"dataframe": data}
        )
        validation = batch.validate(build_suite(f"{suite_name}_{frame_name}", selected))
        for expectation_result in validation.results:
            rule_id = expectation_result.expectation_config.meta["rule_id"]
            rule = next(r for r in selected if r.rule_id == rule_id)
            result = expectation_result.result or {}
            by_id[rule_id] = RuleResult(
                rule=rule,
                success=bool(expectation_result.success),
                unexpected_count=result.get("unexpected_count"),
                element_count=result.get("element_count"),
                detail=_detail(rule, result, bool(expectation_result.success)),
            )
    results = [by_id[rule.rule_id] for rule in rules if rule.rule_id in by_id]
    outcome = GateOutcome(passed=True, results=results)
    outcome.passed = not outcome.hard_failures
    return outcome


def describe_rules(rules: list[Rule]) -> list[str]:
    return [
        f"{rule.rule_id} [{rule.severity}] "
        f"{(rule.column + ': ') if rule.column else ''}{rule.check} (because {rule.why})"
        for rule in rules
    ]


def report(outcome: GateOutcome) -> str:
    """Plain-language gate report: every rule, why it exists, and its result."""
    verdict = (
        "PASSED"
        if outcome.passed and not outcome.warnings
        else "PASSED WITH WARNINGS"
        if outcome.passed
        else "QUARANTINED"
    )
    lines = [
        f"Quality gate: {verdict} ({len(outcome.results)} rules, "
        f"{len(outcome.hard_failures)} hard failures, {len(outcome.warnings)} warnings)"
    ]
    for result in outcome.results:
        rule = result.rule
        mark = "ok  " if result.success else ("FAIL" if rule.severity == HARD else "warn")
        target = f"{rule.column}: " if rule.column else ""
        lines.append(
            f"  {mark} {rule.rule_id} [{rule.severity}] {target}{rule.check} -> {result.detail}"
        )
        lines.append(f"         why: {rule.why}")
    return "\n".join(lines)
