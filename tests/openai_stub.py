"""A real OpenAI-compatible chat-completions server, for exercising the
custom-LLM backend over actual HTTP.

The custom-LLM path (`BIDPILOT_CUSTOM_LLM_URL`) is how BidPilot runs with no
Anthropic key at all — self-hosted, fine-tuned, Azure, Bedrock-behind-a-shim,
Ollama, anything OpenAI-shaped. It was previously covered only by env-var
routing assertions, so none of the HTTP, parsing, or retry code had ever run.

This stub is deliberately NOT a fake router: it speaks the wire protocol, and
it answers by synthesizing an instance of whatever JSON Schema the backend
embedded in the system prompt. That makes it a contract test for the real
client rather than a restatement of it.

It can also be told to fail in the ways a self-hosted model actually fails —
connection resets, 503s, rate limits, prose-wrapped JSON, schema violations —
so the client's resilience is tested against the failure modes that matter.
"""

from __future__ import annotations

import json
import re
import threading
from typing import Any, Optional

import uvicorn
from fastapi import FastAPI, Request

# The backend appends the schema after this sentence; recovering it is how the
# stub knows what shape to answer with.
_SCHEMA_MARKER = "no prose, no markdown fences:"


class StubBehavior:
    """How the stub should misbehave, so resilience can be tested."""

    def __init__(self) -> None:
        self.fail_times: int = 0            # transient failures before succeeding
        self.fail_status: int = 503
        self.reset_connection: bool = False  # drop the socket instead of replying
        self.wrap_in_prose: bool = False     # ```json fences + chatter
        self.bad_json_once: bool = False     # first reply violates the schema
        self.content_filter: bool = False
        # Fill optional schema fields by property name. Used to make the stub
        # emit *cited* claims, so the citation gate can be tested from both
        # sides rather than only observed to fire.
        self.field_overrides: dict[str, Any] = {}
        # Emit optional properties too. Real models do, and the required-only
        # payload is unrealistically sparse — e.g. it omits section claims
        # entirely, which quietly skips the citation gate.
        self.fill_optional: bool = False
        self.calls: list[dict] = []
        self._seen_bad = False

    def reset(self) -> None:
        self.__init__()


