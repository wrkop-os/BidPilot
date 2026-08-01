"""Past Performance agent (A8): select best-matching KB references, draft
relevancy narratives, flag gaps against the solicitation's stated bar."""

from __future__ import annotations

from ..kb.store import KnowledgeBase
from ..models import ComplianceMatrix, DocTree, PastPerformanceSelection
from ..prompting import sections_for, split_for_cache
from ..routing import ModelRouter, Tier

SYSTEM = """You select and present past performance references for a federal
proposal. From the knowledge base records provided (cite by kb_id ONLY — these
are the only references that exist):
- Score each candidate's relevancy (0-1) against the solicitation's scope, size,
  and recency requirements.
- Select the best set meeting the solicitation's stated requirement (e.g. "three
  contracts of similar size and scope in the last five years").
- Draft a relevancy narrative per selected reference tying its scope to THIS
  solicitation's requirements, using the government's terminology.
- gaps: state plainly where the KB falls short of the stated bar (e.g.
  "solicitation wants 3 refs >= $1M; KB has 2"). Never pad with invented or
  unrelated references."""


def select_past_performance(
    router: ModelRouter, matrix: ComplianceMatrix, kb: KnowledgeBase, doc_tree: DocTree
) -> PastPerformanceSelection:
    pp_requirements = "\n".join(
        f"- [{r.req_id}] {r.verbatim_text}"
        for r in matrix.requirements
        if "past performance" in r.verbatim_text.lower() or "reference" in r.verbatim_text.lower()
    )
    _corpus = split_for_cache(doc_tree.corpus(), 200000, doc_tree,
                              sections_for("past_performance"))
    prompt = f"""Select past performance references.

=== PAST-PERFORMANCE REQUIREMENTS ===
{pp_requirements or "(none explicitly extracted — check the corpus)"}

=== KB PAST PERFORMANCE RECORDS ===
{kb.past_performance_text()}

=== SOLICITATION CORPUS (scope for relevancy) ===
{_corpus.tail}"""
    return router.structured(
        Tier.FRONTIER,
        system=SYSTEM,
        prompt=prompt,
        output_type=PastPerformanceSelection,
        cache_prefix=_corpus.head,
        stage="past_performance",
    )
