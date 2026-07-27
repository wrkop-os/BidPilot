"""Reviewer edit loop (A13): per-section draft files that round-trip.

The merged per-volume markdown is convenient to read but ambiguous to
re-import, so every section is ALSO written as its own file under
`volumes/sections/<section_id>.md` — the canonical edit surface.

Clobber protection: a sidecar (`.written.json`) records the SHA-256 of what
BidPilot last wrote per file. If the file on disk no longer matches, a human
edited it and hasn't synced — the pipeline never overwrites it; the edit is
surfaced as a warning until `bidpilot sync-drafts` imports it into state.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

SECTIONS_DIR = ("volumes", "sections")
SIDECAR_NAME = ".written.json"

_ADDRESSES_RE = re.compile(r"<!--\s*addresses\s+([^>]+?)\s*-->")


def _sections_dir(run_dir: Path) -> Path:
    return run_dir.joinpath(*SECTIONS_DIR)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _safe_name(section_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", section_id) + ".md"


def _load_sidecar(directory: Path) -> dict[str, str]:
    path = directory / SIDECAR_NAME
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _save_sidecar(directory: Path, data: dict[str, str]) -> None:
    (directory / SIDECAR_NAME).write_text(json.dumps(data, indent=2), encoding="utf-8")


def extract_addressed(markdown: str) -> list[str]:
    ids: list[str] = []
    for match in _ADDRESSES_RE.findall(markdown):
        for token in re.split(r"[,;]\s*", match.strip()):
            if token and token not in ids:
                ids.append(token)
    return ids


# ---------------------------------------------------------------------------
# Write (pipeline -> disk), preserving unsynced human edits
# ---------------------------------------------------------------------------


def write_section_files(state) -> list[str]:
    """Write per-section files; NEVER overwrite an unsynced human edit.
    Returns warnings for files preserved because they hold unsynced edits."""
    if not state.section_drafts:
        return []
    directory = _sections_dir(Path(state.run_dir))
    directory.mkdir(parents=True, exist_ok=True)
    sidecar = _load_sidecar(directory)
    warnings: list[str] = []

    for draft in state.section_drafts:
        name = _safe_name(draft.section_id)
        path = directory / name
        if path.exists():
            on_disk = path.read_text(encoding="utf-8")
            recorded = sidecar.get(name)
            if recorded is not None and _sha(on_disk) != recorded and on_disk != draft.markdown:
                warnings.append(
                    f"{path} has unsynced human edits — preserved on disk; run "
                    "`bidpilot sync-drafts` to import them before re-running stages"
                )
                continue
        path.write_text(draft.markdown, encoding="utf-8")
        sidecar[name] = _sha(draft.markdown)

    _save_sidecar(directory, sidecar)
    return warnings


# ---------------------------------------------------------------------------
# Sync (disk -> state): import reviewer edits
# ---------------------------------------------------------------------------


@dataclass
class SyncResult:
    updated: list[str] = field(default_factory=list)
    unchanged: int = 0
    missing_files: list[str] = field(default_factory=list)


def sync_drafts(state) -> SyncResult:
    """Import edited section files into state.section_drafts. Updated sections
    are marked human_edited; word counts and `<!-- addresses -->` markers are
    re-derived; the sidecar is refreshed so files count as synced."""
    result = SyncResult()
    directory = _sections_dir(Path(state.run_dir))
    if not directory.exists():
        result.missing_files = [str(directory)]
        return result
    sidecar = _load_sidecar(directory)

    for draft in state.section_drafts:
        name = _safe_name(draft.section_id)
        path = directory / name
        if not path.exists():
            result.missing_files.append(name)
            continue
        on_disk = path.read_text(encoding="utf-8")
        if on_disk == draft.markdown:
            result.unchanged += 1
            sidecar[name] = _sha(on_disk)
            continue
        draft.markdown = on_disk
        draft.word_count = len(on_disk.split())
        merged = list(draft.addressed_requirements)
        for req_id in extract_addressed(on_disk):
            if req_id not in merged:
                merged.append(req_id)
        draft.addressed_requirements = merged
        draft.human_edited = True
        sidecar[name] = _sha(on_disk)
        result.updated.append(draft.section_id)

    _save_sidecar(directory, sidecar)
    return result
