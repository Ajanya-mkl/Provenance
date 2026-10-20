"""Thin wrapper around the Groq chat-completions API with tool calling.

Configuration comes only from the environment (.env): LLM_API_KEY and
LLM_MODEL_NAME. The key is held on the client and never logged or printed.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import groq
from dotenv import load_dotenv

LOGGER = logging.getLogger(__name__)
DEFAULT_MODEL = "llama-3.3-70b-versatile"
DEFAULT_MAX_TOKENS = 1024
MAX_ATTEMPTS = 3
BASE_DELAY_SECONDS = 2.0
MAX_DELAY_SECONDS = 60.0


class LLMError(RuntimeError):
    """Raised when the model cannot be reached after every retry."""


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class LLMResponse:
    content: str | None
    tool_calls: list[ToolCall]
    prompt_tokens: int
    completion_tokens: int
    finish_reason: str | None

    def as_message(self) -> dict[str, Any]:
        """The assistant turn to append to the conversation."""
        message: dict[str, Any] = {"role": "assistant", "content": self.content or ""}
        if self.tool_calls:
            message["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": call.arguments},
                }
                for call in self.tool_calls
            ]
        return message


@dataclass
class UsageRecord:
    prompt_tokens: int
    completion_tokens: int
    attempts: int


@dataclass
class GroqLLM:
    """Temperature-0 chat client that retries 429s and malformed tool calls."""

    model: str = DEFAULT_MODEL
    max_tokens: int = DEFAULT_MAX_TOKENS
    max_attempts: int = MAX_ATTEMPTS
    base_delay: float = BASE_DELAY_SECONDS
    client: Any = field(default=None, repr=False)
    sleep: Callable[[float], None] = field(default=time.sleep, repr=False)
    usage: list[UsageRecord] = field(default_factory=list)

    @classmethod
    def from_env(cls, **overrides: Any) -> GroqLLM:
        load_dotenv()
        api_key = os.environ.get("LLM_API_KEY", "").strip()
        if not api_key:
            raise LLMError("LLM_API_KEY is not set; add it to .env.")
        model = os.environ.get("LLM_MODEL_NAME", "").strip() or DEFAULT_MODEL
        return cls(model=model, client=groq.Groq(api_key=api_key, max_retries=0), **overrides)

    @property
    def prompt_tokens(self) -> int:
        return sum(record.prompt_tokens for record in self.usage)

    @property
    def completion_tokens(self) -> int:
        return sum(record.completion_tokens for record in self.usage)

    def chat(
        self,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]] | None = None,
        tool_choice: str = "auto",
    ) -> LLMResponse:
        request: dict[str, Any] = {
            "model": self.model,
            "messages": list(messages),
            "temperature": 0,
            "max_tokens": self.max_tokens,
        }
        if tools:
            request["tools"] = list(tools)
            request["tool_choice"] = tool_choice

        for attempt in range(1, self.max_attempts + 1):
            try:
                completion = self.client.chat.completions.create(**request)
            except groq.RateLimitError as error:
                reason, delay = "rate limited (429)", _retry_after(error)
            except groq.BadRequestError as error:
                if not _is_malformed_tool_call(error):
                    raise
                reason, delay = "malformed tool call", None
            else:
                return self._parse(completion, attempt)

            if attempt == self.max_attempts:
                raise LLMError(f"LLM call failed after {attempt} attempts: {reason}")
            wait = delay if delay is not None else self.base_delay * 2 ** (attempt - 1)
            wait = min(wait, MAX_DELAY_SECONDS)
            LOGGER.warning("LLM %s; retrying in %.1fs (attempt %d)", reason, wait, attempt)
            self.sleep(wait)
        raise AssertionError("unreachable")

    def _parse(self, completion: Any, attempts: int) -> LLMResponse:
        choice = completion.choices[0]
        message = choice.message
        usage = completion.usage
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        self.usage.append(UsageRecord(prompt_tokens, completion_tokens, attempts))
        tool_calls = [
            ToolCall(
                id=call.id,
                name=call.function.name,
                arguments=call.function.arguments or "{}",
            )
            for call in (message.tool_calls or [])
        ]
        return LLMResponse(
            content=message.content,
            tool_calls=tool_calls,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            finish_reason=choice.finish_reason,
        )


def _is_malformed_tool_call(error: groq.BadRequestError) -> bool:
    # Groq rejects unparseable model-generated tool calls with code tool_use_failed.
    body = error.body if isinstance(error.body, dict) else {}
    detail = body.get("error", body) if isinstance(body.get("error", body), dict) else {}
    return detail.get("code") == "tool_use_failed" or "tool_use_failed" in str(error)


def _retry_after(error: groq.RateLimitError) -> float | None:
    response = getattr(error, "response", None)
    header = response.headers.get("retry-after") if response is not None else None
    try:
        return float(header) if header is not None else None
    except ValueError:
        return None
