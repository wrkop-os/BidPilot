"""Thin wrapper around the Anthropic API for BidPilot agents.

Two call shapes:

* ``structured()`` — schema-validated extraction/analysis into a Pydantic
  model, via ``client.messages.parse``.
* ``draft()`` — long-form drafting (proposal volumes, narratives), streamed
  to avoid HTTP timeouts, with server-side refusal fallbacks enabled.
"""

from __future__ import annotations

import os
from typing import TypeVar

import anthropic
from pydantic import BaseModel

DEFAULT_MODEL = "claude-opus-5"

T = TypeVar("T", bound=BaseModel)


class RefusalError(RuntimeError):
    """Raised when the model's safety classifiers declined the request."""


class LLM:
    def __init__(self, model: str | None = None, effort: str = "high"):
        self.model = model or os.environ.get("BIDPILOT_MODEL", DEFAULT_MODEL)
        self.effort = effort
        self.client = anthropic.Anthropic()

    def structured(self, *, system: str, prompt: str, output_type: type[T], max_tokens: int = 16000) -> T:
        response = self.client.messages.parse(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_format=output_type,
        )
        if response.stop_reason == "refusal":
            raise RefusalError(_refusal_message(response))
        if response.stop_reason == "max_tokens":
            raise RuntimeError(
                "Structured output truncated at max_tokens — increase max_tokens for this call."
            )
        parsed = response.parsed_output
        if parsed is None:
            raise RuntimeError("Model response did not parse into the expected schema.")
        return parsed

    def draft(self, *, system: str, prompt: str, max_tokens: int = 64000) -> str:
        with self.client.beta.messages.stream(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_config={"effort": self.effort},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        ) as stream:
            message = stream.get_final_message()
        if message.stop_reason == "refusal":
            raise RefusalError(_refusal_message(message))
        return "".join(block.text for block in message.content if block.type == "text")


def _refusal_message(response) -> str:
    details = getattr(response, "stop_details", None)
    category = getattr(details, "category", None) if details else None
    explanation = getattr(details, "explanation", None) if details else None
    parts = ["The model declined this request"]
    if category:
        parts.append(f"(category: {category})")
    if explanation:
        parts.append(f"— {explanation}")
    return " ".join(parts)
