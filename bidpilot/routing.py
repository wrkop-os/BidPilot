"""Model routing (PRD §6.2.4): frontier models where judgment matters,
fast/cheap models where volume lives. This is where 80% of cost control is.

Tiers:
  FAST     — extraction, classification, dedup at scale (Haiku-class)
  FRONTIER — solution architecture, writing, red-team, pricing judgment

All calls flow through this module so the audit log captures every model
invocation with prompt hash and token usage (FR-18, FR-22).

Custom-LLM serving (MLE path): setting BIDPILOT_CUSTOM_LLM_URL routes calls
to any OpenAI-compatible chat endpoint (vLLM, TGI, Ollama, a fine-tuned
deployment) instead of the Anthropic API. BIDPILOT_CUSTOM_LLM_TIERS picks
which tiers move over ("fast", "frontier", or "all") so a distilled model
can take volume work while the frontier keeps judgment calls — or run the
whole backend. Setting BIDPILOT_CAPTURE_TRAINING_DATA=1 writes every
(system, prompt, output) triple to training_capture.jsonl beside the audit
log; `bidpilot mle` turns those captures into fine-tuning datasets.
"""

from __future__ import annotations

import json
import os
import re
import time
from enum import Enum
from pathlib import Path
from typing import Optional, TypeVar

import httpx
from pydantic import BaseModel

from .audit import AuditLog, prompt_hash

FRONTIER_MODEL = os.environ.get("BIDPILOT_FRONTIER_MODEL", "claude-opus-5")
FAST_MODEL = os.environ.get("BIDPILOT_FAST_MODEL", "claude-haiku-4-5")

# Haiku's context window is 200K; cap what we hand to fast-tier calls.
FAST_TIER_MAX_PROMPT_CHARS = 500_000

T = TypeVar("T", bound=BaseModel)


# Prompt caching. Eleven stages read the same solicitation corpus, so on a
# measured run the corpus was sent about thirteen times: 34K chars of source
# became 468K chars on the wire, a 14x amplification that is pure repetition.
#
# Passing that shared text as `cache_prefix` puts it in its own leading content
# block marked cacheable. Anthropic then charges it once at a write premium and
# roughly a tenth of the input rate on every later read inside the TTL, which a
# sequential pipeline run comfortably fits inside.
#
# Below the provider's minimum cacheable size the marker is simply ignored and
# billing is normal, so short corpora lose nothing.
MIN_CACHEABLE_CHARS = 4_000


def _user_content(prompt: str, cache_prefix: Optional[str]):
    """One user turn, with the shared prefix isolated so it can be cached.

    The prefix MUST be byte-identical across calls or every one is a cache
    miss, which is why callers pass the corpus itself rather than a
    per-agent string that merely contains it.
    """
    if not cache_prefix:
        return prompt
    if len(cache_prefix) < MIN_CACHEABLE_CHARS:
        return f"{cache_prefix}\n\n{prompt}"     # too small to be worth a block
    return [
        {"type": "text", "text": cache_prefix,
         "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": prompt},
    ]


class Tier(str, Enum):
    FAST = "fast"
    FRONTIER = "frontier"


class RefusalError(RuntimeError):
    """Model safety classifiers declined the request."""


class MissingCredentialsError(RuntimeError):
    """No usable model backend is configured — actionable, not a stack trace."""


class CustomBackendError(RuntimeError):
    """The custom model endpoint is configured but unusable. Names the URL and
    the reason, because a typo'd BIDPILOT_CUSTOM_LLM_URL is the likeliest
    first-run mistake on this path."""


