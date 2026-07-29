"""P(win) advisor: data contract, heuristic determinism, model fallback,
outcome capture, and the train-vs-baseline ship gate (docs/ML_ADOPTION.md)."""

import time
from pathlib import Path

import pytest

from bidpilot.kb.store import load_kb
from bidpilot.models import BidRecommendation, EligibilityReport, NoticeMetadata
from bidpilot.ml.pwin import (
    FEATURE_ORDER,
    PwinFeatures,
    advisory_for,
    build_features,
    heuristic_score,
    load_outcomes,
    record_outcome,
    score,
)

EXAMPLE_KB = Path(__file__).resolve().parent.parent / "kb.example"


def _report(hard=0, soft=0, missing=0, confidence=0.8):
    return EligibilityReport(
        hard_blockers=[f"hb{i}" for i in range(hard)],
        soft_risks=[f"sr{i}" for i in range(soft)],
        missing_info=[f"mi{i}" for i in range(missing)],
        bid_recommendation=BidRecommendation.BID,
        confidence=confidence,
        rationale="test",
    )


def _meta(set_aside="Service-Disabled Veteran-Owned Small Business Set-Aside", naics="541511"):
    return NoticeMetadata(notice_id="a" * 32, set_aside=set_aside, naics_code=naics)


def test_feature_contract_matches_vector_order():
    features = build_features(_meta(), _report(soft=1), load_kb(str(EXAMPLE_KB)))
    vec = features.vector()
    assert len(vec) == len(FEATURE_ORDER)
    assert features.set_aside_held == 1.0          # SDVOSB held in kb.example
    assert features.naics_registered == 1.0
    assert vec[FEATURE_ORDER.index("soft_risks")] == 1.0


def test_heuristic_is_deterministic_and_bounded():
    f = build_features(_meta(), _report(soft=2, missing=1), load_kb(str(EXAMPLE_KB)))
    p1, p2 = heuristic_score(f), heuristic_score(f)
    assert p1 == p2
    assert 0.02 <= p1 <= 0.65
    # Hard blockers floor the score.
    fb = build_features(_meta(), _report(hard=1), load_kb(str(EXAMPLE_KB)))
    assert heuristic_score(fb) == 0.03


def test_unheld_set_aside_lowers_score():
    kb = load_kb(str(EXAMPLE_KB))
    held = heuristic_score(build_features(_meta(), _report(), kb))
    unheld = heuristic_score(
        build_features(_meta(set_aside="8(a) Set-Aside"), _report(), kb)
    )
    assert unheld < held


def test_model_failure_falls_back_to_heuristic(monkeypatch, tmp_path):
    monkeypatch.setenv("BIDPILOT_PWIN_MODEL", str(tmp_path / "nonexistent.joblib"))
    est = score(build_features(_meta(), _report(), load_kb(str(EXAMPLE_KB))))
    assert est.method == "heuristic-fallback"
    assert 0.02 <= est.p_win <= 0.65


def test_advisory_never_sounds_like_a_gate():
    text = advisory_for(_meta(), _report(soft=1), load_kb(str(EXAMPLE_KB)))
    assert "P(win) advisory" in text
    assert "human gate" in text


def test_outcome_capture_roundtrip(tmp_path):
    f = PwinFeatures(set_aside_held=1.0)
    record_outcome(tmp_path, "a" * 32, "won", f, ts=100.0)
    record_outcome(tmp_path, "b" * 32, "no_bid", f, ts=101.0)
    rows = load_outcomes(tmp_path)
    assert [r["outcome"] for r in rows] == ["won", "no_bid"]
    assert rows[0]["features"]["set_aside_held"] == 1.0
    with pytest.raises(ValueError):
        record_outcome(tmp_path, "c" * 32, "maybe", f)


def test_training_refuses_insufficient_data(tmp_path):
    from bidpilot.ml.train_pwin import train

    for i in range(5):
        record_outcome(tmp_path, f"{i:032x}", "won", PwinFeatures(), ts=i)
    with pytest.raises(SystemExit, match="need >= 30"):
        train(tmp_path, tmp_path / "model.joblib")


def test_train_and_serve_model(tmp_path, monkeypatch):
    pytest.importorskip("sklearn")
    from bidpilot.ml.train_pwin import train

    # Separable synthetic history: strong evidence wins, weak evidence loses.
    for i in range(60):
        won = i % 2 == 0
        f = PwinFeatures(
            set_aside_held=1.0 if won else 0.0,
            naics_registered=1.0 if won else 0.0,
            relevant_past_perf=3.0 if won else 0.0,
            soft_risks=0.0 if won else 4.0,
            confidence=0.9 if won else 0.3,
        )
        record_outcome(tmp_path, f"{i:032x}", "won" if won else "lost", f, ts=float(i))
    model_path = tmp_path / "pwin.joblib"
    metrics = train(tmp_path, model_path)
    assert model_path.exists()
    assert metrics["feature_order"] == FEATURE_ORDER
    assert metrics["ships"] is True  # separable data must beat the heuristic

    monkeypatch.setenv("BIDPILOT_PWIN_MODEL", str(model_path))
    strong = PwinFeatures(set_aside_held=1.0, naics_registered=1.0,
                          relevant_past_perf=3.0, confidence=0.9)
    est = score(strong)
    assert est.method == "model"
    assert est.p_win > 0.5


def test_eligibility_stage_attaches_advisory(tmp_path):
    from bidpilot.orchestrator import run
    from bidpilot.state import Stage
    from test_orchestrator_e2e import _make_ctx

    ctx = _make_ctx(tmp_path)
    state = run(ctx, stop_after=Stage.ELIGIBILITY)
    assert state.eligibility.pwin_advisory
    assert "P(win) advisory" in state.eligibility.pwin_advisory
    # The advisory is informational: the recommendation is untouched.
    assert state.eligibility.bid_recommendation == BidRecommendation.BID
