"""QA / Red Team agent (A12b): the LLM half of QA.

(a) Deterministic checks live in qa_checks.py and run first.
(b) LLM checks here: citation sampling audit, tech-vs-cost consistency
    audit, and the mock evaluation — score the proposal against Section M as
    a skeptical government evaluator. Findings route back to writers in a
    bounded fix loop (max 2 iterations) or to the human.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..kb.store import KnowledgeBase
from ..models import (
    ComplianceMatrix,
    MockEvaluation,
    QAFinding,
    SectionDraft,
)
from ..pricing.models import PricingModel
from ..routing import ModelRouter, Tier

MOCK_EVAL_SYSTEM = """You are a skeptical federal source-selection evaluator. Score
this proposal strictly against the evaluation factors (Section M or 52.212-2)
using adjectival ratings (Outstanding / Good / Acceptable / Marginal /
Unacceptable). For each factor list concrete strengths, weaknesses, and
deficiencies, quoting the proposal where relevant. Do not grade on effort or
prose quality — grade the way an evaluator constrained by the stated criteria
would. Deficiencies are failures to meet a material requirement."""

CONSISTENCY_SYSTEM = """You audit a federal proposal for internal consistency —
the classic evaluator gotchas:
- Do technical-volume staffing/hours statements match the cost volume's hours?
- Do promised deliverables/timelines appear consistently across sections?
- Are named key personnel consistent everywhere?
- Any contradictions between sections?
Report each inconsistency as a finding with severity 'hard' only when an
evaluator would treat it as a credibility problem; else 'soft'."""

CITATION_AUDIT_SYSTEM = """You audit claim->source pairs from a proposal draft.
For each pair, judge whether the KB source text actually supports the claim as
written. Report ONLY unsupported or overstated claims (severity 'hard' — these
are fabrication risks)."""


class _FindingList(BaseModel):
    findings: list[QAFinding] = Field(default_factory=list)


def mock_evaluate(
    router: ModelRouter, matrix: ComplianceMatrix, drafts: list[SectionDraft]
) -> MockEvaluation:
    factors = "\n".join(
        f"- [{r.req_id}] {r.verbatim_text}"
        for r in matrix.requirements
        if r.category.value == "evaluation"
    )
    proposal_text = "\n\n".join(d.markdown for d in drafts)
    return router.structured(
        Tier.FRONTIER,
        system=MOCK_EVAL_SYSTEM,
        prompt=f"""=== EVALUATION FACTORS ===
{factors or "(none extracted)"}

=== PROPOSAL ===
{proposal_text[:400_000]}""",
        output_type=MockEvaluation,
        max_tokens=16000,
        stage="qa.mock_eval",
    )


def consistency_audit(
    router: ModelRouter, drafts: list[SectionDraft], pricing: PricingModel | None
) -> list[QAFinding]:
    proposal_text = "\n\n".join(d.markdown for d in drafts)
    pricing_summary = ""
    if pricing and pricing.estimate:
        pricing_summary = "\n".join(
            f"- {line.labor_category}: {line.hours} hrs on {line.task_id}"
            for line in pricing.estimate.lines
        )
    result = router.structured(
        Tier.FRONTIER,
        system=CONSISTENCY_SYSTEM,
        prompt=f"""=== PROPOSAL TEXT ===
{proposal_text[:300_000]}

=== COST VOLUME LABOR HOURS ===
{pricing_summary or "(no pricing model)"}""",
        output_type=_FindingList,
        stage="qa.consistency",
    )
    return result.findings


def citation_sample_audit(
    router: ModelRouter, drafts: list[SectionDraft], kb: KnowledgeBase, sample_size: int = 20
) -> list[QAFinding]:
    """Sample claim->source pairs and verify the source supports the claim."""
    pairs = []
    for draft in drafts:
        for claim in draft.claims:
            if claim.kb_source_id and not claim.needs_input:
                source = kb.resolve(claim.kb_source_id)
                if source is not None:
                    pairs.append(
                        f"[{draft.section_id}] CLAIM: {claim.text}\nSOURCE [{claim.kb_source_id}]: "
                        f"{getattr(source, 'scope_narrative', None) or getattr(source, 'text', None) or getattr(source, 'resume_summary', None) or str(source)[:500]}"
                    )
    if not pairs:
        return []
    sample = pairs[:sample_size]
    result = router.structured(
        Tier.FRONTIER,
        system=CITATION_AUDIT_SYSTEM,
        prompt="Audit these claim->source pairs:\n\n" + "\n\n".join(sample),
        output_type=_FindingList,
        stage="qa.citation_audit",
    )
    return result.findings
