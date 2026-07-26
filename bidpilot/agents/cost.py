"""Cost agent: ROM cost/price estimate with a defensible basis of estimate.

The estimate uses the company's own labor categories and rates. It is a
starting point for the human pricing lead, never a submittable price — the
model is instructed to enumerate every pricing decision a human must make.
"""

from __future__ import annotations

from ..config import CompanyProfile
from ..llm import LLM
from ..models import CostEstimate, SolicitationAnalysis

SYSTEM = """You are a federal pricing analyst producing a rough-order-of-magnitude \
(ROM) cost estimate with a defensible basis of estimate (BOE).

Rules:
- Decompose the scope into labor line items mapped to the company's labor \
categories and rates. Use bottom-up hour estimates per scope item and state the \
estimating rationale for each in `assumption`.
- Include ODCs (travel, materials, tools) only where the solicitation implies them.
- Use only the rates in the company profile. If a needed labor category has no \
rate, include the line with rate=null and flag it in human_pricing_actions.
- The BOE narrative must explain methodology (e.g. bottoms-up engineering \
estimate), period of performance assumptions, and what is excluded.
- List EVERY judgment a human pricing lead must make (fee/profit, escalation, \
indirect/wrap rates, competitive price-to-win) in human_pricing_actions.
- Never present this as a final price."""


def estimate_cost(
    llm: LLM, analysis: SolicitationAnalysis, profile: CompanyProfile
) -> CostEstimate:
    prompt = f"""Produce the ROM cost estimate and basis of estimate.

=== SOLICITATION ANALYSIS (JSON) ===
{analysis.model_dump_json(indent=2)}

=== COMPANY PROFILE (YAML) ===
{profile.summary_text()}"""
    return llm.structured(
        system=SYSTEM,
        prompt=prompt,
        output_type=CostEstimate,
        max_tokens=32000,
    )


def estimate_to_markdown(est: CostEstimate) -> str:
    lines = [
        "# Cost Estimate (ROM — NOT a submittable price)",
        "",
        f"**Estimated total:** {est.total if est.total is not None else 'TBD'} {est.currency}",
        "",
        "| Description | Labor Category | Hours | Rate | Extended | Assumption |",
        "|---|---|---|---|---|---|",
    ]
    for li in est.line_items:
        lines.append(
            f"| {li.description} | {li.labor_category or ''} | {li.hours or ''} "
            f"| {li.rate or ''} | {li.extended_cost or ''} | {li.assumption or ''} |"
        )
    lines += ["", "## Basis of Estimate", "", est.basis_of_estimate]
    if est.risks_and_exclusions:
        lines += ["", "## Risks & Exclusions"] + [f"- {r}" for r in est.risks_and_exclusions]
    if est.human_pricing_actions:
        lines += ["", "## ⚠️ Human pricing actions required"] + [
            f"- [ ] {a}" for a in est.human_pricing_actions
        ]
    return "\n".join(lines)
