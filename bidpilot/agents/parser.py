"""Parser agent: full solicitation corpus -> structured SolicitationAnalysis."""

from __future__ import annotations

from ..llm import LLM
from ..models import RawOpportunity, SolicitationAnalysis

SYSTEM = """You are a federal proposal analyst. You read complete solicitations \
(SAM.gov notice text plus all attachments: the solicitation document, SOW/PWS, \
amendments, Q&A, wage determinations) and extract their structure precisely.

Rules:
- Quote requirements faithfully; never invent requirements that are not in the text.
- Pay special attention to Section L (instructions to offerors) and Section M \
(evaluation criteria) or their equivalents in commercial-format solicitations.
- Amendments override the base solicitation — if amendments are present, reflect \
the amended requirements and note the change.
- If something material is ambiguous or missing, record it in ambiguities_and_risks \
rather than guessing."""


def analyze_solicitation(llm: LLM, opportunity: RawOpportunity, corpus: str) -> SolicitationAnalysis:
    prompt = f"""Analyze this federal contract opportunity end to end.

Notice metadata from SAM.gov:
- Notice ID: {opportunity.notice_id}
- Solicitation #: {opportunity.solicitation_number or "unknown"}
- Title: {opportunity.title or "unknown"}
- Agency: {opportunity.agency or "unknown"}
- Notice type: {opportunity.notice_type or "unknown"}
- Response deadline (per SAM.gov): {opportunity.response_deadline or "unknown"}
- NAICS: {opportunity.naics_code or "unknown"}
- Set-aside: {opportunity.set_aside or "none stated"}

Full solicitation corpus follows.

{corpus}"""
    return llm.structured(
        system=SYSTEM,
        prompt=prompt,
        output_type=SolicitationAnalysis,
        max_tokens=32000,
    )
