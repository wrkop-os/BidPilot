"""P(win) bid advisor — Phase 3 decoupling boundary from docs/ML_ADOPTION.md.

Single integration point: advisory_for(). Scoring order: trained model when
BIDPILOT_PWIN_MODEL points at a loadable artifact, else the deterministic
heuristic; any model failure silently falls back to the heuristic. The
output is an ADVISORY string on the eligibility report — it never changes
bid_recommendation and never gates.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from ..kb.store import KnowledgeBase
from ..models import EligibilityReport, NoticeMetadata

# Set-aside fragment -> certification, shared convention with discover.py.
from ..discover import SET_ASIDE_CERT_MAP

# Frozen data contract: the trainer and every model artifact use exactly
# this vector layout. Append-only — reordering breaks deployed models.
FEATURE_ORDER = [
    "set_aside_held",
    "naics_registered",
    "relevant_past_perf",
    "hard_blockers",
    "soft_risks",
    "missing_info",
    "confidence",
]

# FY2024 average of ~3.6 offerors per federal solicitation -> baseline
# P(win) ~ 0.28 before discriminators (kb.pro/MARKET_RESEARCH.md §4).
BASELINE_PWIN = 0.28
OUTCOMES_NAME = "ml_outcomes.jsonl"
VALID_OUTCOMES = ("won", "lost", "no_bid")


class PwinFeatures(BaseModel):
    set_aside_held: float = Field(default=0.0, description="1.0 if the notice's set-aside matches a held certification (or is unrestricted)")
    naics_registered: float = 0.0
    relevant_past_perf: float = Field(default=0.0, description="KB past-performance records sharing the notice NAICS, capped at 3")
    hard_blockers: float = 0.0
    soft_risks: float = 0.0
    missing_info: float = 0.0
    confidence: float = Field(default=0.0, description="Eligibility agent confidence 0-1")

    def vector(self) -> list[float]:
        return [getattr(self, name) for name in FEATURE_ORDER]


class PwinEstimate(BaseModel):
    p_win: float
    method: str  # heuristic | model | heuristic-fallback
    rationale: str
    features: PwinFeatures


def build_features(
    metadata: NoticeMetadata, report: EligibilityReport, kb: KnowledgeBase
) -> PwinFeatures:
    set_aside = (metadata.set_aside or "").lower()
    held = {c.upper() for c in kb.profile.socioeconomic_certifications}
    if not set_aside or "small business" in set_aside:
        set_aside_held = 1.0
    else:
        required = next(
            (cert for frag, cert in SET_ASIDE_CERT_MAP.items() if frag in set_aside), None
        )
        set_aside_held = 1.0 if (required is None or required.upper() in held) else 0.0
    naics = metadata.naics_code
    relevant_pp = sum(
        1 for pp in kb.data.past_performance if naics and naics in pp.naics_codes
    )
    return PwinFeatures(
        set_aside_held=set_aside_held,
        naics_registered=1.0 if naics and naics in kb.profile.naics_codes else 0.0,
        relevant_past_perf=float(min(relevant_pp, 3)),
        hard_blockers=float(len(report.hard_blockers)),
        soft_risks=float(len(report.soft_risks)),
        missing_info=float(len(report.missing_info)),
        confidence=float(report.confidence or 0.0),
    )


def heuristic_score(features: PwinFeatures) -> float:
    """Deterministic baseline (docs/ML_ADOPTION.md 'Heuristic')."""
    if features.hard_blockers > 0:
        return 0.03
    p = BASELINE_PWIN
    p += 0.06 * features.set_aside_held
    p += 0.03 * features.naics_registered
    p += 0.03 * features.relevant_past_perf          # capped at 3 upstream
    p -= 0.03 * features.soft_risks
    p -= 0.02 * features.missing_info
    return round(min(0.65, max(0.02, p)), 3)


def _promoted(model_path: str) -> None:
    """Fail-closed promotion check: the trainer's metrics sidecar must exist,
    say ships=true (Brier beat the heuristic), AND match the artifact's
    sha256 — joblib.load is pickle, so a swapped artifact is code execution,
    not just a bad score. No sidecar, no hash match, no service."""
    sidecar = Path(model_path).with_suffix(".metrics.json")
    metrics = json.loads(sidecar.read_text(encoding="utf-8"))
    if metrics.get("ships") is not True:
        raise RuntimeError("artifact not promoted (metrics ships!=true)")
    import hashlib

    actual = hashlib.sha256(Path(model_path).read_bytes()).hexdigest()
    if metrics.get("model_sha256") != actual:
        raise RuntimeError("artifact hash mismatch vs promotion record")


def score(features: PwinFeatures) -> PwinEstimate:
    model_path = os.environ.get("BIDPILOT_PWIN_MODEL")
    if model_path:
        try:
            import joblib

            _promoted(model_path)
            model = joblib.load(model_path)
            p = float(model.predict_proba([features.vector()])[0][1])
            return PwinEstimate(
                p_win=round(p, 3), method="model",
                rationale=f"trained model {Path(model_path).name}", features=features,
            )
        except Exception as exc:  # Phase-3 fallback: the pipeline never notices
            return PwinEstimate(
                p_win=heuristic_score(features), method="heuristic-fallback",
                rationale=f"model unavailable ({type(exc).__name__}); heuristic used",
                features=features,
            )
    return PwinEstimate(
        p_win=heuristic_score(features), method="heuristic",
        rationale="evidence-adjusted market baseline (no trained model configured)",
        features=features,
    )


def advisory_for(
    metadata: NoticeMetadata, report: EligibilityReport, kb: KnowledgeBase
) -> str:
    est = score(build_features(metadata, report, kb))
    return (
        f"P(win) advisory ~{est.p_win:.0%} ({est.method}; {est.rationale}). "
        "Advisory only — the bid/no-bid decision stays with the human gate."
    )


# -- Phase-2 outcome capture (labels for training) -----------------------------

def record_outcome(
    output_root: Path,
    notice_id: str,
    outcome: str,
    features: PwinFeatures,
    ts: Optional[float] = None,
) -> Path:
    if outcome not in VALID_OUTCOMES:
        raise ValueError(f"outcome must be one of {VALID_OUTCOMES}, got {outcome!r}")
    path = output_root / OUTCOMES_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "notice_id": notice_id,
            "outcome": outcome,
            "features": features.model_dump(),
            "ts": ts if ts is not None else time.time(),
        }) + "\n")
    return path


def load_outcomes(output_root: Path, latest_only: bool = True) -> list[dict]:
    """Outcome rows, newest-wins per notice by default.

    The file is an append-only audit trail, so a corrected report (or a
    double-click) leaves several rows for one notice. Training must see one
    label per bid — otherwise a Won-then-Lost correction feeds the model two
    contradictory examples, and a double-click double-weights one bid.
    """
    path = output_root / OUTCOMES_NAME
    if not path.exists():
        return []
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not latest_only:
        return rows
    latest: dict[str, dict] = {}
    for row in rows:  # file order is chronological; last write wins
        latest[row.get("notice_id", "")] = row
    return list(latest.values())
