"""Labor estimator (PRD §9.2–9.3): WBS decomposition and per-task hour
proposals with recorded method (analogy | parametric | bottom_up), rationale,
and confidence per line. The LLM proposes hours and records WHY; the rate
engine (rates.py) turns them into dollars deterministically."""

from __future__ import annotations

from pydantic import BaseModel

from ..kb.store import KnowledgeBase
from ..models import ComplianceMatrix, WinStrategy
from ..routing import ModelRouter, Tier
from .models import LaborEstimate, ODCItem, PricingStructure

SYSTEM = """You are a senior federal cost estimator. Decompose the solution into a
work breakdown structure mapped to SOW paragraphs, then propose hours per task by
labor category.

Rules for every labor line:
- Record the estimating method: 'analogy' when the company's historical actuals
  (provided from the knowledge base) cover similar work — this is the strongest
  basis; 'parametric' when driven by a countable driver (sites, tickets/month,
  pages, users); 'bottom_up' otherwise.
- The rationale must state the driver and arithmetic (e.g. "12 sites x 6 hrs
  survey + 4 hrs config = 120 hrs"), because it becomes the basis of estimate.
- Use ONLY labor categories from the company's list. If work needs a category
  the company lacks, still emit the line with the closest category and say so.
- Set confidence low/medium/high honestly; low-confidence lines get human review.
- Do NOT price anything — no rates, no dollars. Hours and rationale only."""

ODC_SYSTEM = """Identify Other Direct Costs (travel, materials, licenses, hardware)
this solicitation implies. Price from the provided knowledge-base catalog entries
where possible (cite the entry); anything requiring a live vendor quote must be
emitted with quote_needed=true and no invented price."""


class _ODCList(BaseModel):
    items: list[ODCItem]


def estimate_labor(
    router: ModelRouter,
    strategy: WinStrategy,
    matrix: ComplianceMatrix,
    structure: PricingStructure,
    kb: KnowledgeBase,
) -> LaborEstimate:
    scope_reqs = "\n".join(
        f"- [{r.req_id}] ({r.source.render()}) {r.verbatim_text}"
        for r in matrix.requirements
        if r.category.value == "content"
    )
    prompt = f"""Build the WBS and labor estimate.

=== WIN STRATEGY / SOLUTION APPROACH ===
{strategy.model_dump_json(indent=2)}

=== PRICING STRUCTURE (CLINs) ===
{structure.model_dump_json(indent=2)}

=== SCOPE REQUIREMENTS (from compliance matrix) ===
{scope_reqs or "(none extracted)"}

=== COMPANY LABOR CATEGORIES ===
{kb.labor_category_list()}

=== HISTORICAL ACTUALS / PAST PERFORMANCE (for analogy estimates) ===
{kb.past_performance_text()}"""
    return router.structured(
        Tier.FRONTIER,
        system=SYSTEM,
        prompt=prompt,
        output_type=LaborEstimate,
        max_tokens=32000,
        stage="pricing.labor",
    )


def estimate_odcs(router: ModelRouter, strategy: WinStrategy, kb: KnowledgeBase) -> list[ODCItem]:
    prompt = f"""Identify ODCs.

=== SOLUTION APPROACH ===
{strategy.solution_summary}

Assumptions: {strategy.assumptions}

=== KB CATALOG / REUSABLE CONTENT ===
{kb.reusable_content_text()[:20000]}"""
    result = router.structured(
        Tier.FRONTIER,
        system=ODC_SYSTEM,
        prompt=prompt,
        output_type=_ODCList,
        stage="pricing.odc",
    )
    return result.items
