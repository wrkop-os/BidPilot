"""Audit log (FR-18, NFR-5): every agent call, tool call, and human action.

Append-only JSONL per run. Each LLM event records the model, a SHA-256 hash
of the full prompt, token usage, and duration, so any output is attributable
and the diligence trail exists (§14.1).
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import threading
from pathlib import Path
from typing import Any, Optional


def prompt_hash(*parts: str) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update(part.encode("utf-8", errors="replace"))
        h.update(b"\x00")
    return h.hexdigest()


class AuditLog:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def record(
        self,
        event: str,
        actor: str,
        *,
        stage: Optional[str] = None,
        model: Optional[str] = None,
        prompt_sha256: Optional[str] = None,
        tokens_in: Optional[int] = None,
        tokens_out: Optional[int] = None,
        duration_s: Optional[float] = None,
        detail: Any = None,
    ) -> None:
        entry = {
            "ts": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "event": event,
            "actor": actor,
            "stage": stage,
            "model": model,
            "prompt_sha256": prompt_sha256,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "duration_s": duration_s,
            "detail": detail,
        }
        line = json.dumps({k: v for k, v in entry.items() if v is not None}, default=str)
        with self._lock:
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")

    def human_action(self, gate: str, approved: bool, actor: str, notes: Optional[str] = None) -> None:
        self.record(
            "human_gate",
            actor=actor,
            detail={"gate": gate, "approved": approved, "notes": notes},
        )

    def entries(self) -> list[dict]:
        if not self.path.exists():
            return []
        with open(self.path, "r", encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]
