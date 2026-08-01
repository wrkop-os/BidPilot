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

from dataclasses import dataclass
from typing import Optional

# The shared, cacheable head. Sized to sit under every agent's own cap so it is
# genuinely common to all of them; agents permitted more get the tail inline.
CACHE_HEAD_CHARS = 150_000

# The tightest per-stage cap in the pipeline. A corpus larger than this is
# invisible past that point to at least one stage, which is worth saying out
# loud once per run rather than never.
SMALLEST_STAGE_LIMIT = 150_000


@dataclass
class CorpusSlice:
    """A corpus split into the shared cached head and an agent-specific tail."""

    head: str
    tail: str
    total_chars: int
    limit: int

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
        return (
            f"Corpus truncated for this stage: {self.dropped_chars:,} of "
            f"{self.total_chars:,} characters were not shown (limit "
            f"{self.limit:,}). Requirements in the dropped tail cannot be found "
            "by this stage."
        )


def split_for_cache(corpus: str, limit: int) -> CorpusSlice:
    """Split so the head is identical for every caller.

    `limit` stays per-agent — it reflects that agent's context budget — but the
    cached head does not, because a per-agent head would never hit the cache.
    """
    corpus = corpus or ""
    visible = corpus[:limit]
    head = visible[:CACHE_HEAD_CHARS]
    return CorpusSlice(head=head, tail=visible[CACHE_HEAD_CHARS:],
                       total_chars=len(corpus), limit=limit)
