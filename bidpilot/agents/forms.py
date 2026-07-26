"""Forms agent: required government forms & representations, with pre-fill.

Forms carry legal certifications, so BidPilot only pre-fills administrative
fields from the company profile and enumerates what a human must complete,
certify, and sign.
"""

from __future__ import annotations

from ..config import CompanyProfile
from ..llm import LLM
from ..models import FormsPackage, SolicitationAnalysis

SYSTEM = """You are a contracts administrator preparing the forms and \
representations package for a federal proposal.

Rules:
- Identify every required form (SF-1449, SF-33, SF-30 amendment acknowledgments, \
etc.) and required representations/certifications (FAR 52.212-3, 52.204-24/25/26, \
Section K, or as stated).
- For each, pre-fill only administrative fields that map directly from the company \
profile (name, UEI, CAGE, address, POC). Never pre-fill a certification answer.
- human_actions must list everything requiring human judgment or signature: \
certifications, acknowledging amendments, signing blocks, reps & certs answers.
- If the solicitation references reps & certs maintained in SAM.gov, note that a \
human must verify the SAM record is current."""


def prepare_forms(
    llm: LLM, analysis: SolicitationAnalysis, profile: CompanyProfile
) -> FormsPackage:
    prompt = f"""Prepare the forms package.

=== SOLICITATION ANALYSIS (JSON) ===
{analysis.model_dump_json(indent=2)}

=== COMPANY PROFILE (YAML) ===
{profile.summary_text()}"""
    return llm.structured(
        system=SYSTEM,
        prompt=prompt,
        output_type=FormsPackage,
    )


def forms_to_markdown(pkg: FormsPackage) -> str:
    lines = ["# Forms & Representations Checklist", ""]
    for form in pkg.forms:
        lines.append(f"## {form.form_name}")
        lines.append(f"*{form.purpose}*")
        if form.prefill:
            lines.append("\n**Pre-filled from company profile:**")
            lines += [f"- {k}: {v}" for k, v in form.prefill.items()]
        if form.human_actions:
            lines.append("\n**⚠️ Human must:**")
            lines += [f"- [ ] {a}" for a in form.human_actions]
        lines.append("")
    if pkg.notes:
        lines.append("## Notes")
        lines += [f"- {n}" for n in pkg.notes]
    return "\n".join(lines)
