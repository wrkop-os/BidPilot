"""Lightweight retrieval over the solicitation corpus (A2: chunk + index
with doc/section/page metadata).

v1 is a dependency-free lexical index (BM25-style scoring) so section
writers pull the *relevant* SOW/PWS excerpts instead of receiving the whole
200–400K-char corpus per call — the single biggest model-spend lever in the
produce stage (NFR-2). Production target is pgvector behind this same
interface.

Deliberately NOT used by the shredder, classifier, eligibility, or
submission agents: those must see everything — recall beats cost there.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field

from ..models import DocTree

CHUNK_CHARS = 2_000
CHUNK_OVERLAP = 200

_TOKEN_RE = re.compile(r"[a-z0-9]{2,}")
_PAGE_RE = re.compile(r"\[page (\d+)\]")

_STOPWORDS = frozenset(
    "the a an and or of to in for on by with shall must will be is are as at "
    "this that from any all not no it its if under section per".split()
)


def _tokens(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS]


@dataclass
class Chunk:
    doc: str
    page: int | None
    text: str
    tf: Counter = field(default_factory=Counter)

    def header(self) -> str:
        page = f" p.{self.page}" if self.page else ""
        return f"[{self.doc}{page}]"


class SearchIndex:
    """BM25-ish scoring over overlapping chunks with doc/page metadata."""

    K1 = 1.5
    B = 0.75

    def __init__(self, chunks: list[Chunk]):
        self.chunks = chunks
        self._df: Counter = Counter()
        total_len = 0
        for chunk in chunks:
            toks = _tokens(chunk.text)
            chunk.tf = Counter(toks)
            total_len += len(toks)
            for term in chunk.tf:
                self._df[term] += 1
        self._avg_len = (total_len / len(chunks)) if chunks else 1.0

    @classmethod
    def from_doc_tree(cls, tree: DocTree) -> "SearchIndex":
        chunks: list[Chunk] = []
        for doc in tree.docs:
            text = doc.full_text
            if not text.strip():
                continue
            pos = 0
            step = CHUNK_CHARS - CHUNK_OVERLAP
            while pos < len(text):
                piece = text[pos : pos + CHUNK_CHARS]
                # Nearest page marker at or before this chunk.
                markers = _PAGE_RE.findall(text[: pos + 200])
                page = int(markers[-1]) if markers else None
                chunks.append(Chunk(doc=doc.name, page=page, text=piece))
                pos += step
        return cls(chunks)

    def _idf(self, term: str) -> float:
        df = self._df.get(term, 0)
        n = len(self.chunks)
        return math.log(1 + (n - df + 0.5) / (df + 0.5))

    def score(self, query: str, chunk: Chunk) -> float:
        score = 0.0
        doc_len = sum(chunk.tf.values()) or 1
        for term in set(_tokens(query)):
            tf = chunk.tf.get(term, 0)
            if tf == 0:
                continue
            idf = self._idf(term)
            score += idf * (tf * (self.K1 + 1)) / (
                tf + self.K1 * (1 - self.B + self.B * doc_len / self._avg_len)
            )
        return score

    def search(self, query: str, top_k: int = 10) -> list[tuple[float, Chunk]]:
        scored = [(self.score(query, c), c) for c in self.chunks]
        scored = [(s, c) for s, c in scored if s > 0]
        scored.sort(key=lambda sc: -sc[0])
        return scored[:top_k]

    def excerpts(self, query: str, budget_chars: int = 60_000) -> str:
        """Top-scoring excerpts under a character budget, each labeled with
        its source so writer citations stay resolvable. Falls back to the
        corpus head when nothing matches (never returns an empty context)."""
        picked: list[str] = []
        used = 0
        for _, chunk in self.search(query, top_k=200):
            block = f"{chunk.header()}\n{chunk.text}"
            if used + len(block) > budget_chars:
                if used == 0:
                    picked.append(block[:budget_chars])
                    used = budget_chars
                break
            picked.append(block)
            used += len(block)
        if not picked:
            head = [f"{c.header()}\n{c.text}" for c in self.chunks[: max(1, budget_chars // CHUNK_CHARS)]]
            return "\n\n".join(head)[:budget_chars]
        return "\n\n".join(picked)
