"""Collect training examples from completed run directories.

Two sources, kept distinct via `kind`:
  - "capture":    raw (system, prompt, output) triples the router logged.
  - "preference": a captured section draft whose section file was later
    human-edited — (prompt, machine output, human-corrected output). These
    are the highest-value examples: they encode exactly what reviewers fix.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional


@dataclass
class Example:
    kind: str                    # capture | preference
    stage: Optional[str]
    tier: Optional[str]
    system: str
    prompt: str
    output: str
    corrected_output: Optional[str] = None
    run_dir: str = ""


@dataclass
class DatasetStats:
    runs_scanned: int = 0
    captures: int = 0
    preferences: int = 0
    by_stage: dict = field(default_factory=dict)

    def add(self, example: Example) -> None:
        if example.kind == "preference":
            self.preferences += 1
        else:
            self.captures += 1
        key = example.stage or "(unlabeled)"
        self.by_stage[key] = self.by_stage.get(key, 0) + 1


CAPTURE_NAME = "training_capture.jsonl"
_SECTION_ID_RE = re.compile(r'"section_id"\s*[:=]?\s*"([A-Za-z0-9_-]+)"|section_id[ =]+([A-Za-z0-9_-]+)')


def iter_run_dirs(root: Path) -> Iterable[Path]:
    """Run dirs are directories containing state.json (any nesting level 1)."""
    if (root / "state.json").exists():
        yield root
        return
    for child in sorted(root.iterdir()):
        if child.is_dir() and (child / "state.json").exists():
            yield child


def collect_runs(root: Path) -> tuple[list[Example], DatasetStats]:
    examples: list[Example] = []
    stats = DatasetStats()
    for run_dir in iter_run_dirs(root):
        stats.runs_scanned += 1
        examples.extend(_collect_one(run_dir, stats))
    return examples, stats


def _collect_one(run_dir: Path, stats: DatasetStats) -> list[Example]:
    capture_file = run_dir / CAPTURE_NAME
    if not capture_file.exists():
        return []
    human_edited = _human_edited_sections(run_dir)
    out: list[Example] = []
    for line in capture_file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        example = Example(
            kind="capture",
            stage=row.get("stage"),
            tier=row.get("tier"),
            system=row.get("system", ""),
            prompt=row.get("prompt", ""),
            output=row.get("output", ""),
            run_dir=str(run_dir),
        )
        # Writer captures whose section was later human-edited become
        # preference pairs: machine output vs the reviewer's final text.
        section_id = _section_id_of(example)
        if section_id and section_id in human_edited:
            example.kind = "preference"
            example.corrected_output = human_edited[section_id]
        out.append(example)
        stats.add(example)
    return out


def _human_edited_sections(run_dir: Path) -> dict[str, str]:
    """section_id -> current on-disk text, for sections marked human_edited."""
    state_file = run_dir / "state.json"
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except Exception:
        return {}
    edited: dict[str, str] = {}
    for draft in state.get("section_drafts") or []:
        if not draft.get("human_edited"):
            continue
        sid = draft.get("section_id")
        section_file = run_dir / "volumes" / "sections" / f"{sid}.md"
        if sid and section_file.exists():
            edited[sid] = section_file.read_text(encoding="utf-8")
    return edited


def _section_id_of(example: Example) -> Optional[str]:
    if not (example.stage or "").startswith(("produce.write", "writer")):
        return None
    for text in (example.output, example.prompt):
        m = _SECTION_ID_RE.search(text or "")
        if m:
            return m.group(1) or m.group(2)
    return None
