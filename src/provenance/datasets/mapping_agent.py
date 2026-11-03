"""Column mapping proposed by an LLM agent, checked by the deterministic validator.

The model sees the contract and the profile through three read-only tools. It
never sees text values: text columns show only length statistics, unless the
caller explicitly passes sample_values=True. It returns mapping JSON only; it
never writes SQL or code. The validator checks the proposal (grounding its
evidence against exactly what the tools returned); on failure the model gets one
retry with the validator's message, then the heuristic mapper takes over and the
proposal is marked source='heuristic'.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from provenance.agent.llm import LLMError
from provenance.datasets.contract import CONTRACT_DESCRIPTION
from provenance.datasets.heuristic_mapper import heuristic_assessment, heuristic_mapping
from provenance.datasets.validator import DatasetContext, ValidationResult, validate

LOGGER = logging.getLogger(__name__)
MAX_STEPS = 8
SAMPLE_VALUES = 5
SAMPLE_CHARS = 30
MODES = ("heuristic_first", "agent", "heuristic")
DEFAULT_CONFIDENCE_THRESHOLD = 0.8


def confidence_threshold() -> float:
    """MAPPING_AGENT_CONFIDENCE_THRESHOLD (default 0.8); below it heuristic-first asks the agent."""
    try:
        return float(
            os.environ.get("MAPPING_AGENT_CONFIDENCE_THRESHOLD", DEFAULT_CONFIDENCE_THRESHOLD)
        )
    except ValueError:
        return DEFAULT_CONFIDENCE_THRESHOLD


SYSTEM_PROMPT = """\
You map the columns of an uploaded dataset onto a standard shipment contract.

Rules:
1. Call get_contract and get_dataset_profile first; use get_column_profile for detail.
2. Use column names exactly as the tools list them. Never invent a column.
3. Each column may fill only one role. An entity is a supplier, vendor or category
   name or code, not a row identifier. event_date is the order or shipment date;
   copy its date_format from the profile.
4. For a late_flag, late_when is the value that means late: a column meaning
   "on time" maps with late_when "false".
5. In evidence, quote facts exactly as the tools showed them, as "key=value"
   strings. Do not compute or round numbers yourself.
6. If a required field cannot be mapped, set it to null and say why in notes.
7. Reply with ONLY the mapping JSON object: no prose, no code fence.
"""

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_contract",
            "description": "The shipment contract: required and optional fields, outcome kinds.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_dataset_profile",
            "description": "Every column: type, null %, distinct count, ranges or text lengths.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_column_profile",
            "description": "One column's full profile.",
            "parameters": {
                "type": "object",
                "properties": {"column": {"type": "string", "description": "exact column name"}},
                "required": ["column"],
                "additionalProperties": False,
            },
        },
    },
]


def column_view(
    context: DatasetContext, name: str, *, sample_values: bool = False
) -> dict[str, Any]:
    """What the model may see about one column. No text values unless sampled on purpose."""
    column = context.columns[name]
    kind = column["inferred_type"]
    view: dict[str, Any] = {
        "column": name,
        "inferred_type": kind,
        "null_pct": column["null_pct"],
        "distinct_count": column["distinct_count"],
        "is_candidate_key": bool(column["is_candidate_key"]),
    }
    if kind != "string" and column.get("type_share") is not None:
        view["type_share_pct"] = round(100 * column["type_share"], 2)
    if kind in ("int", "float", "date"):
        view["min_value"], view["max_value"] = column["min_value"], column["max_value"]
    if kind == "date":
        view["date_format"] = column["date_format"]
    if kind == "bool":
        view["distinct_values"] = sorted(
            str(value).lower() for value in context.frame[name].dropna().unique()
        )
    if kind == "string":
        lengths = context.frame[name].dropna().str.len()
        if len(lengths):
            view["min_length"] = int(lengths.min())
            view["mean_length"] = round(float(lengths.mean()), 1)
            view["max_length"] = int(lengths.max())
        if sample_values:
            values = sorted(context.frame[name].dropna().unique())[:SAMPLE_VALUES]
            view["sample_values"] = [str(value)[:SAMPLE_CHARS] for value in values]
    return view


class MappingTools:
    """The agent's read-only tools; every result is kept for grounding."""

    def __init__(self, context: DatasetContext, *, sample_values: bool = False) -> None:
        self.context = context
        self.sample_values = sample_values
        self.results: list[dict[str, Any]] = []

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "get_contract":
            result: dict[str, Any] = CONTRACT_DESCRIPTION
        elif name == "get_dataset_profile":
            result = {
                "dataset": self.context.name,
                "row_count": self.context.row_count,
                "column_count": len(self.context.columns),
                "columns": [
                    column_view(self.context, column, sample_values=self.sample_values)
                    for column in self.context.columns
                ],
            }
        elif name == "get_column_profile":
            column = arguments.get("column")
            if column not in self.context.columns:
                result = {"error": f"no column named {column!r}"}
            else:
                result = column_view(self.context, column, sample_values=self.sample_values)
        else:
            result = {"error": f"unknown tool {name!r}"}
        self.results.append({"name": name, "result": result})
        return result


@dataclass
class MappingProposal:
    mapping: dict[str, Any]
    source: str  # agent | heuristic | human
    validation: ValidationResult
    attempts: list[dict[str, Any]] = field(default_factory=list)
    fallback_reason: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_s: float = 0.0
    steps: int = 0
    # Which path produced the mapping (heuristic | agent | heuristic_fallback) and why
    # the agent was asked, if it was.
    decided_by: str = "heuristic"
    escalation_reasons: list[str] = field(default_factory=list)


