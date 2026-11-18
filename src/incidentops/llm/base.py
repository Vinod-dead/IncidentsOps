"""Provider-agnostic LLM interface + validated-JSON helper with retry-with-feedback."""

import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar

T = TypeVar("T")


@dataclass
class LLMResult:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class LLM(Protocol):
    name: str

    def complete(self, system: str, prompt: str) -> LLMResult: ...


class LLMOutputError(Exception):
    def __init__(self, message: str, tokens: int = 0) -> None:
        super().__init__(message)
        self.tokens = tokens


def extract_json(text: str) -> dict[str, Any]:
    text = re.sub(r"```(?:json)?", "", text)
    i, j = text.find("{"), text.rfind("}")
    if i < 0 or j < i:
        raise ValueError("no JSON object found in reply")
    obj = json.loads(text[i : j + 1])
    if not isinstance(obj, dict):
        raise ValueError("top-level JSON must be an object")
    return obj


def llm_json(
    llm: LLM, system: str, prompt: str, validate: Callable[[dict[str, Any]], T], retries: int = 2
) -> tuple[T, int]:
    """Call the LLM, parse + validate JSON. On failure, re-ask with the error (up to `retries`)."""
    tokens, feedback, last = 0, "", "unknown"
    for _ in range(retries + 1):
        res = llm.complete(system, prompt + feedback)
        tokens += res.total_tokens
        try:
            return validate(extract_json(res.text)), tokens
        except (ValueError, TypeError, KeyError) as e:  # pydantic ValidationError is a ValueError
            last = str(e)[:300]
            feedback = (
                f"\n\nYour previous reply was invalid: {last}\n"
                "Return ONLY one valid JSON object matching the schema."
            )
    raise LLMOutputError(last, tokens)


def get_llm(provider: str | None = None) -> LLM:
    provider = (provider or os.getenv("INCIDENTOPS_LLM") or "fake").lower()
    if provider == "fake":
        from .fake import FakeLLM

        return FakeLLM()
    from . import providers

    factory: Any = {
        "anthropic": providers.AnthropicLLM,
        "openai": providers.OpenAILLM,
        "gemini": providers.GeminiLLM,
    }.get(provider)
    if factory is None:
        raise ValueError(f"unknown LLM provider '{provider}' (fake|anthropic|openai|gemini)")
    llm: LLM = factory()
    return llm
