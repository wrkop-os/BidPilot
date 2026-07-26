"""Drafting agent: technical / management / past-performance volumes.

Long-form generation, streamed. Drafts are written against the compliance
matrix and the solicitation's own volume structure so every 'shall' has a
home, and are explicitly marked DRAFT for human revision.
"""

from __future__ import annotations

from ..config import CompanyProfile
from ..llm import LLM
from ..models import ComplianceMatrix, SolicitationAnalysis, VolumeRequirement

SYSTEM = """You are a senior federal proposal writer. You draft proposal volumes that:
- Follow the solicitation's required volume structure, section order, and \
formatting instructions exactly (Section L or equivalent).
- Respond to every evaluation factor (Section M or equivalent) with substantive, \
specific content — evaluators score against these factors.
- Use only facts from the company profile. Where a fact is missing (a named person, \
a metric, a contract number), insert a clearly marked placeholder like \
[[HUMAN: insert past performance POC name]] rather than inventing one.
- Write in active voice, benefits-forward ("The Government receives X because we do Y"),
  with compliance-traceable headings.
- Respect stated page limits: keep the draft comfortably within them.

Output plain Markdown. Start the document with a '> DRAFT — requires human review' \
blockquote."""


def draft_volume(
    llm: LLM,
    volume: VolumeRequirement,
    analysis: SolicitationAnalysis,
    matrix: ComplianceMatrix,
    profile: CompanyProfile,
) -> str:
    relevant_rows = [
        r for r in matrix.rows if r.category in ("content", "evaluation", "format")
    ]
    matrix_excerpt = "\n".join(
        f"- [{r.requirement_id}] ({r.source}) {r.requirement_text}" for r in relevant_rows
    )
    prompt = f"""Draft the following proposal volume.

=== VOLUME TO DRAFT ===
{volume.model_dump_json(indent=2)}

=== SOLICITATION ANALYSIS ===
{analysis.model_dump_json(indent=2)}

=== CONTENT/EVALUATION/FORMAT REQUIREMENTS FROM THE COMPLIANCE MATRIX ===
{matrix_excerpt or "(no matrix rows extracted — draft from the analysis alone)"}

=== COMPANY PROFILE ===
{profile.summary_text()}

Draft the complete volume now. Where the compliance matrix requirement is addressed,
note the requirement ID in an HTML comment (e.g. <!-- addresses L-4.2 -->) so the
matrix can be back-filled with proposal locations."""
    return llm.draft(system=SYSTEM, prompt=prompt, max_tokens=64000)