def build_app(behavior: StubBehavior) -> FastAPI:
    app = FastAPI()

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request):
        from fastapi.responses import JSONResponse

        body = await request.json()
        behavior.calls.append(body)

        if behavior.fail_times > 0:
            behavior.fail_times -= 1
            if behavior.reset_connection:
                # Hard transport failure: no HTTP response at all.
                raise RuntimeError("connection reset by peer")
            return JSONResponse(
                status_code=behavior.fail_status,
                content={"error": {"message": "temporarily unavailable"}},
            )

        if behavior.content_filter:
            return JSONResponse({
                "choices": [{"message": {"content": ""}, "finish_reason": "content_filter"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 0},
            })

        system = ""
        for message in body.get("messages", []):
            if message.get("role") == "system":
                system = message.get("content") or ""
        schema = _extract_schema(system)

        if schema is None:
            content = "Drafted narrative from the custom model."
        else:
            if behavior.bad_json_once and not behavior._seen_bad:
                behavior._seen_bad = True
                content = json.dumps({"definitely": "not the schema"})
            else:
                content = json.dumps(_instantiate(
                    schema, schema, overrides=behavior.field_overrides,
                    fill_optional=behavior.fill_optional))
            if behavior.wrap_in_prose:
                content = f"Sure! Here you go:\n```json\n{content}\n```\nHope that helps."

        return JSONResponse({
            "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50},
        })

    return app


def _extract_schema(system: str) -> Optional[dict]:
    idx = system.find(_SCHEMA_MARKER)
    if idx < 0:
        return None
    raw = system[idx + len(_SCHEMA_MARKER):].strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


# -- minimal JSON Schema instantiation ---------------------------------------
# Enough of draft-2020 to satisfy Pydantic models: $ref/$defs, enums, unions,
# arrays with minItems, and the scalar types. Values are placeholders; the
# point is a payload that VALIDATES, so the real client's parsing runs.


def _resolve(node: dict, root: dict) -> dict:
    seen = 0
    while "$ref" in node and seen < 10:
        ref = node["$ref"]
        if not ref.startswith("#/"):
            return {}
        target: Any = root
        for part in ref[2:].split("/"):
            target = target.get(part, {}) if isinstance(target, dict) else {}
        node = target if isinstance(target, dict) else {}
        seen += 1
    return node


def _instantiate(node: dict, root: dict, depth: int = 0,
                 overrides: Optional[dict] = None,
                 fill_optional: bool = False) -> Any:
    node = _resolve(node or {}, root)
    if depth > 12:
        return None
    overrides = overrides or {}

    if "default" in node:
        return node["default"]
    if "const" in node:
        return node["const"]
    if node.get("enum"):
        return node["enum"][0]

    for key in ("anyOf", "oneOf", "allOf"):
        if node.get(key):
            options = [o for o in node[key]
                       if _resolve(o, root).get("type") != "null"]
            chosen = options[0] if options else node[key][0]
            return _instantiate(chosen, root, depth + 1, overrides, fill_optional)

    node_type = node.get("type")
    if isinstance(node_type, list):
        node_type = next((t for t in node_type if t != "null"), "string")

    if node_type == "object" or "properties" in node:
        out: dict[str, Any] = {}
        properties = node.get("properties") or {}
        required = node.get("required") or list(properties)
        wanted = list(properties) if fill_optional else list(required)
        wanted += [k for k in overrides if k in properties]
        for name in wanted:
            if name not in properties or name in out:
                continue
            if name in overrides:
                out[name] = overrides[name]
            else:
                out[name] = _instantiate(properties[name], root, depth + 1,
                                         overrides, fill_optional)
        return out
    if node_type == "array":
        items = node.get("items") or {}
        count = max(int(node.get("minItems") or 1), 1)
        return [_instantiate(items, root, depth + 1, overrides, fill_optional)
                for _ in range(count)]
    if node_type == "integer":
        return int(node.get("minimum", 1) or 1)
    if node_type == "number":
        return float(node.get("minimum", 1.0) or 1.0)
    if node_type == "boolean":
        return False
    if node_type == "null":
        return None
    return _placeholder_string(node)


def _placeholder_string(node: dict) -> str:
    fmt = node.get("format")
    if fmt == "date":
        return "2027-03-14"
    if fmt == "date-time":
        return "2027-03-14T14:00:00Z"
    if fmt == "uri":
        return "https://example.gov/notice"
    pattern = node.get("pattern")
    if pattern:
        literal = re.sub(r"[\^\$\\]", "", pattern)
        if literal.isalnum():
            return literal
    minimum = int(node.get("minLength") or 0)
    value = "stub"
    while len(value) < minimum:
        value += "x"
    return value


class StubServer:
    """Runs the stub on a real port, in a thread, for the duration of a test."""

    def __init__(self, behavior: Optional[StubBehavior] = None, port: int = 8791):
        self.behavior = behavior or StubBehavior()
        self.port = port
        self._server: Optional[uvicorn.Server] = None
        self._thread: Optional[threading.Thread] = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    def __enter__(self) -> "StubServer":
        config = uvicorn.Config(build_app(self.behavior), host="127.0.0.1",
                                port=self.port, log_level="error")
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        deadline = __import__("time").monotonic() + 20
        while not self._server.started and __import__("time").monotonic() < deadline:
            __import__("time").sleep(0.05)
        if not self._server.started:
            raise RuntimeError("stub OpenAI server did not start")
        return self

    def __exit__(self, *exc) -> None:
        if self._server:
            self._server.should_exit = True
        if self._thread:
            self._thread.join(timeout=10)
