"""Knowledge operations for the Company KB — the loop that makes the system
smarter between runs.

Two deterministic passes (no LLM: invariant 3):

  GAP MINING   every run records exactly what the KB could not answer —
               `[NEEDS INPUT]` claims and uncited-claim QA findings. Mining
               those across runs turns "the draft had holes" into a ranked
               shopping list: add THIS fact and N future proposals improve.

  HEALTH       the KB itself is knowledge that rots. Duplicate records split
               citations, stale entries carry last year's rates into this
               year's bid, thin past-performance records cannot support
               analogy estimating, and broken cross-references cite entries
               that do not exist.

Both are read-only reporting: nothing here edits the KB. A human owns every
company fact (FR-10 governance).
"""

from __future__ import annotations

import datetime as _dt
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .store import KnowledgeBase

# Same normalization the eval harness uses, for consistent similarity.
_WORD_RE = re.compile(r"[^a-z0-9 ]")
DUPLICATE_THRESHOLD = 0.72
# Gaps are free-text asks whose phrasing varies run to run; cluster them a
# little more eagerly than KB records, where a false merge hides a record.
GAP_CLUSTER_THRESHOLD = 0.65
THIN_NARRATIVE_CHARS = 120

# Function words carry no topical signal but dominate short strings — two
# phrasings of one missing fact must not read as two facts.
_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in",
    "is", "it", "its", "of", "on", "or", "our", "plus", "that", "the", "their",
    "this", "to", "we", "with",
}


def _norm(text: str) -> set[str]:
    words = _WORD_RE.sub("", " ".join((text or "").lower().split())).split()
    return {w for w in words if w not in _STOPWORDS}