class CustomLLMBackend:
    """OpenAI-compatible chat-completions client for self-hosted or
    fine-tuned models. Structured output is enforced by schema-in-prompt +
    parse-with-one-retry (JSON mode requested when the server honors it)."""

    # Self-hosted inference restarts, autoscales, and rate-limits. Without
    # retries a single blip destroys a run that is nine stages deep and has
    # already cost real money, so this path gets the same NFR-3 resilience the
    # SAM.gov client has: retry connection errors, 429 and 5xx with exponential
    # backoff, honor Retry-After, and never retry a 4xx that will not change.
    MAX_RETRIES = 3
    BACKOFF_BASE_S = 1.0
    BACKOFF_CAP_S = 30.0

    def __init__(self, base_url: str, model: str, api_key: str = "",
                 timeout: float = 300.0, transport: Optional[httpx.BaseTransport] = None):
        self.base_url = base_url.rstrip("/")
        self.model = model
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._http = httpx.Client(timeout=timeout, headers=headers, transport=transport)
        self._sleep = time.sleep      # injectable so tests do not actually wait

    def chat(self, system: str, prompt: str, max_tokens: int, json_mode: bool = False) -> dict:
        payload = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        url = f"{self.base_url}/chat/completions"

        last_exc: Optional[Exception] = None
        for attempt in range(self.MAX_RETRIES + 1):
            try:
                resp = self._http.post(url, json=payload)
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last_exc = exc
                if attempt < self.MAX_RETRIES:
                    self._backoff(attempt, None)
                    continue
                raise CustomBackendError(
                    f"Could not reach the custom model at {self.base_url} after "
                    f"{self.MAX_RETRIES + 1} attempts ({type(exc).__name__}). "
                    "Check BIDPILOT_CUSTOM_LLM_URL and that the server is up."
                ) from exc
            if resp.status_code == 429 or resp.status_code >= 500:
                last_exc = httpx.HTTPStatusError(
                    f"{resp.status_code} from the custom model",
                    request=resp.request, response=resp,
                )
                if attempt < self.MAX_RETRIES:
                    self._backoff(attempt, resp.headers.get("retry-after"))
                    continue
            if resp.status_code >= 400:
                raise CustomBackendError(
                    f"Custom model returned HTTP {resp.status_code} from "
                    f"{self.base_url}: {resp.text[:200]}"
                )
            return resp.json()
        raise CustomBackendError(str(last_exc))      # pragma: no cover — loop always returns/raises

    def _backoff(self, attempt: int, retry_after: Optional[str]) -> None:
        delay = min(self.BACKOFF_BASE_S * (2 ** attempt), self.BACKOFF_CAP_S)
        if retry_after:
            try:
                delay = max(delay, float(retry_after))
            except ValueError:
                pass
        self._sleep(delay)

    def complete(self, system: str, prompt: str, max_tokens: int) -> tuple[str, dict]:
        data = self.chat(system, prompt, max_tokens)
        choice = data["choices"][0]
        if choice.get("finish_reason") == "content_filter":
            raise RefusalError("The custom model declined this request (content_filter).")
        return choice["message"]["content"] or "", data.get("usage") or {}

    def structured(self, system: str, prompt: str, output_type: type[T], max_tokens: int) -> tuple[T, dict]:
        schema = json.dumps(output_type.model_json_schema())
        sys_full = (
            f"{system}\n\nRespond ONLY with a JSON object valid against this "
            f"JSON Schema — no prose, no markdown fences:\n{schema}"
        )
        last_err = None
        attempt_prompt = prompt
        for _ in range(2):  # one retry with the validation error fed back
            data = self.chat(sys_full, attempt_prompt, max_tokens, json_mode=True)
            choice = data["choices"][0]
            if choice.get("finish_reason") == "content_filter":
                raise RefusalError("The custom model declined this request (content_filter).")
            text = _strip_fences(choice["message"]["content"] or "")
            try:
                return output_type.model_validate_json(text), data.get("usage") or {}
            except Exception as exc:  # pydantic.ValidationError or JSON decode
                last_err = exc
                attempt_prompt = (
                    f"{prompt}\n\nYour previous response failed validation with:\n{exc}\n"
                    "Return corrected JSON only."
                )
        raise RuntimeError(f"Custom model output failed {output_type.__name__} validation: {last_err}")


