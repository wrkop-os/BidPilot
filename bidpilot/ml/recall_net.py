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


# A line that does not end in terminal punctuation is a wrapped continuation,
# not a sentence. PDF and DOCX extraction wraps constantly, so splitting on
# newlines alone turns one requirement into several fragments -- each too short
# to classify and none of them quotable as verbatim binding language.
_ENDS_SENTENCE = re.compile(r"[.!?:;]\s*$")
# Blank lines, bullets, numbered headings and ALL-CAPS headers start something
# new even when the previous line did not terminate.
_STARTS_BLOCK = re.compile(
    r"^\s*(?:[-*\u2022]|\(?[a-z0-9]{1,3}[.)]\s|[A-Z][A-Z \t]{6,}$|SECTION\b|ATTACHMENT\b)",
)


def _unwrap(text: str) -> list[str]:
    """Rejoin lines that a renderer wrapped mid-sentence."""
    blocks: list[str] = []
    current = ""
    for raw in text.splitlines():
        line = " ".join(raw.split())
        if not line:
            if current:
                blocks.append(current)
                current = ""
            continue
        if current and not _STARTS_BLOCK.match(raw):
            current = f"{current} {line}"
        else:
            if current:
                blocks.append(current)
            current = line
        if _ENDS_SENTENCE.search(current):
            blocks.append(current)
            current = ""
    if current:
        blocks.append(current)
    return blocks


def split_sentences(text: str) -> list[str]:
    if not text:
        return []
    out: list[str] = []
    for line in _unwrap(text):
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
    """Casefold, drop punctuation, and COLLAPSE whitespace.

    Collapsing matters: coverage is decided partly by substring containment,
    and PDF extraction routinely emits doubled spaces and stray newlines. A
    matrix entry reading "the offeror  shall submit" would not contain the same
    sentence re-extracted as "the offeror shall submit", so a requirement
    already captured would be reported as missed. Whitespace-only input
    normalizes to the empty string rather than a run of spaces.
    """
    return " ".join(re.sub(r"[^a-z0-9 ]", "", (text or "").lower()).split())


COVERAGE_OVERLAP = 0.75


class _CoverageIndex:
    """Which matrix entries could possibly cover a given sentence.

    Comparing every sentence against every matrix entry is O(sentences x
    requirements) with set algebra in the inner loop, and it showed: a large
    solicitation (6,000 sentences, 600 requirements) took 11 seconds to
    produce 40 candidates. An inverted index over tokens turns that into a
    lookup, because two texts that share no token can neither contain one
    another nor clear the overlap bar — so they never need comparing.
    """

    def __init__(self, texts: list[str]):
        self.norms: list[str] = []
        self.tokens: list[set[str]] = []
        self.postings: dict[str, list[int]] = {}
        for text in texts:
            norm = _normalize(text)
            if not norm:
                continue
            index = len(self.norms)
            self.norms.append(norm)
            words = set(norm.split())
            self.tokens.append(words)
            for word in words:
                self.postings.setdefault(word, []).append(index)

    def covers(self, sentence: str) -> bool:
        norm = _normalize(sentence)
        if not norm:
            return True
        words = set(norm.split())
        if not words:
            return True
        # Only entries sharing at least one token can qualify. Containment
        # implies sharing every token of the shorter text, so nothing that
        # would have matched is skipped.
        candidates: set[int] = set()
        for word in words:
            candidates.update(self.postings.get(word, ()))
        for i in candidates:
            existing = self.norms[i]
            if norm in existing or existing in norm:
                return True
            other = self.tokens[i]
            union = len(words | other)
            if union and len(words & other) / union >= COVERAGE_OVERLAP:
                return True
        return False


def _covered(sentence: str, covered_norms: list[str]) -> bool:
    """Kept for direct callers and tests; the batch path uses _CoverageIndex."""
    return _CoverageIndex(covered_norms).covers(sentence)


def find_missed(doc_tree, matrix, classifier: Optional[RequirementClassifier] = None,
                limit: int = 40) -> list[MissedCandidate]:
    """Requirement-shaped sentences absent from the matrix.

    Returns [] when no promoted model is configured — the pipeline behaves
    exactly as it did before, with no fallback logic to get wrong.
    """
    classifier = classifier or load()
    if classifier is None or not getattr(classifier, "can_screen", False):
        return []

    index = _CoverageIndex([r.verbatim_text for r in (matrix.requirements or [])])

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
            if key in seen or index.covers(sentence):
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
