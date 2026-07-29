"""Fail-closed promotion gates: unpromoted artifacts never serve, and the
custom-LLM gate applies G2 recall before any tier flip."""

import json
from pathlib import Path

import pytest

from bidpilot.kb.store import load_kb
from bidpilot.ml.pwin import PwinFeatures, record_outcome, score
from bidpilot.mle.promotion import CUSTOM_LLM_GATES, assert_promotion_ready, gate_custom_llm

REPO = Path(__file__).resolve().parent.parent
DEMO_EXTRACTED = REPO / "evals" / "corpus_demo" / "sample_extracted_matrix.json"
DEMO_GOLD = REPO / "evals" / "corpus_demo" / "gold_matrix.csv"


def test_assert_promotion_ready():
    assert_promotion_ready({"recall": 0.99}, CUSTOM_LLM_GATES)
    with pytest.raises(ValueError, match="Failed promotion gates"):
        assert_promotion_ready({"recall": 0.90}, CUSTOM_LLM_GATES)
    with pytest.raises(ValueError, match="missing required gates"):
        assert_promotion_ready({}, CUSTOM_LLM_GATES)


def test_custom_llm_gate_passes_on_demo_corpus(tmp_path):
    record = gate_custom_llm(DEMO_EXTRACTED, DEMO_GOLD, tmp_path / "promo.json")
    assert record["passed"] is True
    assert record["metrics"]["recall"] >= 0.98
    saved = json.loads((tmp_path / "promo.json").read_text())
    assert saved["passed"] is True and saved["gates"]["recall"] == ["min", 0.98]


def test_custom_llm_gate_fails_closed_on_missed_requirement(tmp_path):
    gold = tmp_path / "gold.csv"
    rows = DEMO_GOLD.read_text().rstrip() + (
        '\nR-999,content,"RFP p.99","The contractor shall maintain quantum flux '
        'capacitor containment integrity at all times.",QF,\n'
    )
    gold.write_text(rows)
    record = gate_custom_llm(DEMO_EXTRACTED, gold, None)
    assert record["passed"] is False
    assert "recall" in record["reason"]
    assert any("quantum flux" in m for m in record["missed"])


def test_unpromoted_pwin_artifact_never_serves(tmp_path, monkeypatch):
    model = tmp_path / "pwin.joblib"
    model.write_bytes(b"not a real model")   # exists, but no metrics sidecar
    monkeypatch.setenv("BIDPILOT_PWIN_MODEL", str(model))
    est = score(PwinFeatures(set_aside_held=1.0))
    assert est.method == "heuristic-fallback"

    # Sidecar that explicitly failed the ship gate: still refuses.
    model.with_suffix(".metrics.json").write_text(json.dumps({"ships": False}))
    est = score(PwinFeatures(set_aside_held=1.0))
    assert est.method == "heuristic-fallback"


def test_promoted_pwin_artifact_serves_with_stamp(tmp_path, monkeypatch):
    pytest.importorskip("sklearn")
    from bidpilot.ml.train_pwin import train

    for i in range(60):
        won = i % 2 == 0
        record_outcome(
            tmp_path, f"{i:032x}", "won" if won else "lost",
            PwinFeatures(set_aside_held=1.0 if won else 0.0,
                         relevant_past_perf=3.0 if won else 0.0,
                         soft_risks=0.0 if won else 4.0),
            ts=float(i),
        )
    model = tmp_path / "pwin.joblib"
    metrics = train(tmp_path, model)
    # Reproducibility stamp travels with the artifact.
    assert len(metrics["dataset_sha256"]) == 64
    assert metrics["trained_at"] > 0
    monkeypatch.setenv("BIDPILOT_PWIN_MODEL", str(model))
    assert score(PwinFeatures(set_aside_held=1.0, relevant_past_perf=3.0)).method == "model"
