"""Deterministic grounding check: every number in a report must come from a tool.

No LLM is involved. A report number is grounded when some number in that run's
tool results rounds to it (half a unit in the report number's last decimal place,
plus a small absolute tolerance). Percentages also match their fraction, so
"63.1%" is grounded by 0.631.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

# A sign only counts when it does not follow a word character, so the dashes in
# 2017-01-01 split the date rather than making -01 negative; Q1 or v2 are labels.
NUMBER_PATTERN = re.compile(r"(?<![\w.])(-?)(\d+(?:\.\d+)?)(\s?%)?")
ABSOLUTE_TOLERANCE = 1e-6


@dataclass(frozen=True)
class ReportNumber:
    text: str
    value: float
    decimals: int
    percent: bool

    def candidates(self) -> list[float]:
        values = [self.value]
        if self.percent:
            values.append(self.value / 100)
        return values

    def tolerance(self, candidate: float) -> float:
        scale = 1 / 100 if self.percent and candidate != self.value else 1
        return 0.5 * 10 ** (-self.decimals) * scale + ABSOLUTE_TOLERANCE


@dataclass
class GroundingResult:
    grounded: bool
    grounded_ratio: float
    numbers_checked: int
    ungrounded_numbers: list[str] = field(default_factory=list)
    unknown_evidence_tools: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "grounded": self.grounded,
            "grounded_ratio": self.grounded_ratio,
            "numbers_checked": self.numbers_checked,
            "ungrounded_numbers": self.ungrounded_numbers,
            "unknown_evidence_tools": self.unknown_evidence_tools,
        }


def extract_numbers(text: str) -> list[ReportNumber]:
    numbers = []
    for match in NUMBER_PATTERN.finditer(text):
        sign, digits, percent = match.groups()
        decimals = len(digits.split(".")[1]) if "." in digits else 0
        numbers.append(
            ReportNumber(
                text=match.group(0).strip(),
                value=float(sign + digits),
                decimals=decimals,
                percent=bool(percent),
            )
        )
    return numbers


def _strings(value: Any) -> Iterable[str]:
    if isinstance(value, Mapping):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list | tuple):
        for item in value:
            yield from _strings(item)
    elif isinstance(value, bool) or value is None:
        return
    elif isinstance(value, int | float):
        yield repr(value)
    else:
        yield str(value)


def source_numbers(tool_results: Sequence[Any]) -> list[float]:
    """Every number in the tool results, including numbers inside strings."""
    values: set[float] = set()
    for result in tool_results:
        for text in _strings(result):
            values.update(number.value for number in extract_numbers(text))
    return sorted(values)


def _is_grounded(number: ReportNumber, sources: Sequence[float]) -> bool:
    for candidate in number.candidates():
        tolerance = number.tolerance(candidate)
        if any(abs(candidate - source) <= tolerance for source in sources):
            return True
    return False


def check_grounding(
    report: Mapping[str, Any],
    tool_calls: Sequence[Mapping[str, Any]],
) -> GroundingResult:
    """Check a report against the tool calls of the run that produced it.

    tool_calls items need "name" and "result". Evidence citing a tool that the
    run never called successfully also makes the report ungrounded.
    """
    successful = [call for call in tool_calls if "error" not in call["result"]]
    sources = source_numbers([call["result"] for call in successful])
    called = {call["name"] for call in successful}

    report_numbers = [number for text in _strings(report) for number in extract_numbers(text)]
    ungrounded = [number.text for number in report_numbers if not _is_grounded(number, sources)]

    unknown_tools = sorted(
        {
            str(item.get("tool_name"))
            for item in report.get("evidence", [])
            if isinstance(item, Mapping) and item.get("tool_name") not in called
        }
    )
    checked = len(report_numbers)
    ratio = 1.0 if checked == 0 else round((checked - len(ungrounded)) / checked, 4)
    return GroundingResult(
        grounded=not ungrounded and not unknown_tools,
        grounded_ratio=ratio,
        numbers_checked=checked,
        ungrounded_numbers=ungrounded,
        unknown_evidence_tools=unknown_tools,
    )


def dumps(result: GroundingResult) -> str:
    return json.dumps(result.as_dict(), sort_keys=True)
