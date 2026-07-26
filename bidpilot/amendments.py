"""Amendment lifecycle (FR-4, Phase 4 week 25): diff engine + "what changed /
what to re-review" report.

When `bidpilot amend` runs, the current DocTree is archived before downstream
invalidation. After the re-run parses the amended documents, the diff engine
compares old vs. new per document (deterministic unified diff) and a frontier
model summarizes what changed and which proposal artifacts need re-review.
"""

from __future__ import annotations

import difflib
import json
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from .models import DocTree
from .routing import ModelRouter, Tier

ARCHIVE_NAME = "doc_tree_pre_amendment.json"

SUMMARY_SYSTEM = """You summarize what changed in an amended federal solicitation,
for a proposal team mid-draft. From the unified diffs provided, produce:
- changes: each material change in plain language (deadline moves, scope
  additions/deletions, page-limit changes, new forms, Q&A answers that alter
  requirements)
- re_review: which proposal artifacts each change forces the team to re-check
  (compliance matrix rows, specific volumes, pricing, submission sheet)
- unchanged_note: state plainly if the diffs are only administrative.
Never invent changes not visible in the diffs."""


class AmendmentChange(BaseModel):
    description: str
    impact: str = Field(description="What it forces the team to re-review")
    severity: str = Field(description="material | administrative")


class AmendmentReport(BaseModel):
    changes: list[AmendmentChange] = Field(default_factory=list)
    re_review: list[str] = Field(default_factory=list)
    unchanged_note: Optional[str] = None


def archive_doc_tree(run_dir: Path, doc_tree: DocTree) -> None:
    (run_dir / ARCHIVE_NAME).write_text(doc_tree.model_dump_json(), encoding="utf-8")


def load_archived_doc_tree(run_dir: Path) -> Optional[DocTree]:
    path = run_dir / ARCHIVE_NAME
    if not path.exists():
        return None
    return DocTree.model_validate_json(path.read_text(encoding="utf-8"))


def diff_doc_trees(old: DocTree, new: DocTree, context_lines: int = 3) -> dict[str, str]:
    """Per-document unified diffs (deterministic). Also reports added/removed
    documents. Returns {doc name: diff text} for docs that changed."""
    old_docs = {d.name: d.full_text for d in old.docs}
    new_docs = {d.name: d.full_text for d in new.docs}
    diffs: dict[str, str] = {}

    for name in sorted(set(old_docs) | set(new_docs)):
        old_text = old_docs.get(name)
        new_text = new_docs.get(name)
        if old_text is None:
            diffs[name] = f"[NEW DOCUMENT] {name} added by amendment."
            continue
        if new_text is None:
            diffs[name] = f"[REMOVED] {name} no longer present."
            continue
        if old_text == new_text:
            continue
        diff = "\n".join(
            difflib.unified_diff(
                old_text.splitlines(), new_text.splitlines(),
                fromfile=f"{name} (before)", tofile=f"{name} (after)",
                n=context_lines, lineterm="",
            )
        )
        # Bound huge diffs; the summary model needs the changes, not the world.
        diffs[name] = diff[:120_000]
    return diffs


def summarize_amendment(router: ModelRouter, diffs: dict[str, str]) -> AmendmentReport:
    if not diffs:
        return AmendmentReport(unchanged_note="No textual changes detected between versions.")
    body = "\n\n".join(f"=== {name} ===\n{diff}" for name, diff in diffs.items())
    return router.structured(
        Tier.FRONTIER,
        system=SUMMARY_SYSTEM,
        prompt=f"Summarize this amendment.\n\n{body[:400_000]}",
        output_type=AmendmentReport,
        stage="amendment.summary",
    )


def report_to_markdown(report: AmendmentReport, diffs: dict[str, str]) -> str:
    lines = ["# Amendment Report — what changed / what to re-review", ""]
    if report.unchanged_note:
        lines.append(report.unchanged_note)
    for change in report.changes:
        marker = "🔴" if change.severity == "material" else "▫️"
        lines.append(f"- {marker} {change.description}")
        lines.append(f"  - re-review: {change.impact}")
    if report.re_review:
        lines += ["", "## Re-review checklist"] + [f"- [ ] {r}" for r in report.re_review]
    if diffs:
        lines += ["", "## Changed documents"] + [f"- {name}" for name in diffs]
        lines.append("\n(Full diffs: `amendment_diffs.json`)")
    return "\n".join(lines)


def run_amendment_diff(router: ModelRouter, run_dir: Path, new_tree: DocTree) -> Optional[AmendmentReport]:
    """Called after docproc on a post-amendment re-run. Writes the report
    artifacts and returns the report (None when there was no archived tree)."""
    old_tree = load_archived_doc_tree(run_dir)
    if old_tree is None:
        return None
    diffs = diff_doc_trees(old_tree, new_tree)
    report = summarize_amendment(router, diffs)
    (run_dir / "amendment_diffs.json").write_text(json.dumps(diffs, indent=2), encoding="utf-8")
    (run_dir / "AMENDMENT_REPORT.md").write_text(report_to_markdown(report, diffs), encoding="utf-8")
    (run_dir / ARCHIVE_NAME).unlink(missing_ok=True)
    return report
