"""Capability statement agent (PRD §2.2): the response to a Sources Sought /
RFI is a 2-5 page capability statement, not a proposal. Same citation
discipline as section writers — company facts come from the KB or are
emitted as [NEEDS INPUT]."""

from __future__ import annotations

from ..kb.store import KnowledgeBase
from ..models import DocTree, NoticeMetadata, SectionDraft
from ..routing import ModelRouter, Tier

SYSTEM = """You write a capability statement responding to a federal Sources
Sought notice / RFI. Structure (2-5 pages):
1. Company identification: name, UEI, CAGE, socioeconomic certifications,
   NAICS codes, POC.
2. Direct responses to EVERY question or information request in the notice,
   in the notice's own order and numbering.
3. Relevant capabilities mapped to the described scope, using the
   government's terminology.
4. Relevant past performance with concrete scope/value/period.

Hard rules (same as proposal writers):
- Every company fact cites a KB entry (kb_source_id in claims) or is emitted
  as [NEEDS INPUT: ...] with needs_input=true. Never invent facts.
- Answer what was asked; a sources-sought response shapes the acquisition
  strategy (e.g. influencing a set-aside decision) — state the company's
  size/socioeconomic status prominently and accurately.
- markdown output; addressed_requirements lists any notice question numbers
  answered."""


def write_capability_statement(
    router: ModelRouter, metadata: NoticeMetadata, doc_tree: DocTree, kb: KnowledgeBase
) -> SectionDraft:
    draft = router.structured(
        Tier.FRONTIER,
        system=SYSTEM,
        prompt=f"""Write the capability statement.

=== NOTICE ===
Title: {metadata.title}
Agency: {metadata.agency}
NAICS: {metadata.naics_code} | Set-aside: {metadata.set_aside or "none stated"}

=== VALID KB CITATION IDs ===
{", ".join(kb.known_ids())}

=== COMPANY KNOWLEDGE BASE ===
{kb.citable_corpus()[:150_000]}

=== NOTICE CORPUS ===
{doc_tree.corpus()[:300_000]}""",
        output_type=SectionDraft,
        max_tokens=32000,
        stage="capability_statement",
    )
    draft.section_id = "CAP-1"
    draft.volume = "Capability Statement"
    draft.word_count = len(draft.markdown.split())
    return draft
