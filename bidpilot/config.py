"""Local configuration loading.

One job: make a `.env` file in the project root actually take effect, without
adding a dependency. Secrets live in `.env` (gitignored) so a key is never
pasted into a tracked file.

Precedence is deliberate: **an already-set environment variable always wins.**
CI, a container, and an explicit `export` must never be silently overridden by
a stale file someone forgot on disk.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

ENV_FILENAME = ".env"


def load_dotenv(start: Optional[Path] = None) -> list[str]:
    """Load `.env` from `start` or the nearest ancestor. Returns the names of
    the variables actually set (never their values — these are secrets)."""
    path = find_dotenv(start)
    if path is None:
        return []
    applied: list[str] = []
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        if not key or key in os.environ:      # already set wins
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        os.environ[key] = value
        applied.append(key)
    return applied


def find_dotenv(start: Optional[Path] = None) -> Optional[Path]:
    here = (start or Path.cwd()).resolve()
    for directory in [here, *here.parents]:
        candidate = directory / ENV_FILENAME
        if candidate.is_file():
            return candidate
    return None