def _parse(content: str | None) -> tuple[dict[str, Any] | None, str | None]:
    text = (content or "").strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None, "Your reply did not contain a JSON object. Reply with only the mapping JSON."
    try:
        mapping = json.loads(text[start : end + 1])
    except json.JSONDecodeError as error:
        return (
            None,
            f"Your reply was not valid JSON ({error.msg}). Reply with only the mapping JSON.",
        )
    return (
        (mapping, None) if isinstance(mapping, dict) else (None, "The mapping must be an object.")
    )


def heuristic_proposal(context: DatasetContext, reason: str | None = None) -> MappingProposal:
    started = time.perf_counter()
    mapping = heuristic_mapping(context)
    proposal = MappingProposal(
        mapping=mapping,
        source="heuristic",
        validation=validate(mapping, context),
        fallback_reason=reason,
    )
    proposal.latency_s = round(time.perf_counter() - started, 3)
    return proposal


def escalation_reasons(context: DatasetContext, threshold: float | None = None) -> list[str]:
    """Why the heuristic's answer is not good enough on its own (empty when it is)."""
    mapping, close = heuristic_assessment(context)
    validation = validate(mapping, context)
    limit = confidence_threshold() if threshold is None else threshold
    reasons = [f"heuristic left {name} unmapped" for name in validation.unmapped]
    reasons += [f"heuristic mapping is invalid: {error}" for error in validation.errors]
    reasons += [f"close candidates for {item}" for item in close]
    if float(mapping.get("confidence") or 0) < limit:
        reasons.append(f"heuristic confidence {mapping.get('confidence')} is below {limit}")
    return reasons


def propose_mapping(
    context: DatasetContext,
    *,
    llm_factory: Callable[[], Any] | None = None,
    heuristic_only: bool = False,
    mode: str = "heuristic_first",
    sample_values: bool = False,
    max_steps: int = MAX_STEPS,
    threshold: float | None = None,
) -> MappingProposal:
    """Propose a mapping.

    heuristic_first (default): the heuristic decides unless a required field is
    unmapped, two candidates are equally good, or its confidence is below the
    threshold; then the agent is asked. agent: always ask the agent. heuristic:
    never. An agent answer is validated, retried once, and falls back to the
    heuristic; `decided_by` records which path produced the stored mapping.
    """
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    if heuristic_only or llm_factory is None:
        mode = "heuristic"
    reasons: list[str] = []
    if mode == "heuristic_first":
        reasons = escalation_reasons(context, threshold)
        if not reasons:
            return heuristic_proposal(context)
    elif mode == "agent":
        reasons = ["agent mode: the agent is always asked"]
    if mode == "heuristic":
        return heuristic_proposal(context)
    proposal = _agent_proposal(
        context, llm_factory, sample_values=sample_values, max_steps=max_steps
    )
    proposal.escalation_reasons = reasons
    return proposal


def _agent_proposal(
    context: DatasetContext,
    llm_factory: Callable[[], Any],
    *,
    sample_values: bool,
    max_steps: int,
) -> MappingProposal:
    """Agent proposal, validated; one retry; heuristic fallback."""
    started = time.perf_counter()
    tools = MappingTools(context, sample_values=sample_values)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "Propose the column mapping for this dataset."},
    ]
    attempts: list[dict[str, Any]] = []
    prompt_tokens = completion_tokens = steps = 0
    try:
        llm = llm_factory()
        for steps in range(1, max_steps + 1):
            response = llm.chat(messages, TOOL_SCHEMAS)
            prompt_tokens += response.prompt_tokens
            completion_tokens += response.completion_tokens
            messages.append(response.as_message())
            if response.tool_calls:
                for call in response.tool_calls:
                    try:
                        arguments = json.loads(call.arguments or "{}")
                    except json.JSONDecodeError:
                        arguments = {}
                    result = tools.call(call.name, arguments if isinstance(arguments, dict) else {})
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.id,
                            "content": json.dumps(result, separators=(",", ":")),
                        }
                    )
                continue
            mapping, problem = _parse(response.content)
            if mapping is None:
                errors = [problem]
            else:
                validation = validate(mapping, context, extra_facts=tools.results)
                if validation.ok:
                    return MappingProposal(
                        mapping=validation.mapping,
                        source="agent",
                        validation=validation,
                        attempts=attempts,
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        latency_s=round(time.perf_counter() - started, 3),
                        steps=steps,
                        decided_by="agent",
                    )
                errors = validation.errors
            attempts.append({"mapping": mapping, "errors": errors})
            if len(attempts) >= 2:
                break
            messages.append(
                {
                    "role": "user",
                    "content": "The validator rejected the mapping:\n- "
                    + "\n- ".join(errors)
                    + "\nFix these and reply with only the corrected mapping JSON.",
                }
            )
        reason = (
            "the agent's mapping failed validation twice"
            if len(attempts) >= 2
            else "the agent did not return a mapping within its step limit"
        )
    except (LLMError, OSError) as error:
        reason = f"the agent could not be reached ({type(error).__name__})"
    LOGGER.warning("Mapping agent fell back to the heuristic: %s", reason)
    proposal = heuristic_proposal(context, reason)
    proposal.attempts = attempts
    proposal.prompt_tokens, proposal.completion_tokens = prompt_tokens, completion_tokens
    proposal.latency_s = round(time.perf_counter() - started, 3)
    proposal.steps = steps
    proposal.decided_by = "heuristic_fallback"
    return proposal
