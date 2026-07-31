"""The domain model: a trained requirement classifier for the shredder.

What it is
----------
A supervised text classifier over federal solicitation language. Given one
sentence it answers two things: is this a binding requirement, and which of
the four RequirementCategory kinds is it. Trained in-repo, served in-process,
no API call and no network.

What it is NOT
--------------
It is not a proposal writer. Drafting, win strategy, and red-team review need
a large general model; see docs/DOMAIN_MODEL.md for why that cannot be trained
here and what it takes. This model targets the one stage where a narrow expert
genuinely beats a general model on cost and latency: the shredder, which reads
the full corpus on every run (invariant 4) and is the pipeline's highest-volume
model call by a wide margin.

How it is allowed to serve
--------------------------
Never by default, and never on its own say-so. Recall is the whole game for the
shredder — a missed requirement is an unaddressed requirement, which is how
proposals get eliminated — so the promotion gate is a recall floor, declared
before training in `mle/promotion.py` and checked at load time against a
metrics sidecar bound to the artifact's sha256. joblib.load is pickle, so a
swapped artifact is arbitrary code execution, not merely a bad score.

Absent a promoted artifact the pipeline behaves exactly as before. This model
can only ever be an accelerator in front of the LLM, never a silent
replacement for it.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .corpus import CATEGORIES

ENV_MODEL_PATH = "BIDPILOT_REQ_MODEL"

# Declared before training, applied after — never tuned to make a finished
# artifact pass.
#
# The artifact earns TWO capabilities independently, because they carry very
# different consequences and there is no reason a model that can do one must
# also do the other:
#
#   SCREEN      "is this a requirement?" A miss is an unaddressed requirement,
#               which is how proposals get eliminated. Gated on recall.
#   CATEGORIZE  "which of the four kinds?" A miss routes the requirement to
#               the wrong volume owner — a reviewer correction, not a lost
#               bid. Gated on accuracy, separately.
#
# A model that passes SCREEN and fails CATEGORIZE serves as a pre-filter and
# the LLM keeps categorization. That is a narrower job, not a lowered bar.
SCREEN_GATES = {
    "requirement_recall": ("min", 0.95),
}
CATEGORIZE_GATES = {
    "category_accuracy": ("min", 0.80),
}
REQUIREMENT_GATES = {**SCREEN_GATES, **CATEGORIZE_GATES}

# Recall-first decision rule: call a sentence "not a requirement" only when
# the model is confident it is not. Below this, the sentence survives to the
# LLM. The threshold is chosen at training time on held-back TRAIN families
# and stored in the promotion record — never fitted on the test side.
DEFAULT_NONE_THRESHOLD = 0.90


@dataclass
class Prediction:
    is_requirement: bool
    category: str                 # one of CATEGORIES
    confidence: float
    method: str                   # model | unavailable
    p_none: float = 0.0           # the screen's actual evidence


class ModelUnavailable(RuntimeError):
    """No promoted artifact. Callers fall back to the LLM path."""


def _verify_promoted(model_path: Path) -> dict:
    """A model serves only with a promotion record proving it beat its gate,
    and only if that record describes THIS artifact."""
    sidecar = model_path.with_suffix(".metrics.json")
    if not sidecar.exists():
        raise ModelUnavailable(f"no promotion record beside {model_path.name}")
    metrics = json.loads(sidecar.read_text(encoding="utf-8"))
    # Screening is the minimum useful capability; an artifact that cannot
    # screen has earned nothing and must not load at all.
    if metrics.get("ships_screen") is not True:
        raise ModelUnavailable("artifact not promoted for screening")
    actual = hashlib.sha256(model_path.read_bytes()).hexdigest()
    if metrics.get("model_sha256") != actual:
        raise ModelUnavailable("artifact hash does not match its promotion record")
    return metrics


class RequirementClassifier:
    """Loads a promoted artifact, or refuses. Never trains at import time."""

    def __init__(self, model_path: Path):
        import joblib

        self.model_path = Path(model_path)
        self.metrics = _verify_promoted(self.model_path)
        self._pipeline = joblib.load(self.model_path)
        self.none_threshold = float(
            self.metrics.get("none_threshold") or DEFAULT_NONE_THRESHOLD)
        # Which jobs this artifact actually earned.
        self.can_screen = bool(self.metrics.get("ships_screen"))
        self.can_categorize = bool(self.metrics.get("ships_categorize"))

    def _decide(self, proba, classes) -> Prediction:
        index = {c: i for i, c in enumerate(classes)}
        p_none = float(proba[index["none"]]) if "none" in index else 0.0
        # Only a confident "none" removes a sentence from the LLM's view.
        if p_none >= self.none_threshold:
            return Prediction(False, "none", round(p_none, 4), "model", round(p_none, 4))
        best_req = max((c for c in classes if c != "none"),
                       key=lambda c: proba[index[c]], default="content")
        return Prediction(
            is_requirement=True,
            category=str(best_req) if self.can_categorize else "",
            confidence=round(float(proba[index[best_req]]), 4),
            method="model",
            p_none=round(p_none, 4),
        )

    def predict(self, text: str) -> Prediction:
        text = (text or "").strip()
        if not text:
            return Prediction(False, "none", 1.0, "model", 1.0)
        proba = self._pipeline.predict_proba([text])[0]
        return self._decide(proba, list(self._pipeline.classes_))

    def predict_many(self, texts: list[str]) -> list[Prediction]:
        if not texts:
            return []
        classes = list(self._pipeline.classes_)
        return [self._decide(row, classes)
                for row in self._pipeline.predict_proba(texts)]


_cache: dict[str, Optional[RequirementClassifier]] = {}


def load(model_path: Optional[str] = None) -> Optional[RequirementClassifier]:
    """The promoted classifier, or None. Never raises: a missing or rejected
    model must degrade to the LLM path, not break a run."""
    path = model_path or os.environ.get(ENV_MODEL_PATH)
    if not path:
        return None
    if path in _cache:
        return _cache[path]
    try:
        classifier = RequirementClassifier(Path(path))
    except Exception:          # noqa: BLE001 — unavailable is a normal state
        classifier = None
    _cache[path] = classifier
    return classifier


def reset_cache() -> None:
    _cache.clear()


def build_pipeline():
    """The estimator. Word + character n-grams, because solicitation language
    is distinguished as much by morphology ('shall', '-point', 'not to
    exceed') as by vocabulary, and character n-grams survive the OCR damage
    this corpus routinely carries."""
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline, make_union

    features = make_union(
        TfidfVectorizer(ngram_range=(1, 2), min_df=1, sublinear_tf=True,
                        strip_accents="unicode", lowercase=True),
        TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=2,
                        sublinear_tf=True, lowercase=True),
    )
    # class_weight balanced: "none" dominates any real solicitation, and an
    # unweighted fit buys accuracy by predicting it — which is precisely the
    # failure mode (missed requirements) the gate exists to prevent.
    return Pipeline([
        ("features", features),
        ("clf", CalibratedClassifierCV(
            LogisticRegression(max_iter=2000, class_weight="balanced", C=4.0),
            cv=3, method="sigmoid",
        )),
    ])


def categories() -> tuple[str, ...]:
    return CATEGORIES
