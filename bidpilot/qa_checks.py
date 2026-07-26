"""Deterministic QA checks (A12a) — code, not LLM.

These implement the fail-closed guarantees:
- FR-10: uncited company-fact claims are HARD failures; export is blocked.
- Matrix coverage: every requirement with an owner section must be addressed
  somewhere.
- Format checks: page estimates vs. limits (the production renderer owns
  exact page counts; the word-count heuristic catches gross violations).
- WD violations from the rate engine are re-asserted here as hard failures.
"""

from __future__ import annotations

import re

from .kb.store import KnowledgeBase
from .models import (
    ComplianceMatrix,
    QAFinding,
    QASeverity,
    RequirementStatus,
    SectionDraft,
)
from .pricing.models import PricingModel

WORDS_PER_PAGE = 450  # single-spaced 12pt heuristic; renderer owns the real count


def citation_check(drafts: list[SectionDraft], kb: KnowledgeBase) -> list[QAFinding]:
    """FR-10 fail-closed: every company-fact claim must resolve to a KB entry
    or be an explicit [NEEDS INPUT]."""
    findings: list[QAFinding] = []
    for draft in drafts:
        for claim in draft.claims:
            if claim.needs_input:
                findings.append(
                    QAFinding(
                        severity=QASeverity.SOFT,
                        category="citation",
                        description=f"[NEEDS INPUT] outstanding: {claim.input_note or claim.text[:120]}",
                        location=draft.section_id,
                    )
                )
            elif not claim.kb_source_id:
                findings.append(
                    QAFinding(
                        severity=QASeverity.HARD,
                        category="fabrication",
                        description=f"Uncited company-fact claim: “{claim.text[:160]}”",
                        location=draft.section_id,
                    )
                )
            elif kb.resolve(claim.kb_source_id) is None:
                findings.append(
                    QAFinding(
                        severity=QASeverity.HARD,
                        category="fabrication",
                        description=(
                            f"Claim cites nonexistent KB entry '{claim.kb_source_id}': "
                            f"“{claim.text[:120]}”"
                        ),
                        location=draft.section_id,
                    )
                )
        # Also catch inline [NEEDS INPUT] markers not surfaced as claims.
        for marker in re.findall(r"\[NEEDS INPUT:? [^\]]*\]", draft.markdown):
            if not any(c.needs_input and (c.input_note or "") in marker for c in draft.claims):
                findings.append(
                    QAFinding(
                        severity=QASeverity.SOFT,
                        category="citation",
                        description=f"Unresolved marker in prose: {marker[:160]}",
                        location=draft.section_id,
                    )
                )
    return findings


def coverage_check(matrix: ComplianceMatrix, drafts: list[SectionDraft]) -> list[QAFinding]:
    """Every matrix requirement must map to at least one addressed location.
    Updates requirement statuses in place (unaddressed -> drafted)."""
    findings: list[QAFinding] = []
    addressed: dict[str, list[str]] = {}
    for draft in drafts:
        ids = set(draft.addressed_requirements)
        ids.update(re.findall(r"<!--\s*addresses\s+([^>]+?)\s*-->", draft.markdown))
        for rid in ids:
            for token in re.split(r"[,;]\s*", rid.strip()):
                if token:
                    addressed.setdefault(token, []).append(draft.section_id)

    for req in matrix.requirements:
        locations = addressed.get(req.req_id, [])
        if locations:
            req.addressed_in = sorted(set(req.addressed_in) | set(locations))
            if req.status == RequirementStatus.UNADDRESSED:
                req.status = RequirementStatus.DRAFTED
        elif req.category.value in ("content", "evaluation") and req.owner_section:
            findings.append(
                QAFinding(
                    severity=QASeverity.HARD,
                    category="coverage",
                    description=f"Requirement {req.req_id} unaddressed: “{req.verbatim_text[:140]}”",
                    location=req.owner_section,
                )
            )
        else:
            findings.append(
                QAFinding(
                    severity=QASeverity.SOFT,
                    category="coverage",
                    description=(
                        f"Requirement {req.req_id} ({req.category.value}) has no mapped "
                        "draft location — verify it is handled (forms/format/submission side)."
                    ),
                )
            )
    return findings


def format_check(matrix: ComplianceMatrix, drafts: list[SectionDraft]) -> list[QAFinding]:
    """Page-limit heuristic per volume (renderer owns exact counts)."""
    findings: list[QAFinding] = []
    if not matrix.constraints.page_limits:
        return findings
    words_by_volume: dict[str, int] = {}
    for draft in drafts:
        words_by_volume[draft.volume] = words_by_volume.get(draft.volume, 0) + draft.word_count

    for volume, limit in matrix.constraints.page_limits.items():
        words = words_by_volume.get(volume)
        if words is None:
            continue
        est_pages = words / WORDS_PER_PAGE
        if est_pages > limit:
            findings.append(
                QAFinding(
                    severity=QASeverity.HARD,
                    category="format",
                    description=(
                        f"{volume}: ~{est_pages:.0f} estimated pages exceeds the "
                        f"{limit}-page limit ({words} words)."
                    ),
                    location=volume,
                )
            )
        elif est_pages > limit * 0.9:
            findings.append(
                QAFinding(
                    severity=QASeverity.SOFT,
                    category="format",
                    description=f"{volume}: ~{est_pages:.0f} pages is within 10% of the {limit}-page limit.",
                    location=volume,
                )
            )
    return findings


def pricing_check(pricing: PricingModel) -> list[QAFinding]:
    findings: list[QAFinding] = []
    for violation in pricing.wd_violations:
        findings.append(
            QAFinding(
                severity=QASeverity.HARD,
                category="consistency",
                description=f"WAGE DETERMINATION VIOLATION — {violation.detail}",
                location=violation.labor_category,
            )
        )
    for item in pricing.quote_needed:
        findings.append(
            QAFinding(severity=QASeverity.SOFT, category="consistency",
                      description=f"[QUOTE NEEDED] {item}")
        )
    return findings