_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)```", re.DOTALL | re.IGNORECASE)


def _strip_fences(text: str) -> str:
    """Recover the JSON object from whatever a model actually returned.

    Frontier models honor "no prose, no markdown fences". Small and local
    models — precisely the ones people point BIDPILOT_CUSTOM_LLM_URL at —
    reliably answer with "Sure! Here you go:" and a fenced block, and used to
    fail every schema parse on this path. Being strict here does not make
    those models behave; it just makes the keyless route unusable.

    Tried in order of trustworthiness: the whole string, a fenced block
    anywhere in it, then the first balanced brace span.
    """
    text = (text or "").strip()
    if not text:
        return text
    if text[:1] in "{[":
        return text

    fenced = _FENCE_RE.search(text)
    if fenced:
        inner = fenced.group(1).strip()
        if inner:
            return inner

    # Unfenced prose around a bare object: find the first balanced {...},
    # respecting strings and escapes so a brace inside a value cannot end it.
    start = text.find("{")
    if start < 0:
        return text
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        char = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return text


def custom_backend_from_env() -> tuple[Optional[CustomLLMBackend], set[Tier]]:
    url = os.environ.get("BIDPILOT_CUSTOM_LLM_URL")
    if not url:
        return None, set()
    backend = CustomLLMBackend(
        base_url=url,
        model=os.environ.get("BIDPILOT_CUSTOM_LLM_MODEL", "bidpilot-custom"),
        api_key=os.environ.get("BIDPILOT_CUSTOM_LLM_API_KEY", ""),
    )
    tiers_raw = os.environ.get("BIDPILOT_CUSTOM_LLM_TIERS", "all").lower()
    tiers = {Tier.FAST, Tier.FRONTIER} if tiers_raw == "all" else {
        Tier(t.strip()) for t in tiers_raw.split(",") if t.strip() in ("fast", "frontier")
    }
    return backend, tiers


class ModelRouter:
    def __init__(self, audit: Optional[AuditLog] = None, effort: str = "high"):
        self.audit = audit
        self.effort = effort
        self._client = None  # Anthropic client, built lazily so custom-only
        # deployments never need an ANTHROPIC_API_KEY.
        self.custom, self.custom_tiers = custom_backend_from_env()
        self.capture_path: Optional[Path] = None
        if os.environ.get("BIDPILOT_CAPTURE_TRAINING_DATA") == "1" and audit is not None:
            self.capture_path = audit.path.parent / "training_capture.jsonl"

    @property
    def client(self):
        if self._client is None:
            import anthropic

            # The SDK's own error for this is a TypeError about header
            # resolution, raised mid-run after intake and docproc have already
            # done real work. Fail with something a human can act on instead.
            if not (os.environ.get("ANTHROPIC_API_KEY")
                    or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
                raise MissingCredentialsError(
                    "No Anthropic credentials. Set ANTHROPIC_API_KEY in .env "
                    "(or export ANTHROPIC_AUTH_TOKEN, or run `ant auth login`).\n"
                    "To run entirely against your own model instead, set "
                    "BIDPILOT_CUSTOM_LLM_URL (and optionally _MODEL, _API_KEY, "
                    "_TIERS) — no Anthropic key is needed then.\n"
                    "`bidpilot doctor` checks both."
                )
            self._client = anthropic.Anthropic()
        return self._client

    def model_for(self, tier: Tier) -> str:
        if self._uses_custom(tier):
            return f"custom:{self.custom.model}"
        return FAST_MODEL if tier == Tier.FAST else FRONTIER_MODEL

    def _uses_custom(self, tier: Tier) -> bool:
        return self.custom is not None and tier in self.custom_tiers

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
        cache_prefix: Optional[str] = None,
    ) -> T:
        if tier == Tier.FAST:
            budget = FAST_TIER_MAX_PROMPT_CHARS - len(cache_prefix or "")
            prompt = prompt[:max(budget, 0)]
        start = time.monotonic()
        # Everything the model sees, for audit and capture. The split is a
        # billing optimization; the recorded prompt stays whole so a prompt
        # hash still identifies the actual request.
        full_prompt = f"{cache_prefix}\n\n{prompt}" if cache_prefix else prompt
        try:
            if self._uses_custom(tier):
                parsed, usage = self.custom.structured(system, full_prompt, output_type, max_tokens)
                self._audit_raw(self.model_for(tier), system, full_prompt, usage, stage,
                                time.monotonic() - start)
                self._capture(stage, tier, system, full_prompt, parsed.model_dump_json())
                return parsed
            model = self.model_for(tier)
            response = self.client.messages.parse(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user",
                           "content": _user_content(prompt, cache_prefix)}],
                output_format=output_type,
            )
            self._audit(model, system, full_prompt, response, stage, time.monotonic() - start)
            if response.stop_reason == "refusal":
                raise RefusalError(_refusal_message(response))
            if response.stop_reason == "max_tokens":
                raise RuntimeError(f"Structured output truncated at max_tokens={max_tokens} ({stage}).")
            if response.parsed_output is None:
                raise RuntimeError(f"Response did not parse into {output_type.__name__} ({stage}).")
            self._capture(stage, tier, system, full_prompt,
                          response.parsed_output.model_dump_json())
            return response.parsed_output
        except Exception as exc:
            self._audit_failure(self.model_for(tier), stage, exc, time.monotonic() - start)
            raise

    # -- long-form drafting ----------------------------------------------------

    def draft(
        self,
        *,
        system: str,
        prompt: str,
        max_tokens: int = 64000,
        stage: Optional[str] = None,
        cache_prefix: Optional[str] = None,
    ) -> str:
        """Frontier-tier long-form generation."""
        start = time.monotonic()
        full_prompt = f"{cache_prefix}\n\n{prompt}" if cache_prefix else prompt
        try:
            if self._uses_custom(Tier.FRONTIER):
                text, usage = self.custom.complete(system, full_prompt, max_tokens)
                self._audit_raw(self.model_for(Tier.FRONTIER), system, full_prompt, usage, stage,
                                time.monotonic() - start)
                self._capture(stage, Tier.FRONTIER, system, full_prompt, text)
                return text
            with self.client.beta.messages.stream(
                model=FRONTIER_MODEL,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user",
                           "content": _user_content(prompt, cache_prefix)}],
                output_config={"effort": self.effort},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            ) as stream:
                message = stream.get_final_message()
            self._audit(FRONTIER_MODEL, system, full_prompt, message, stage, time.monotonic() - start)
            if message.stop_reason == "refusal":
                raise RefusalError(_refusal_message(message))
            text = "".join(block.text for block in message.content if block.type == "text")
            self._capture(stage, Tier.FRONTIER, system, full_prompt, text)
            return text
        except Exception as exc:
            self._audit_failure(self.model_for(Tier.FRONTIER), stage, exc, time.monotonic() - start)
            raise

    # -- vision (OCR fallback for image-only pages) ----------------------------

    def transcribe_image(
        self,
        image_bytes: bytes,
        media_type: str = "image/png",
        *,
        stage: Optional[str] = None,
    ) -> str:
        """Transcribe one scanned page image to text (fast tier — volume work).
        Vision stays on the Anthropic API even when a custom text model serves
        the fast tier; point BIDPILOT_CUSTOM_LLM_TIERS at a vision-capable
        deployment before moving OCR."""
        import base64

        model = FAST_MODEL
        start = time.monotonic()
        response = self.client.messages.create(
            model=model,
            max_tokens=8000,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": base64.standard_b64encode(image_bytes).decode(),
                            },
                        },
                        {
                            "type": "text",
                            "text": (
                                "Transcribe this scanned solicitation page verbatim. "
                                "Preserve tables as pipe-delimited rows. Output only the "
                                "transcription — no commentary."
                            ),
                        },
                    ],
                }
            ],
        )
        self._audit(model, "ocr", f"<image {len(image_bytes)}B>", response, stage, time.monotonic() - start)
        if response.stop_reason == "refusal":
            raise RefusalError(_refusal_message(response))
        return "".join(block.text for block in response.content if block.type == "text")

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
            # Cached input is billed at a different rate; recording it
            # separately is what makes the saving visible in `bidpilot costs`.
            cache_write_tokens=getattr(usage, "cache_creation_input_tokens", None),
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", None),
            duration_s=round(duration, 2),
        )

    def _audit_failure(self, model, stage, exc: Exception, duration: float) -> None:
        """Failed calls leave a trace too — retry/failure rates per stage are
        first-class cost telemetry, not invisible noise."""
        if not self.audit:
            return
        self.audit.record(
            "llm_call_failed",
            actor="model",
            stage=stage,
            model=model,
            detail=type(exc).__name__,
            duration_s=round(duration, 2),
        )

    def _audit_raw(self, model, system, prompt, usage: dict, stage, duration) -> None:
        if not self.audit:
            return
        self.audit.record(
            "llm_call",
            actor="model",
            stage=stage,
            model=model,
            prompt_sha256=prompt_hash(system, prompt),
            tokens_in=usage.get("prompt_tokens"),
            tokens_out=usage.get("completion_tokens"),
            duration_s=round(duration, 2),
        )

    def _capture(self, stage, tier, system, prompt, output: str) -> None:
        if not self.capture_path:
            return
        with open(self.capture_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "stage": stage,
                "tier": tier.value if isinstance(tier, Tier) else str(tier),
                "system": system,
                "prompt": prompt,
                "output": output,
                "ts": time.time(),
            }) + "\n")


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
