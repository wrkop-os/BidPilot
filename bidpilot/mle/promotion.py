"""Fail-closed promotion gates (mle-workflow discipline).

Both ML loops share one rule: an artifact serves only with a promotion
record proving it beat its gate. The P(win) scorer enforces this at load
time via the trainer's metrics sidecar; the custom-LLM loop gates on the
Phase-0 eval harness (G2 recall) via `bidpilot mle gate`, which writes the
promotion record operators check before flipping BIDPILOT_CUSTOM_LLM_TIERS.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

# Gate name -> (direction, threshold). Declared before training, applied
# after — never tuned to make a finished artifact pass.
CUSTOM_LLM_GATES = {
    "recall": ("min", 0.98),        # G2: misses are catastrophic
}


def assert_promotion_ready(metrics: dict, gates: dict) -> None:
    missing = sorted(name for name in gates if name not in metrics)
    if missing:
        raise ValueError(f"Promotion metrics missing required gates: {missing}")
    failures = {
        name: metrics[name]
        for name, (direction, threshold) in gates.items()
        if (direction == "min" and metrics[name] < threshold)
        or (direction == "max" and metrics[name] > threshold)
    }
    if failures:
        raise ValueError(f"Failed promotion gates: {failures}")


def gate_custom_llm(extracted_path: Path, gold_path: Path,
                    record_out: Path | None = None) -> dict:
    """Score a custom model's extracted matrix against gold and write the
    promotion record. Returns the record; `passed` is the ship decision."""
    try:
        from evals.harness import load_extracted_json, load_gold_csv, score_matrix
    except ImportError as exc:
        raise SystemExit(f"evals/ package not on path — run from the repo root ({exc})")

    score = score_matrix(load_extracted_json(extracted_path), load_gold_csv(gold_path))
    metrics = {
        "recall": score.recall,
        "precision": score.precision,
        "gold_total": score.gold_total,
        "extracted_total": score.extracted_total,
    }
    record = {
        "gates": {k: list(v) for k, v in CUSTOM_LLM_GATES.items()},
        "metrics": metrics,
        "missed": score.missed,
        "extracted": str(extracted_path),
        "gold": str(gold_path),
        "ts": time.time(),
    }
    try:
        assert_promotion_ready(metrics, CUSTOM_LLM_GATES)
        record["passed"] = True
    except ValueError as exc:
        record["passed"] = False
        record["reason"] = str(exc)
    if record_out:
        record_out.parent.mkdir(parents=True, exist_ok=True)
        record_out.write_text(json.dumps(record, indent=2), encoding="utf-8")
    return record
