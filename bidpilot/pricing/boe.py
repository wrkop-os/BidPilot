"""BOE writer + consistency checks (PRD §9.6–9.7).

The BOE narrative is generated from the RECORDED method/rationale on each
labor line — that is what makes the estimate defensible in negotiations and
audits. The tech-vs-cost consistency check is a classic evaluator gotcha and
runs as code here plus an LLM audit in QA.
"""

from __future__ import annotations

from ..routing import ModelRouter
from .models import LaborEstimate, PricingModel

SYSTEM = """You are writing a Basis of Estimate document for a federal proposal cost
volume. For every line item, justify the hours using ONLY the recorded method and
rationale supplied — do not invent new justifications or change any numbers.
Structure: methodology overview, then per-task justification (task, labor mix,
hours, method, rationale), then assumptions and exclusions. Professional,
audit-ready tone. Output Markdown, starting with a '> DRAFT — pricing lead must
validate' blockquote."""


def write_boe(router: ModelRouter, pricing: PricingModel) -> str:
    prompt = f"""Write the Basis of Estimate from these recorded estimating decisions.

=== WBS & LABOR LINES (with recorded method + rationale) ===
{pricing.estimate.model_dump_json(indent=2) if pricing.estimate else "{}"}

=== PRICED TOTALS (computed deterministically by the rate engine) ===
Total: {pricing.total}
Sensitivity: {[p.model_dump() for p in pricing.sensitivity]}

=== ODCs ===
{[o.model_dump() for o in pricing.odcs]}"""
    return router.draft(system=SYSTEM, prompt=prompt, max_tokens=32000, stage="pricing.boe")


def staffing_consistency(estimate: LaborEstimate, drafts_text: str) -> list[str]:
    """Deterministic cross-check: labor categories priced in the cost volume
    should appear in the technical/staffing narrative. Returns discrepancies."""
    issues = []
    text = drafts_text.lower()
    for category in sorted({line.labor_category for line in estimate.lines}):
        if category.lower() not in text:
            issues.append(
                f"Labor category '{category}' is priced in the cost volume but never "
                "mentioned in the technical/staffing narrative — evaluators catch this."
            )
    return issues
