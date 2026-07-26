"""Model routing (PRD §6.2.4): frontier models where judgment matters,
fast/cheap models where volume lives. This is where 80% of cost control is.

Tiers:
  FAST     — extraction, classification, dedup at scale (Haiku-class)
  FRONTIER — solution architecture, writing, red-team, pricing judgment

All calls flow through this module so the audit log captures every model
invocation with prompt hash and token usage (FR-18, FR-22).
"""

from __future__ import annotations

import os
import time
from enum import Enum
from typing import Optional, TypeVar

import anthropic
from pydantic import BaseModel

from .audit import AuditLog, prompt_hash

FRONTIER_MODEL = os.environ.get("BIDPILOT_FRONTIER_MODEL", "claude-opus-5")
FAST_MODEL = os.environ.get("BIDPILOT_FAST_MODEL", "claude-haiku-4-5")

# Haiku's context window is 200K; cap what we hand to fast-tier calls.
FAST_TIER_MAX_PROMPT_CHARS = 500_000

T = TypeVar("T", bound=BaseModel)


class Tier(str, Enum):
    FAST = "fast"
    FRONTIER = "frontier"


class RefusalError(RuntimeError):
    """Model safety classifiers declined the request."""


class ModelRouter:
    def __init__(self, audit: Optional[AuditLog] = None, effort: str = "high"):
        self.client = anthropic.Anthropic()
        self.audit = audit
        self.effort = effort

    def model_for(self, tier: Tier) -> str:
        return FAST_MODEL if tier == Tier.FAST else FRONTIER_MODEL

    # -- structured extraction/analysis ---------------------------------------

    def structured(
        self,
        tier: Tier,
        *,
        system: str,
        prompt: str,
        output_type: type[T],
        max_tokens: int = 16000,
        stage: Optional[str] = None,
    ) -> T:
        model = self.model_for(tier)
        if tier == Tier.FAST:
            prompt = prompt[:FAST_TIER_MAX_PROMPT_CHARS]
        start = time.monotonic()
        response = self.client.messages.parse(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_format=output_type,
        )
        self._audit(model, system, prompt, response, stage, time.monotonic() - start)
        if response.stop_reason == "refusal":
            raise RefusalError(_refusal_message(response))
        if response.stop_reason == "max_tokens":
            raise RuntimeError(f"Structured output truncated at max_tokens={max_tokens} ({stage}).")
        if response.parsed_output is None:
            raise RuntimeError(f"Response did not parse into {output_type.__name__} ({stage}).")
        return response.parsed_output

    # -- long-form drafting ----------------------------------------------------

    def draft(
        self,
        *,
        system: str,
        prompt: str,
        max_tokens: int = 64000,
        stage: Optional[str] = None,
    ) -> str:
        """Frontier-only long-form generation, streamed, with server-side
        refusal fallbacks enabled."""
        start = time.monotonic()
        with self.client.beta.messages.stream(
            model=FRONTIER_MODEL,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_config={"effort": self.effort},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        ) as stream:
            message = stream.get_final_message()
        self._audit(FRONTIER_MODEL, system, prompt, message, stage, time.monotonic() - start)
        if message.stop_reason == "refusal":
            raise RefusalError(_refusal_message(message))
        return "".join(block.text for block in message.content if block.type == "text")

    # -- internals -------------------------------------------------------------

    def _audit(self, model, system, prompt, response, stage, duration) -> None:
        if not self.audit:
            return
        usage = getattr(response, "usage", None)
        self.audit.record(
            "llm_call",
            actor="model",
            stage=stage,
            model=getattr(response, "model", model),
            prompt_sha256=prompt_hash(system, prompt),
            tokens_in=getattr(usage, "input_tokens", None),
            tokens_out=getattr(usage, "output_tokens", None),
            duration_s=round(duration, 2),
        )


def _refusal_message(response) -> str:
    details = getattr(response, "stop_details", None)
    category = getattr(details, "category", None) if details else None
    explanation = getattr(details, "explanation", None) if details else None
    msg = "The model declined this request"
    if category:
        msg += f" (category: {category})"
    if explanation:
        msg += f" — {explanation}"
    return msg
