"""Eligibility agent: can this company legally and practically compete?"""

from __future__ import annotations

from ..config import CompanyProfile
from ..llm import LLM
from ..models import EligibilityReport, SolicitationAnalysis

SYSTEM = """You are a federal contracting eligibility analyst. Given a structured \
solicitation analysis and a company profile, determine whether the company is \
eligible to compete.

Check at minimum:
- NAICS code match and the associated small business size standard
- Set-aside eligibility (8(a), WOSB, SDVOSB, HUBZone, small business, full & open)
- SAM.gov registration status
- Security/facility clearance requirements
- Mandatory licenses, certifications, bonding, or geographic requirements
- Any pass/fail gate criteria in the evaluation factors

Be conservative: mark a check `passes: null` and add a human_review_items entry \
whenever the profile lacks the information to decide. Only return verdict \
'ineligible' for hard, unambiguous blockers (e.g. wrong set-aside category). \
Otherwise return 'eligible' or 'needs_human_review'."""


def check_eligibility(
    llm: LLM, analysis: SolicitationAnalysis, profile: CompanyProfile
) -> EligibilityReport:
    prompt = f"""Determine eligibility.

=== SOLICITATION ANALYSIS (JSON) ===
{analysis.model_dump_json(indent=2)}

=== COMPANY PROFILE (YAML) ===
{profile.summary_text()}"""
    return llm.structured(
        system=SYSTEM,
        prompt=prompt,
        output_type=EligibilityReport,
    )
