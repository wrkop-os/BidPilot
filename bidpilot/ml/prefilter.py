"""Opt-in: use the domain model to cut what the shredder sends to the LLM.

This is the cost lever, and it is off by default on purpose.

`recall_net.py` runs the model in the safe direction — after extraction, adding
coverage. This module runs it in the direction that saves money: dropping
sentences before they are ever windowed and sent. That trades recall for
tokens, and invariant 4 says recall wins on this stage, so the trade is never
made silently.

Turn it on with `BIDPILOT_REQ_PREFILTER=1`, and read what you are buying first:
the promotion record's `held_out.requirement_recall` is the measured
worst-split recall of the screen. At 0.99 that is roughly one requirement in a
hundred dropped before any model sees it. On a solicitation with 300
requirements, three go missing, and nothing downstream can recover them because
the text never arrived.

Two safeguards make that trade survivable rather than reckless:

  * Only sentences the model is confident about are dropped, using the same
    threshold the promotion record was measured at.
  * Dropped text is not discarded. It is recorded and reported, so a reviewer
    can see exactly what the filter removed and how much was saved.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

from .recall_net import split_sentences
from .requirements_model import RequirementClassifier, load

ENV_ENABLED = "BIDPILOT_REQ_PREFILTER"

# Text short enough to be a heading, a page number, or a table cell is kept
# regardless: it costs almost nothing and often carries the section context a
# following requirement is anchored to.
KEEP_SHORTER_THAN = 25


def enabled() -> bool:
    return os.environ.get(ENV_ENABLED, "").strip() in ("1", "true", "yes", "on")


@dataclass
class PrefilterResult:
    text: str
    kept_sentences: int = 0
    dropped_sentences: int = 0
    chars_before: int = 0
    chars_after: int = 0
    dropped_samples: list[str] = field(default_factory=list)

    @property
    def reduction(self) -> float:
        if not self.chars_before:
            return 0.0
        return 1.0 - (self.chars_after / self.chars_before)

    def summary(self) -> str:
        return (
            f"prefilter: {self.chars_before:,} -> {self.chars_after:,} chars "
            f"({self.reduction:.0%} smaller), {self.dropped_sentences} sentences "
            f"dropped, {self.kept_sentences} kept"
        )


def prefilter_text(text: str,
                   classifier: Optional[RequirementClassifier] = None,
                   sample_limit: int = 25) -> PrefilterResult:
    """Drop sentences the model is confident bind nobody.

    Returns the original text unchanged when no promoted model is available or
    the feature is off, so callers need no branching of their own.
    """
    result = PrefilterResult(text=text, chars_before=len(text or ""))
    result.chars_after = result.chars_before
    if not text or not enabled():
        return result
    classifier = classifier or load()
    if classifier is None or not classifier.can_screen:
        return result

    sentences = split_sentences(text)
    if not sentences:
        return result

    # Short fragments bypass the model entirely — see KEEP_SHORTER_THAN.
    judged_idx = [i for i, s in enumerate(sentences) if len(s) >= KEEP_SHORTER_THAN]
    verdicts = classifier.predict_many([sentences[i] for i in judged_idx])
    drop = {i for i, p in zip(judged_idx, verdicts) if not p.is_requirement}

    kept = [s for i, s in enumerate(sentences) if i not in drop]
    result.text = "\n".join(kept)
    result.kept_sentences = len(kept)
    result.dropped_sentences = len(drop)
    result.chars_after = len(result.text)
    result.dropped_samples = [sentences[i] for i in sorted(drop)][:sample_limit]
    return result


def prefilter_notice() -> str:
    """What to tell the operator when this is on. Names the actual risk."""
    classifier = load()
    recall = None
    if classifier is not None:
        recall = (classifier.metrics.get("held_out") or {}).get("requirement_recall")
    measured = (f"Measured worst-split screen recall is {recall:.3f}, so roughly "
                f"{(1 - recall) * 1000:.0f} requirements in every 1,000 are "
                "dropped before any model sees them."
                if isinstance(recall, (int, float)) else
                "The promotion record does not report a recall figure.")
    return (
        f"Requirement prefilter ENABLED ({ENV_ENABLED}=1). {measured} "
        "Dropped text cannot be recovered downstream. Invariant 4 prefers "
        "recall over cost on this stage — unset the variable to restore it."
    )
