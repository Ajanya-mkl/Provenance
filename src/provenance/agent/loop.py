"""ReAct-style investigation loop: the model calls read-only tools, then reports."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field, ValidationError

from provenance.agent.llm import LLMResponse
from provenance.agent.tools.investigation_tools import TOOL_SCHEMAS

LOGGER = logging.getLogger(__name__)
MAX_STEPS = 6

SYSTEM_PROMPT = """You investigate supplier risk flags in a supply-chain data warehouse.
Suppliers are product categories; you only see aggregated category metrics.

Rules:
1. Use the tools before making any claim. get_flag and recall_past_investigations
   have already been called for you; call get_score_breakdown before reporting.
2. Every number in your report must be copied from a tool result in this
   conversation. Do not compute, convert or estimate new numbers; do not write
   counts of your own (for example "three factors"). Quarters are written as
   tool results write them, e.g. 2017-Q1.
3. If the flag's confidence is "low", or a tool returns an error for this
   supplier or quarter, write "insufficient data" in the summary, set confidence
   to "low" and propose no_action. Never guess.
4. Do not speculate about causes the data does not show. Describe what changed,
   not why it might have changed.
5. Memory: if recall_past_investigations lists past cases, say in the summary
   whether this case matches them (same top factor, same tier change) and cite
   them only with fields from that result. If it lists none, say there are no
   earlier investigations.
6. Call at most two tools per turn, and stop calling tools once you have enough.

When you are done, reply with ONLY a JSON object, no prose and no code fence:
{
  "summary": "2-4 sentences grounded in tool results",
  "top_factor": "name of the factor that contributed most to the score",
  "evidence": [{"tool_name": "<tool you called>", "value": "<field>=<value from it>"}],
  "proposed_action": "no_action" | "watchlist" | "request_review",
  "confidence": "high" | "medium" | "low"
}
Propose request_review for a new tier of high with full data confidence,
watchlist for a new tier of medium with full data confidence, otherwise no_action.
"""


class Evidence(BaseModel):
    tool_name: str
    value: str | float | int


class Report(BaseModel):
    summary: str
    top_factor: str
    evidence: list[Evidence] = Field(min_length=1)
    # The approval guardrail enforces the allow-list (and audits violations).
    proposed_action: str = Field(min_length=1)
    confidence: Literal["high", "medium", "low"]


class ChatModel(Protocol):
    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str = "auto",
    ) -> LLMResponse: ...


class Toolbox(Protocol):
    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]: ...


@dataclass
class InvestigationResult:
    event_id: str
    report: dict[str, Any] | None
    steps: int
    stop_reason: Literal["final_answer", "max_steps"]
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0


def _parse_report(content: str | None) -> tuple[dict[str, Any] | None, str | None]:
    text = (content or "").strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    try:
        return Report.model_validate(json.loads(text)).model_dump(), None
    except json.JSONDecodeError:
        return None, "Your reply was not valid JSON. Reply with only the JSON report object."
    except ValidationError as error:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in issue['loc'])}: {issue['msg']}"
            for issue in error.errors()
        )
        return None, f"The report did not match the schema ({problems}). Reply with fixed JSON."


def _run_tool(toolbox: Toolbox, name: str, raw_arguments: str) -> dict[str, Any]:
    try:
        arguments = json.loads(raw_arguments or "{}")
    except json.JSONDecodeError:
        return {"error": "tool arguments were not valid JSON"}
    if not isinstance(arguments, dict):
        return {"error": "tool arguments must be a JSON object"}
    return toolbox.call(name, arguments)


def _prelude(
    event_id: str, toolbox: Toolbox, messages: list[dict[str, Any]], result: InvestigationResult
) -> None:
    """Call get_flag and recall_past_investigations before the model's first step.

    Recall must run for every investigation, so the loop does it rather than
    trusting the model to. The calls appear as one assistant tool turn.
    """
    calls = [("prelude_flag", "get_flag", {"event_id": event_id})]
    flag = toolbox.call("get_flag", calls[0][2])
    results = [flag]
    supplier_key = flag.get("supplier_key") if "error" not in flag else None
    if supplier_key:
        recall_arguments = {"supplier_key": supplier_key, "limit": 3}
        calls.append(("prelude_recall", "recall_past_investigations", recall_arguments))
        results.append(toolbox.call("recall_past_investigations", recall_arguments))

    messages.append(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(arguments)},
                }
                for call_id, name, arguments in calls
            ],
        }
    )
    for (call_id, name, arguments), tool_result in zip(calls, results, strict=True):
        result.tool_calls.append(
            {"step": 0, "name": name, "arguments": json.dumps(arguments), "result": tool_result}
        )
        messages.append(
            {
                "role": "tool",
                "tool_call_id": call_id,
                "content": json.dumps(tool_result, separators=(",", ":")),
            }
        )


def investigate(
    event_id: str,
    llm: ChatModel,
    toolbox: Toolbox,
    max_steps: int = MAX_STEPS,
) -> InvestigationResult:
    """Run the loop for one event. A step is one model call."""
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Investigate risk flag event_id={event_id}."},
    ]
    result = InvestigationResult(event_id, None, 0, "max_steps")
    _prelude(event_id, toolbox, messages, result)

    for step in range(1, max_steps + 1):
        last_step = step == max_steps
        if last_step:
            messages.append(
                {
                    "role": "user",
                    "content": "This is your last turn. Reply now with only the JSON report.",
                }
            )
        response = llm.chat(messages, TOOL_SCHEMAS, tool_choice="none" if last_step else "auto")
        result.steps = step
        result.prompt_tokens += response.prompt_tokens
        result.completion_tokens += response.completion_tokens
        messages.append(response.as_message())

        if response.tool_calls:
            for call in response.tool_calls:
                tool_result = _run_tool(toolbox, call.name, call.arguments)
                LOGGER.info("step %d: %s -> %s", step, call.name, "error" in tool_result)
                result.tool_calls.append(
                    {
                        "step": step,
                        "name": call.name,
                        "arguments": call.arguments,
                        "result": tool_result,
                    }
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": json.dumps(tool_result, separators=(",", ":")),
                    }
                )
            continue

        report, problem = _parse_report(response.content)
        if report is not None:
            result.report = report
            result.stop_reason = "final_answer"
            return result
        messages.append({"role": "user", "content": problem})

    return result
