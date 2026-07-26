"""Section Writer agents (A7): one call per outline section, parallelizable.

Hard rules encoded in the prompt (PRD §6.3):
- Address every assigned requirement explicitly and in order.
- Every factual claim about the company carries a KB citation (kb_source_id
  from the provided ID list) or is emitted as needs_input — fabricating past
  performance, resumes, or certifications is the cardinal sin and the
  fail-closed gate (FR-10) treats it as a build-breaking error.
- Mirror the government's own terminology; write to the evaluation criteria.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from ..kb.store import KnowledgeBase
from ..models import (
    ComplianceMatrix,
    DocTree,
    OutlineSection,
    SectionDraft,
    WinStrategy,
)
from ..routing import ModelRouter, Tier

SYSTEM = """You are a senior federal proposal section writer. Draft ONE proposal
section. Hard rules:
1. Address EVERY assigned requirement explicitly, in the order given. Note the
   requirement ID in an HTML comment where addressed (<!-- addresses L-4.2 -->)
   and list it in addressed_requirements.
2. Company facts: every factual claim about the company (past performance,
   staff, certifications, metrics, tools) MUST cite a knowledge-base entry ID
   from the provided list via the claims array (kb_source_id). If the fact you
   need is not in the KB, write "[NEEDS INPUT: <what>]" in the prose and emit a
   claim with needs_input=true. NEVER invent a company fact — fabrication is a
   build-breaking error.
3. Mirror the government's terminology from the SOW/PWS exactly.
4. Write to the evaluation criteria — evaluators score against Section M.
5. Respect the page-limit guidance; be substantive and specific, not generic.
6. markdown starts with '## <section title>'. The claims array must list every
   company-factual sentence with its source."""


def write_section(
    router: ModelRouter,
    section: OutlineSection,
    matrix: ComplianceMatrix,
    strategy: WinStrategy,
    kb: KnowledgeBase,
    doc_tree: DocTree,
) -> SectionDraft:
    assigned = [r for r in matrix.requirements if r.req_id in set(section.assigned_requirements)]
    assigned_text = "\n".join(
        f"{i+1}. [{r.req_id}] ({r.source.render()}) {r.verbatim_text}"
        for i, r in enumerate(assigned)
    )
    page_limit = matrix.constraints.page_limits.get(section.volume)
    prompt = f"""Draft this section.

=== SECTION ===
{section.model_dump_json(indent=2)}
Volume page limit: {page_limit or "not stated"}

=== ASSIGNED REQUIREMENTS (address every one, in order) ===
{assigned_text or "(none assigned — write from the section guidance)"}

=== WIN STRATEGY ===
{strategy.model_dump_json(indent=2)}

=== VALID KB CITATION IDs ===
{", ".join(kb.known_ids())}

=== COMPANY KNOWLEDGE BASE (the ONLY source for company facts) ===
{kb.citable_corpus()[:150_000]}

=== RELEVANT SOLICITATION TEXT ===
{doc_tree.corpus()[:200_000]}"""
    draft = router.structured(
        Tier.FRONTIER,
        system=SYSTEM,
        prompt=prompt,
        output_type=SectionDraft,
        max_tokens=32000,
        stage=f"write.{section.section_id}",
    )
    draft.section_id = section.section_id
    draft.volume = section.volume
    draft.word_count = len(draft.markdown.split())
    return draft


def write_all_sections(
    router: ModelRouter,
    matrix: ComplianceMatrix,
    strategy: WinStrategy,
    kb: KnowledgeBase,
    doc_tree: DocTree,
    max_workers: int = 4,
) -> list[SectionDraft]:
    """Parallel production swarm: many concurrent section writers inside a
    fixed graph node (PRD §6.4)."""
    sections = matrix.outline.sections if matrix.outline else []
    if not sections:
        sections = [
            OutlineSection(
                section_id="TECH-1",
                title="Technical and Management Approach",
                volume="Volume I - Technical",
                assigned_requirements=[
                    r.req_id for r in matrix.requirements if r.category.value == "content"
                ],
            )
        ]
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [
            pool.submit(write_section, router, s, matrix, strategy, kb, doc_tree)
            for s in sections
        ]
        return [f.result() for f in futures]
