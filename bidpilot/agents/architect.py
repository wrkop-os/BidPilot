"""Solution Architect (A6): win strategy BEFORE any writing.

Assumptions are surfaced to the human at an explicit checkpoint (the
orchestrator's assumptions gate) rather than silently guessed."""

from __future__ import annotations

from ..kb.store import KnowledgeBase
from ..models import ComplianceMatrix, DocTree, WinStrategy
from ..prompting import split_for_cache
from ..routing import ModelRouter, Tier

SYSTEM = """You are a capture strategist for a government contractor. From the
SOW/PWS, the evaluation factors, and the company's actual capabilities, produce:
- solution_summary: the technical/management approach in 2-4 paragraphs
- win_themes: 3-6 themes tying company strengths to what the evaluators score
- discriminators: what genuinely sets THIS company apart (grounded in the KB —
  do not invent capabilities)
- risks_and_mitigations: delivery risks the government will worry about + answers
- staffing_concept: labor mix concept using the company's real labor categories
- assumptions: EVERY assumption you are making (self-perform vs team, place of
  performance logistics, scope interpretations, reuse of existing tooling).
  These go to a human checkpoint — surface all of them; never silently guess."""


def build_strategy(
    router: ModelRouter, doc_tree: DocTree, matrix: ComplianceMatrix, kb: KnowledgeBase
) -> WinStrategy:
    eval_reqs = "\n".join(
        f"- [{r.req_id}] {r.verbatim_text}"
        for r in matrix.requirements
        if r.category.value == "evaluation"
    )
    _corpus = split_for_cache(doc_tree.corpus(), 400000)
    prompt = f"""Develop the win strategy.

=== EVALUATION FACTORS (from the compliance matrix) ===
{eval_reqs or "(none extracted — read Section M in the corpus)"}

=== COMPANY KNOWLEDGE BASE ===
{kb.citable_corpus()[:150_000]}

=== SOLICITATION CORPUS ===
{_corpus.tail}"""
    return router.structured(
        Tier.FRONTIER,
        system=SYSTEM,
        prompt=prompt,
        output_type=WinStrategy,
        cache_prefix=_corpus.head,
        max_tokens=16000,
        stage="strategy",
    )
