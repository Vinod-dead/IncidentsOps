"""Real providers. Keys come from env vars only (ANTHROPIC_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY).

Model names are env-configurable because they change often:
INCIDENTOPS_MODEL overrides the default for whichever provider is selected.
NOTE: these adapters are thin and not exercised by the offline test-suite.
"""

import os

from .base import LLMResult


class AnthropicLLM:
    name = "anthropic"

    def __init__(self) -> None:
        import anthropic

        self.client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
        self.model = os.getenv("INCIDENTOPS_MODEL", "claude-sonnet-4-5")

    def complete(self, system: str, prompt: str) -> LLMResult:
        r = self.client.messages.create(
            model=self.model,
            max_tokens=1000,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in r.content if getattr(b, "type", "") == "text")
        return LLMResult(text, r.usage.input_tokens, r.usage.output_tokens)


class OpenAILLM:
    name = "openai"

    def __init__(self) -> None:
        import openai

        self.client = openai.OpenAI()  # reads OPENAI_API_KEY
        self.model = os.getenv("INCIDENTOPS_MODEL", "gpt-4o-mini")

    def complete(self, system: str, prompt: str) -> LLMResult:
        extra = {}
        if os.getenv("OPENAI_BASE_URL", "").startswith("https://integrate.api.nvidia.com"):
            extra["extra_body"] = {
                "chat_template_kwargs": {"enable_thinking": False}
            }

        r = self.client.chat.completions.create(
            model=self.model,
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            **extra,
        )
        u = r.usage
        return LLMResult(
            r.choices[0].message.content or "",
            u.prompt_tokens if u else 0,
            u.completion_tokens if u else 0,
        )


class GeminiLLM:
    name = "gemini"

    def __init__(self) -> None:
        from google import genai

        self.client = genai.Client()  # reads GEMINI_API_KEY / GOOGLE_API_KEY
        self.model = os.getenv("INCIDENTOPS_MODEL", "gemini-2.5-flash")

    def complete(self, system: str, prompt: str) -> LLMResult:
        from google.genai import types

        r = self.client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system, response_mime_type="application/json"
            ),
        )
        u = r.usage_metadata
        return LLMResult(
            r.text or "",
            getattr(u, "prompt_token_count", 0) or 0,
            getattr(u, "candidates_token_count", 0) or 0,
        )
