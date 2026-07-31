"""The domain model as a second pair of eyes over the shredder's output.

Invariant 4 says recall beats cost for the shredder, so the trained model is
wired in the direction that invariant points: it does not filter text away from
the LLM, it re-reads the corpus afterwards and reports requirement-shaped
sentences the compliance matrix does not cover.

That choice is deliberate. Using a 0.99-recall screen as a pre-filter would
save tokens and silently discard roughly one requirement in a hundred; using it
as a net can only add coverage. A missed requirement is an unaddressed
requirement, and unaddressed requirements are how proposals get eliminated —
the arithmetic never favors trading recall for tokens here.

The model is local, deterministic, and free. Running it over the whole corpus
costs milliseconds, so there is no reason not to run it on every shred.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .requirements_model import RequirementClassifier, load

# Sentence splitting that survives solicitation text: abbreviations, clause
# numbers (52.222-43), and section references (§L.4) must not end a sentence.
_ABBREV = r"(?<!\bNo)(?<!\bSec)(?<!\bFAR)(?<!\bApprox)(?<!\bMr)(?<!\bMs)(?<!\bDr)"
_SENTENCE_END = re.compile(rf"{_ABBREV}(?<![A-Z])(?<!\d)[.!?](?:\s+|$)")

MIN_SENTENCE_CHARS = 25
MAX_SENTENCE_CHARS = 600


@dataclass
class MissedCandidate:
    text: str
    doc: str
    confidence: float

    def render(self) -> str:
        return f"({self.doc}) {self.text}"


def split_sentences(text: str) -> list[str]:
    if not text:
        return []
    out: list[str] = []
    for line in text.splitlines():
        line = " ".join(line.split())
        if not line:
            continue
        start = 0
        for match in _SENTENCE_END.finditer(line):
            piece = line[start:match.end()].strip()
            if piece:
                out.append(piece)
            start = match.end()
        tail = line[start:].strip()
        if tail:
            out.append(tail)
    return out


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", text.lower())


def _covered(sentence: str, covered_norms: list[str]) -> bool:
    """Is this sentence already represented in the matrix?

    Matrix entries are verbatim extracts, so containment either way is the
    right test — the LLM may have captured a longer clause containing this
    sentence, or a trimmed version of it.
    """
    norm = _normalize(sentence)
    if not norm:
        return True
    for existing in covered_norms:
        if norm in existing or existing in norm:
            return True
        # Near-miss on long extracts: high token overlap counts as covered.
        a, b = set(norm.split()), set(existing.split())
        if a and b and len(a & b) / len(a | b) >= 0.75:
            return True
    return False


def find_missed(doc_tree, matrix, classifier: Optional[RequirementClassifier] = None,
                limit: int = 40) -> list[MissedCandidate]:
    """Requirement-shaped sentences absent from the matrix.

    Returns [] when no promoted model is configured — the pipeline behaves
    exactly as it did before, with no fallback logic to get wrong.
    """
    classifier = classifier or load()
    if classifier is None or not getattr(classifier, "can_screen", False):
        return []

    covered_norms = [_normalize(r.verbatim_text) for r in (matrix.requirements or [])]
    covered_norms = [n for n in covered_norms if n]

    candidates: list[MissedCandidate] = []
    seen: set[str] = set()
    for doc in getattr(doc_tree, "docs", []) or []:
        sentences = [
            s for s in split_sentences(getattr(doc, "full_text", "") or "")
            if MIN_SENTENCE_CHARS <= len(s) <= MAX_SENTENCE_CHARS
        ]
        if not sentences:
            continue
        for sentence, prediction in zip(sentences, classifier.predict_many(sentences)):
            if not prediction.is_requirement:
                continue
            key = _normalize(sentence)
            if key in seen or _covered(sentence, covered_norms):
                continue
            seen.add(key)
            candidates.append(MissedCandidate(
                text=sentence, doc=getattr(doc, "name", "?"),
                confidence=round(1.0 - prediction.p_none, 4),
            ))

    # Most confident first: a reviewer works down the list and stops when it
    # stops being useful, so ordering is the whole ergonomics of this feature.
    candidates.sort(key=lambda c: -c.confidence)
    return candidates[:limit]
