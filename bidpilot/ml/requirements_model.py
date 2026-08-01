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
        bundle = joblib.load(self.model_path)
        if isinstance(bundle, dict):
            self._screen = bundle["screen"]
            self._category = bundle.get("category")
        else:                       # single-model artifact from an older train
            self._screen = bundle
            self._category = None
        self.none_threshold = float(
            self.metrics.get("none_threshold") or DEFAULT_NONE_THRESHOLD)
        # Which jobs this artifact actually earned.
        self.can_screen = bool(self.metrics.get("ships_screen"))
        self.can_categorize = bool(self.metrics.get("ships_categorize"))

    def _p_none(self, proba, classes) -> float:
        index = {c: i for i, c in enumerate(classes)}
        return float(proba[index["none"]]) if "none" in index else 0.0

    def predict(self, text: str) -> Prediction:
        return self.predict_many([text])[0] if (text or "").strip() else \
            Prediction(False, "none", 1.0, "model", 1.0)

    def predict_many(self, texts: list[str]) -> list[Prediction]:
        if not texts:
            return []
        classes = list(self._screen.classes_)
        screen_probas = self._screen.predict_proba(texts)

        # Categorize only what survives the screen, and only if that job was
        # earned — one batched call rather than one per sentence.
        survivors = [i for i, row in enumerate(screen_probas)
                     if self._p_none(row, classes) < self.none_threshold]
        categories: dict[int, str] = {}
        if survivors and self.can_categorize and self._category is not None:
            predicted = self._category.predict([texts[i] for i in survivors])
            categories = dict(zip(survivors, (str(c) for c in predicted)))

        out: list[Prediction] = []
        for i, row in enumerate(screen_probas):
            p_none = round(self._p_none(row, classes), 4)
            if i not in set(survivors):
                out.append(Prediction(False, "none", p_none, "model", p_none))
            else:
                out.append(Prediction(
                    is_requirement=True,
                    category=categories.get(i, ""),
                    confidence=round(1.0 - p_none, 4),
                    method="model",
                    p_none=p_none,
                ))
        return out


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


def _features():
    """Word + character n-grams. Solicitation language is distinguished as much
    by morphology ('shall', '12-point', 'not to exceed') as by vocabulary, and
    character n-grams survive the OCR damage this corpus routinely carries."""
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.pipeline import make_union

    return make_union(
        TfidfVectorizer(ngram_range=(1, 2), min_df=1, sublinear_tf=True,
                        strip_accents="unicode", lowercase=True),
        TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=2,
                        sublinear_tf=True, lowercase=True),
    )


def _estimator(C: float):
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.linear_model import LogisticRegression

    # class_weight balanced: "none" dominates any real solicitation, and an
    # unweighted fit buys accuracy by predicting it — precisely the failure
    # mode (missed requirements) the screen gate exists to prevent.
    return CalibratedClassifierCV(
        LogisticRegression(max_iter=2000, class_weight="balanced", C=C),
        cv=3, method="sigmoid",
    )


def build_screen_pipeline():
    """Requirement vs not. Binary, because that is the decision it makes."""
    from sklearn.pipeline import Pipeline

    return Pipeline([("features", _features()), ("clf", _estimator(4.0))])


def build_category_pipeline():
    """Which of the four kinds — trained on REQUIREMENTS ONLY.

    The first version was one flat 5-way model, and it read format
    requirements as content or evaluation about as often as it got them right
    (21 of 53 on held-out families). "none" is the overwhelming majority class
    in any solicitation, so a joint model spends its capacity separating
    requirements from boilerplate and has little left for the much subtler
    distinction between a page limit and a staffing narrative. Splitting the
    two jobs lets each fit the decision it actually makes.
    """
    from sklearn.pipeline import Pipeline

    return Pipeline([("features", _features()), ("clf", _estimator(8.0))])


# Kept as the screen builder's name for callers that only need one model.
build_pipeline = build_screen_pipeline


def categories() -> tuple[str, ...]:
    return CATEGORIES
