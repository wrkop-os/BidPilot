"""Compliance Shredder (A5) — the single highest-value agent.

Three-pass technique per PRD §6.3:
  1. Overlapping-window extraction over the full corpus (fast tier per
     window — this is where volume lives).
  2. Deterministic dedup/merge in code (normalized-text similarity).
  3. Adversarial "what did you miss?" completeness pass (frontier tier),
     plus outline + format-constraint extraction.

Misses are catastrophic; extra rows are cheap (target >= 98% recall, G2).
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from ..models import (
    ComplianceMatrix,
    DocTree,
    FormatConstraints,
    ProposalOutline,
    Requirement,
)
from ..routing import ModelRouter, Tier

WINDOW_CHARS = 60_000
OVERLAP_CHARS = 8_000

EXTRACT_SYSTEM = """You extract binding requirements from federal solicitation text.
Extract EVERY imperative aimed at the offeror or the contractor: "shall", "must",
"will provide", "is required to", "not to exceed", "shall be organized as follows",
page limits, formatting rules, submission rules, required forms, evaluation
criteria descriptions.

Rules:
- verbatim_text: quote the requirement sentence(s) exactly. Never paraphrase away
  binding language. Split compound sentences into separate requirements.
- source: the document name is given; capture the section (L/M/C/PWS number) and
  the [page N] marker nearest above the text.
- category: format (fonts/pages/files) | content (what the proposal/work must
  contain) | administrative (registration, forms, submission mechanics) |
  evaluation (how the government scores).
- req_id: use the solicitation's own numbering where present (e.g. 'L-4.2.1',
  'PWS 3.1.5'); otherwise leave req_id as 'TBD' — IDs are assigned later.
- Recall over precision: when unsure whether something is binding, extract it."""

ADVERSARIAL_SYSTEM = """You are auditing a compliance matrix for completeness — the
"what did you miss?" pass. You get the full solicitation corpus and the current
matrix. Find binding requirements that are MISSING from the matrix: buried
formatting rules, submission mechanics, required forms, certifications, page
limits, org-of-proposal mandates, evaluation gate criteria, wage determination
obligations. Return ONLY the missing requirements, same extraction rules
(verbatim text, source, category). If truly nothing is missing, return an empty
list — but look hard first; misses are catastrophic."""

OUTLINE_SYSTEM = """From Section L (or 52.212-1 with addenda) of this solicitation,
extract:
1. The MANDATED proposal outline: volumes and their sections, in the exact
   order and naming the government prescribes (FR-9: the outline comes strictly
   from the solicitation, never invented).
2. Format constraints: page limits per volume, font/size, margins, file formats,
   naming conventions, number of copies.
3. Assign each provided requirement ID to the outline section that should
   address it (owner section).
If no explicit outline is mandated, derive the minimal conventional structure
from the instructions that DO exist and say so in a deviation_note."""


class _ExtractedRequirements(BaseModel):
    requirements: list[Requirement] = Field(default_factory=list)


class _OutlineAndConstraints(BaseModel):
    outline: ProposalOutline
    constraints: FormatConstraints
    owner_assignments: dict[str, str] = Field(
        default_factory=dict, description="req_id -> outline section_id"
    )


def shred(router: ModelRouter, doc_tree: DocTree) -> ComplianceMatrix:
    # Pass 1 — overlapping-window extraction, per document.
    raw: list[Requirement] = []
    for doc in doc_tree.docs:
        text = doc.full_text
        if not text.strip():
            continue
        for start in _window_starts(len(text)):
            window = text[start : start + WINDOW_CHARS]
            result = router.structured(
                Tier.FAST,
                system=EXTRACT_SYSTEM,
                prompt=f"Document: {doc.name}\n\n{window}",
                output_type=_ExtractedRequirements,
                max_tokens=32000,
                stage="shred.extract",
            )
            for req in result.requirements:
                req.source.doc = doc.name
            raw.extend(result.requirements)

    # Pass 2 — deterministic dedup/merge.
    merged = dedup_requirements(raw)

    # Pass 3 — adversarial completeness pass on the frontier model.
    matrix_text = "\n".join(f"- ({r.source.render()}) {r.verbatim_text}" for r in merged)
    missing = router.structured(
        Tier.FRONTIER,
        system=ADVERSARIAL_SYSTEM,
        prompt=f"""=== CURRENT MATRIX ({len(merged)} requirements) ===
{matrix_text}