def _overlap(a: str, b: str) -> float:
    ta, tb = _norm(a), _norm(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


# -- gap mining ---------------------------------------------------------------


@dataclass
class KBGap:
    kind: str                      # needs_input | uncited
    detail: str
    count: int = 1
    runs: list[str] = field(default_factory=list)
    sections: list[str] = field(default_factory=list)

    @property
    def priority(self) -> str:
        if self.kind == "uncited":
            return "HIGH"          # blocked an export
        return "MEDIUM" if self.count > 1 else "LOW"


def mine_gaps(output_root: Path, similarity: float = GAP_CLUSTER_THRESHOLD) -> list[KBGap]:
    """Scan every run for knowledge the KB could not supply, clustering
    near-identical asks so a fact requested by five runs reads as one item
    worth five runs."""
    gaps: list[KBGap] = []
    if not output_root.is_dir():
        return gaps
    for run_dir in sorted(output_root.iterdir()):
        state_file = run_dir / "state.json"
        if not (run_dir.is_dir() and state_file.exists()):
            continue
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        run_id = state.get("run_id") or run_dir.name
        for draft in state.get("section_drafts") or []:
            section = draft.get("section_id") or "?"
            for claim in draft.get("claims") or []:
                text = _describe(claim.get("text"))
                if not text:
                    continue
                if claim.get("needs_input"):
                    _absorb(gaps, KBGap("needs_input", text, 1, [run_id], [section]), similarity)
                elif not claim.get("kb_source_id"):
                    _absorb(gaps, KBGap("uncited", text, 1, [run_id], [section]), similarity)

        # QA findings carry the fuller description of the same gap (a claim's
        # text is often just the bare marker). Clustering merges the two.
        for finding in (state.get("qa_report") or {}).get("findings") or []:
            if finding.get("category") != "citation":
                continue
            text = _describe(finding.get("description"))
            if not text:
                continue
            kind = "needs_input" if "NEEDS INPUT" in (finding.get("description") or "") \
                else "uncited"
            section = finding.get("location") or "?"
            _absorb(gaps, KBGap(kind, text, 1, [run_id], [section]), similarity)
    gaps.sort(key=lambda g: (g.kind != "uncited", -g.count, g.detail))
    return gaps


_MARKER_RE = re.compile(r"\[NEEDS INPUT:?\s*(?P<detail>[^\]]*)\]", re.IGNORECASE)
_NOISE_PREFIX_RE = re.compile(
    r"^(?:\[NEEDS INPUT\]\s*outstanding:\s*|Unresolved marker in prose:\s*)",
    re.IGNORECASE,
)


def _describe(raw: Optional[str]) -> str:
    """The *fact that is missing*, not the marker announcing it.

    A claim whose text is a bare `[NEEDS INPUT]` names nothing actionable; the
    useful description lives inside the marker or in the QA finding that
    reported it. Returns '' when there is nothing worth putting in a report.
    """
    text = (raw or "").strip()
    if not text:
        return ""
    text = _NOISE_PREFIX_RE.sub("", text).strip()
    marker = _MARKER_RE.search(text)
    if marker:
        inner = marker.group("detail").strip()
        text = inner if inner else _MARKER_RE.sub("", text).strip()
    return text if len(text) >= 8 else ""


def _absorb(gaps: list[KBGap], new: KBGap, similarity: float) -> None:
    for existing in gaps:
        if existing.kind == new.kind and _overlap(existing.detail, new.detail) >= similarity:
            existing.count += new.count
            for run in new.runs:
                if run not in existing.runs:
                    existing.runs.append(run)
            for section in new.sections:
                if section not in existing.sections:
                    existing.sections.append(section)
            return
    gaps.append(new)


def gaps_markdown(gaps: list[KBGap]) -> str:
    if not gaps:
        return (
            "# KB gap report\n\nNo gaps found: every company claim across the "
            "scanned runs resolved to a knowledge-base entry.\n"
        )
    lines = [
        "# KB gap report",
        "",
        "Knowledge the pipeline needed and the KB could not supply, mined from "
        "run artifacts and clustered by similarity. Fill the top rows first: "
        "each one is a fact that will keep costing you drafts until it exists.",
        "",
        "| Priority | Times asked | Kind | Missing knowledge | Sections | Runs |",
        "|---|---|---|---|---|---|",
    ]
    for gap in gaps:
        detail = gap.detail.replace("|", "\\|")
        detail = detail[:150] + ("…" if len(gap.detail) > 150 else "")
        lines.append(
            f"| {gap.priority} | {gap.count} | {gap.kind} | {detail} | "
            f"{', '.join(gap.sections[:4])} | {len(gap.runs)} |"
        )
    lines += [
        "",
        "`uncited` gaps are HIGH: an uncited company claim is a hard QA failure "
        "that blocks export (FR-10). `needs_input` gaps shipped as visible "
        "`[NEEDS INPUT]` markers a human had to resolve by hand.",
        "",
        "Add these to the KB (`bidpilot interview` walks the schema), then "
        "re-run: the same claims will cite entries instead of asking again.",
    ]
    return "\n".join(lines)


# -- KB health ----------------------------------------------------------------


@dataclass
class KBHealth:
    entries: int = 0
    stale: list[str] = field(default_factory=list)
    duplicates: list[tuple[str, str, float]] = field(default_factory=list)
    broken_refs: list[str] = field(default_factory=list)
    thin: list[str] = field(default_factory=list)
    unusable_for_analogy: list[str] = field(default_factory=list)

    @property
    def issues(self) -> int:
        return (len(self.stale) + len(self.duplicates) + len(self.broken_refs)
                + len(self.thin) + len(self.unusable_for_analogy))


_REF_RE = re.compile(r"\b((?:pp|person|content)-[a-z0-9-]+)\b")


def health(kb: KnowledgeBase, max_age_days: int = 365) -> KBHealth:
    report = KBHealth(entries=len(kb.known_ids()))
    report.stale = kb.stale_entries(max_age_days)

    # Near-duplicate records split citations across two half-facts.
    docs: list[tuple[str, str]] = []
    for pp in kb.data.past_performance:
        docs.append((pp.kb_id, f"{pp.customer} {pp.scope_narrative}"))
    for content in kb.data.reusable_content:
        docs.append((content.kb_id, f"{content.title} {content.text}"))
    for i, (id_a, text_a) in enumerate(docs):
        for id_b, text_b in docs[i + 1:]:
            score = _overlap(text_a, text_b)
            if score >= DUPLICATE_THRESHOLD:
                report.duplicates.append((id_a, id_b, round(score, 2)))

    # Cross-references that name an entry which does not exist.
    known = set(kb.known_ids())
    for record in list(kb.data.past_performance) + list(kb.data.reusable_content):
        body = getattr(record, "text", "") or getattr(record, "scope_narrative", "")
        for ref in _REF_RE.findall(body or ""):
            if ref not in known:
                report.broken_refs.append(f"{record.kb_id} cites unknown entry {ref}")

    # Records too thin to support a claim.
    for pp in kb.data.past_performance:
        if len(pp.scope_narrative or "") < THIN_NARRATIVE_CHARS:
            report.thin.append(f"{pp.kb_id}: scope narrative too short to cite")
        if not pp.historical_actuals:
            report.unusable_for_analogy.append(
                f"{pp.kb_id}: no historical_actuals — cannot anchor analogy estimates"
            )
    for person in kb.data.personnel:
        if len(person.resume_summary or "") < THIN_NARRATIVE_CHARS:
            report.thin.append(f"{person.kb_id}: resume summary too short to cite")

    if not kb.profile.indirect_rates:
        report.thin.append("profile: no indirect rates — pricing falls back to defaults")
    return report


def health_markdown(report: KBHealth, kb: KnowledgeBase) -> str:
    lines = [
        "# KB health report",
        "",
        f"{report.entries} citable entries · **{report.issues} issues**",
        "",
    ]
    if not report.issues:
        lines.append("No issues: entries are fresh, distinct, cross-referenced, and citable.")
        return "\n".join(lines)

    def block(title: str, rows: list[str], why: str) -> None:
        if rows:
            lines.extend(["", f"## {title}", "", f"_{why}_", ""])
            lines.extend(f"- {row}" for row in rows)

    block("Stale entries", report.stale,
          "Rates and certifications age out; a stale entry quietly prices last "
          "year's contract. Re-verify and bump last_verified.")
    block("Possible duplicates",
          [f"`{a}` ≈ `{b}` ({score:.0%} overlap)" for a, b, score in report.duplicates],
          "Two records covering one engagement split its evidence — writers "
          "cite whichever they see first and the stronger detail goes unused.")
    block("Broken cross-references", report.broken_refs,
          "A record naming an entry that does not exist cannot be followed by "
          "a reviewer checking the claim.")
    block("Too thin to cite", report.thin,
          "A claim is only as good as the entry behind it; short records "
          "produce vague, unverifiable prose.")
    block("Unusable for analogy estimating", report.unusable_for_analogy,
          "Without historical_actuals the estimator falls back to parametric "
          "or judgment — the weakest basis of estimate.")

    lines += ["", "---", "",
              f"Owner: {kb.profile.governance.owner or '(unset)'} · "
              "nothing here is auto-fixed: every company fact is human-owned (FR-10)."]
    return "\n".join(lines)


def write_reports(output_root: Path, kb: KnowledgeBase,
                  dest: Optional[Path] = None) -> list[Path]:
    dest = dest or Path("kb_reports")
    dest.mkdir(parents=True, exist_ok=True)
    stamp = _dt.date.today().isoformat()
    gap_path = dest / f"KB_GAPS_{stamp}.md"
    health_path = dest / f"KB_HEALTH_{stamp}.md"
    gap_path.write_text(gaps_markdown(mine_gaps(output_root)), encoding="utf-8")
    health_path.write_text(health_markdown(health(kb), kb), encoding="utf-8")
    return [gap_path, health_path]
