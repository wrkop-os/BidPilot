"""Shared-prefix construction for prompt caching.

Eleven stages read the same solicitation corpus. On a measured run that turned
34K chars of source into 468K chars on the wire — a 14x amplification that is
pure repetition, and the single largest controllable cost in the pipeline.

Prompt caching fixes it, but only under one condition: **the cached block must
be byte-identical across calls**. Every differing character is a cache miss and
the saving evaporates silently, which is the failure mode this module exists to
prevent.

That condition was not free. Each agent capped the corpus at its own limit —
400K, 300K, 200K, 150K — so no two agents sent the same bytes. `split_for_cache`
keeps every agent's total visibility while making the *first* CACHE_HEAD_CHARS
identical for all of them: the head is cached and shared, and any agent allowed
to see more receives the remainder inline as ordinary prompt text.

Truncation is also reported rather than silent. An agent that only sees the
first 300K chars of a 900K-char solicitation is working from a fragment, and
before this the pipeline said nothing about it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# The shared, cacheable head. Deliberately SMALLER than the tightest stage cap
# (150K): if the head consumed a stage's entire budget, that stage would have
# nothing left to spend on the sections it actually needs, and section
# targeting below would silently do nothing for it.
CACHE_HEAD_CHARS = 100_000

# The tightest per-stage cap in the pipeline. A corpus larger than this is
# invisible past that point to at least one stage, which is worth saying out
# loud once per run rather than never.
SMALLEST_STAGE_LIMIT = 150_000

# Which UCF sections each stage should be given first when the corpus will not
# fit whole. Uniform Contract Format puts the answers in predictable places:
#   K  representations and certifications
#   L  instructions to offerors (what to submit, how, by when)
#   M  evaluation factors
#   I  contract clauses
#   C  statement of work / PWS
#   B  supplies, services and prices
STAGE_SECTIONS: dict[str, tuple[str, ...]] = {
    "submission":       ("L", "M", "A"),
    "forms":            ("K", "I", "A"),
    "eligibility":      ("K", "L", "M", "I"),
    "past_performance": ("L", "M", "C"),
    "strategy":         ("C", "M", "L"),
    "capability":       ("C", "L"),
    "classify":         ("A", "B", "C"),
}


def sections_for(stage: str) -> tuple[str, ...]:
    return STAGE_SECTIONS.get(stage, ())


@dataclass
class CorpusSlice:
    """A corpus split into the shared cached head and an agent-specific tail."""

    head: str
    tail: str
    total_chars: int
    limit: int
    # True when the tail was filled by UCF section relevance rather than by
    # position, which only happens for a corpus too large to send whole.
    prioritized: bool = False
    sections_used: list = field(default_factory=list)

    @property
    def truncated(self) -> bool:
        return self.total_chars > self.limit

    @property
    def dropped_chars(self) -> int:
        return max(0, self.total_chars - self.limit)

    def notice(self) -> Optional[str]:
        """What to tell a human when the agent did not see everything."""
        if not self.truncated:
            return None
        if self.prioritized:
            return (
                f"Corpus too large to send whole ({self.total_chars:,} chars, "
                f"limit {self.limit:,}): this stage received the shared head "
                f"plus UCF sections {', '.join(self.sections_used)} instead of "
                "the next contiguous text. Material outside those sections was "
                "not shown."
            )
        return (
            f"Corpus truncated for this stage: {self.dropped_chars:,} of "
            f"{self.total_chars:,} characters were not shown (limit "
            f"{self.limit:,}). Requirements in the dropped tail cannot be found "
            "by this stage."
        )


def split_for_cache(corpus: str, limit: int, doc_tree=None,
                    prefer: tuple[str, ...] = ()) -> CorpusSlice:
    """Split so the head is identical for every caller.

    `limit` stays per-agent — it reflects that agent's context budget — but the
    cached head does not, because a per-agent head would never hit the cache.

    When the corpus fits, the tail is simply the rest of it. When it does not,
    the tail is spent on the UCF sections this stage actually needs (`prefer`)
    rather than on whatever text happened to come next. Selecting by position
    is the worst possible rule: submission instructions live in Section L, reps
    and certs in K, and a 900K-char package puts both well past any prefix.

    Prioritization only ever affects the TAIL, never the head, so the shared
    cached block stays byte-identical across stages.
    """
    corpus = corpus or ""
    head = corpus[:CACHE_HEAD_CHARS]
    budget = max(0, limit - len(head))
    if len(corpus) <= limit or not prefer or doc_tree is None:
        return CorpusSlice(head=head, tail=corpus[CACHE_HEAD_CHARS:limit],
                           total_chars=len(corpus), limit=limit,
                           prioritized=False)

    picked, used = [], 0
    for section_id in prefer:
        for doc in getattr(doc_tree, "docs", []) or []:
            for section in getattr(doc, "sections", []) or []:
                if section.section_id.upper() != section_id.upper():
                    continue
                text = section.text or ""
                # Anything already inside the cached head is not worth resending.
                if text and text in head:
                    continue
                room = budget - used
                if room <= 0:
                    break
                header = f"=== {doc.name} \u00a7{section.section_id}: {section.title} ===\n"
                chunk = text[:max(0, room - len(header))]
                if chunk:
                    picked.append(header + chunk)
                    # Headers count against the budget too, or the limit the
                    # caller set is not the limit it gets.
                    used += len(header) + len(chunk)
    if not picked:
        return CorpusSlice(head=head, tail=corpus[CACHE_HEAD_CHARS:limit],
                           total_chars=len(corpus), limit=limit,
                           prioritized=False)

    # Targeted sections are usually far smaller than the budget. Spend what is
    # left continuing through the corpus, so prioritizing never costs recall
    # relative to the positional slice it replaced — it only reorders which
    # text is guaranteed to survive the cut.
    marker = "=== CONTINUED CORPUS ===\n"
    remaining = budget - used - len(marker)
    if remaining > 0:
        filler = corpus[CACHE_HEAD_CHARS:CACHE_HEAD_CHARS + remaining]
        if filler:
            picked.append(marker + filler)
    # Join separators count too; truncating the assembled tail is the only way
    # to guarantee the caller's limit is actually the limit.
    return CorpusSlice(head=head, tail="\n\n".join(picked)[:budget],
                       total_chars=len(corpus), limit=limit,
                       prioritized=True, sections_used=list(prefer))