=== FULL CORPUS ===
{doc_tree.corpus()}""",
        output_type=_ExtractedRequirements,
        max_tokens=32000,
        stage="shred.adversarial",
    )
    merged = dedup_requirements(merged + missing.requirements)
    assign_ids(merged)

    # Outline + constraints + owner assignment.
    req_list = "\n".join(f"{r.req_id}: {r.verbatim_text[:200]}" for r in merged)
    outline_result = router.structured(
        Tier.FRONTIER,
        system=OUTLINE_SYSTEM,
        prompt=f"""=== REQUIREMENTS ===
{req_list}

=== FULL CORPUS ===
{doc_tree.corpus()}""",
        output_type=_OutlineAndConstraints,
        max_tokens=32000,
        stage="shred.outline",
    )
    by_id = {r.req_id: r for r in merged}
    for req_id, section_id in outline_result.owner_assignments.items():
        if req_id in by_id:
            by_id[req_id].owner_section = section_id

    matrix = ComplianceMatrix(
        requirements=merged,
        outline=outline_result.outline,
        constraints=outline_result.constraints,
    )

    # Fourth pass, deterministic and local: the trained domain model re-reads
    # the corpus and reports requirement-shaped sentences the matrix does not
    # cover. No-op unless a promoted artifact is configured (BIDPILOT_REQ_MODEL).
    from ..ml.recall_net import find_missed

    for candidate in find_missed(doc_tree, matrix):
        matrix.model_flagged_gaps.append(candidate.render())
    return matrix


# ---------------------------------------------------------------------------
# Deterministic helpers
# ---------------------------------------------------------------------------


def _window_starts(length: int) -> list[int]:
    if length <= WINDOW_CHARS:
        return [0]
    starts, pos = [], 0
    step = WINDOW_CHARS - OVERLAP_CHARS
    while pos < length:
        starts.append(pos)
        pos += step
    return starts


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", " ".join(text.lower().split()))


def dedup_requirements(requirements: list[Requirement]) -> list[Requirement]:
    """Merge near-duplicate requirements from overlapping windows/passes.
    Exact-normalized-text duplicates merge; containment (one requirement's
    text inside another's) keeps the longer, more complete quote."""
    kept: list[Requirement] = []
    for req in requirements:
        norm = _normalize(req.verbatim_text)
        if not norm:
            continue
        duplicate = False
        for existing in kept:
            enorm = _normalize(existing.verbatim_text)
            if norm == enorm:
                duplicate = True
            elif norm in enorm:
                duplicate = True
            elif enorm in norm:
                existing.verbatim_text = req.verbatim_text
                existing.source = req.source
                duplicate = True
            if duplicate:
                break
        if not duplicate:
            kept.append(req)
    return kept


def assign_ids(requirements: list[Requirement]) -> None:
    """Keep solicitation-native IDs; assign sequential REQ-NNN to the rest,
    de-conflicting collisions."""
    seen: set[str] = set()
    counter = 1
    for req in requirements:
        rid = (req.req_id or "").strip()
        if rid and rid.upper() != "TBD" and rid not in seen:
            seen.add(rid)
            continue
        while f"REQ-{counter:03d}" in seen:
            counter += 1
        req.req_id = f"REQ-{counter:03d}"
        seen.add(req.req_id)
        counter += 1


def matrix_to_csv(matrix: ComplianceMatrix) -> str:
    import csv
    import io

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["req_id", "category", "source", "verbatim_text", "owner_section", "status", "addressed_in", "notes"]
    )
    for r in matrix.requirements:
        writer.writerow(
            [
                r.req_id, r.category.value, r.source.render(), r.verbatim_text,
                r.owner_section or "", r.status.value, "; ".join(r.addressed_in), r.notes or "",
            ]
        )
    return buf.getvalue()
