"""Train the P(win) baseline model (Phase 4, docs/ML_ADOPTION.md).

Usage: python -m bidpilot.ml.train_pwin <output_root> <model_out.joblib>

Reproducibility rules: fixed seed, chronological 80/20 split (random splits
leak market drift), feature matrix shape (n, len(FEATURE_ORDER)). Refuses
to train on fewer than MIN_LABELED outcomes, and reports Brier score
against the heuristic baseline — a model that cannot beat the heuristic
does not ship (the metrics file is the evidence).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .pwin import FEATURE_ORDER, PwinFeatures, heuristic_score, load_outcomes

MIN_LABELED = 30
SEED = 13


def train(output_root: Path, model_out: Path) -> dict:
    try:
        import joblib
        from sklearn.linear_model import LogisticRegression
        from sklearn.metrics import accuracy_score, brier_score_loss
    except ImportError as exc:
        raise SystemExit(
            f"Training needs the ml extras: pip install -e '.[ml]' ({exc})"
        )

    rows = [r for r in load_outcomes(output_root) if r["outcome"] in ("won", "lost")]
    if len(rows) < MIN_LABELED:
        raise SystemExit(
            f"Only {len(rows)} labeled outcomes (won/lost); need >= {MIN_LABELED}. "
            "Keep capturing with `bidpilot outcome` — the heuristic serves until then."
        )

    rows.sort(key=lambda r: r.get("ts", 0))          # chronological, no leakage
    split = int(len(rows) * 0.8)
    train_rows, test_rows = rows[:split], rows[split:]

    def xy(batch):
        X = [PwinFeatures(**r["features"]).vector() for r in batch]   # (n, 7)
        y = [1 if r["outcome"] == "won" else 0 for r in batch]
        return X, y

    X_train, y_train = xy(train_rows)
    X_test, y_test = xy(test_rows)

    model = LogisticRegression(random_state=SEED, max_iter=1000)
    model.fit(X_train, y_train)

    proba = [p[1] for p in model.predict_proba(X_test)]
    heur = [heuristic_score(PwinFeatures(**r["features"])) for r in test_rows]
    import hashlib
    import time

    outcomes_file = output_root / "ml_outcomes.jsonl"
    metrics = {
        "n_train": len(train_rows),
        "n_test": len(test_rows),
        "feature_order": FEATURE_ORDER,
        "seed": SEED,
        "dataset_sha256": hashlib.sha256(outcomes_file.read_bytes()).hexdigest(),
        "trained_at": time.time(),
        "accuracy": round(accuracy_score(y_test, [p >= 0.5 for p in proba]), 3),
        "brier_model": round(brier_score_loss(y_test, proba), 4),
        "brier_heuristic_baseline": round(brier_score_loss(y_test, heur), 4),
    }
    metrics["ships"] = metrics["brier_model"] <= metrics["brier_heuristic_baseline"]

    model_out.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, model_out)
    metrics_path = model_out.with_suffix(".metrics.json")
    metrics_path.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    return metrics


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 2:
        print(__doc__)
        return 2
    metrics = train(Path(args[0]), Path(args[1]))
    verdict = "SHIPS (beats heuristic)" if metrics["ships"] else "DOES NOT SHIP (heuristic wins)"
    print(json.dumps(metrics, indent=2))
    print(verdict)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
