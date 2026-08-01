"""Train the requirement classifier.

    python -m bidpilot.ml.train_requirements --out models/requirements.joblib

The split is by FAMILY, never by row. Templates inside a family are
paraphrases, so a random split would put near-duplicates on both sides and
report a number the model has not earned. Held-out families are phrasings the
model has genuinely never seen.

A second, independent check runs against `evals/corpus_demo/gold_matrix.csv` —
rows authored separately from this corpus, by hand, for the eval harness. If
the model only works on text shaped like its own training set, that is where
it shows.

The trainer writes a metrics sidecar bound to the artifact's sha256 and sets
`ships` only if every gate in REQUIREMENT_GATES passed. Nothing else may set
that flag; the serving path refuses an artifact without it.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import time
from pathlib import Path
from typing import Optional

from .corpus import CorpusStats, LabeledSentence, build_corpus
from .requirements_model import (
    CATEGORIZE_GATES,
    DEFAULT_NONE_THRESHOLD,
    SCREEN_GATES,
    build_category_pipeline,
    build_screen_pipeline,
)


def split_by_family(rows: list[LabeledSentence], holdout: float = 0.25,
                    seed: int = 13) -> tuple[list[LabeledSentence], list[LabeledSentence]]:
    """Group split. Families are assigned whole to one side or the other, and
    the assignment is stratified by category so both sides see every class."""
    import random

    rng = random.Random(seed)
    by_category: dict[str, list[str]] = {}
    family_category = {}
    for row in rows:
        family_category.setdefault(row.family, row.category)
    for family, category in family_category.items():
        by_category.setdefault(category, []).append(family)

    test_families: set[str] = set()
    for category, fams in by_category.items():
        fams = sorted(fams)
        rng.shuffle(fams)
        # At least one family per category on the test side, but never all of
        # them — a class absent from training cannot be learned.
        n_test = max(1, int(round(len(fams) * holdout)))
        n_test = min(n_test, max(1, len(fams) - 1))
        test_families.update(fams[:n_test])

    train = [r for r in rows if r.family not in test_families]
    test = [r for r in rows if r.family in test_families]
    return train, test


def _predict_with_threshold(pipeline, texts: list[str], threshold: float) -> list[str]:
    """Mirror the serving rule exactly: only a confident "none" drops a
    sentence. Evaluating with plain argmax would measure a decision rule the
    product does not use."""
    classes = list(pipeline.classes_)
    index = {c: i for i, c in enumerate(classes)}
    out: list[str] = []
    for row in pipeline.predict_proba(texts):
        p_none = float(row[index["none"]]) if "none" in index else 0.0
        if p_none >= threshold:
            out.append("none")
            continue
        out.append(str(max((c for c in classes if c != "none"),
                           key=lambda c: row[index[c]], default="content")))
    return out


# The operating point is chosen against near-perfect recall on the calibration
# families, not against the gate itself.
#
# The errors are wildly asymmetric. A sentence wrongly kept costs the LLM one
# more line to read. A sentence wrongly dropped is a requirement that never
# reaches the compliance matrix, and an unaddressed requirement is how
# proposals get eliminated. Recall on unseen sub-topics is always worse than on
# calibration families, so tuning the threshold to *exactly* clear the gate
# guarantees missing it in the only measurement that counts.
CALIBRATION_RECALL_TARGET = 0.99

# Family splits the gate is evaluated over, in addition to the primary one.
# Gating on the minimum makes the promotion decision robust to which
# sub-topics happen to land on the test side.
EVAL_SPLIT_SEEDS = (23, 47, 61, 83, 97)


def choose_none_threshold(pipeline, rows: list[LabeledSentence],
                          target_recall: float,
                          calibration_target: float = CALIBRATION_RECALL_TARGET) -> float:
    """The most aggressive screen that still clears recall on TRAIN-side data.

    Recall rises monotonically with the threshold (a higher bar sends more
    sentences on to the LLM), so the smallest passing threshold screens the
    most text. Fitting this on the test split would make the reported recall a
    fitted quantity rather than a measured one.
    """
    if not rows:
        return DEFAULT_NONE_THRESHOLD
    texts = [r.text for r in rows]
    truth = [r.category for r in rows]
    real = [i for i, t in enumerate(truth) if t != "none"]
    if not real:
        return DEFAULT_NONE_THRESHOLD
    goal = max(target_recall, calibration_target)
    for threshold in [round(x / 100, 2) for x in range(5, 100, 5)]:
        predicted = _predict_with_threshold(pipeline, texts, threshold)
        kept = sum(1 for i in real if predicted[i] != "none")
        if kept / len(real) >= goal:
            return threshold
    return 0.99          # screen almost nothing rather than drop requirements


def evaluate(pipeline, rows: list[LabeledSentence],
             threshold: float = DEFAULT_NONE_THRESHOLD,
             category_pipeline=None) -> dict:
    if not rows:
        return {}
    texts = [r.text for r in rows]
    truth = [r.category for r in rows]
    predicted = _predict_with_threshold(pipeline, texts, threshold)
    if category_pipeline is not None:
        # Mirror serving: the screen decides survival, the category model
        # labels the survivors. Scoring the screen's own category guess would
        # measure a model the product no longer consults.
        survivors = [i for i, p in enumerate(predicted) if p != "none"]
        if survivors:
            labels = category_pipeline.predict([texts[i] for i in survivors])
            for i, label in zip(survivors, labels):
                predicted[i] = str(label)

    # The gate metric: of the sentences that ARE requirements, how many did we
    # keep? A miss here is an unaddressed requirement.
    real = [i for i, t in enumerate(truth) if t != "none"]
    kept = sum(1 for i in real if predicted[i] != "none")
    recall = kept / len(real) if real else 0.0

    # Of what we called a requirement, how much really was one? Precision only
    # costs reviewer time, so it is reported but not gated.
    called = [i for i, p in enumerate(predicted) if p != "none"]
    precision = (sum(1 for i in called if truth[i] != "none") / len(called)
                 if called else 0.0)

    # Category accuracy is measured on true requirements only: getting "none"
    # right is the easy majority class and would flatter the number.
    correct_category = sum(1 for i in real if predicted[i] == truth[i])
    category_accuracy = correct_category / len(real) if real else 0.0

    # The number that decides whether screening is worth doing at all: of the
    # sentences that bind nobody, how many did we keep away from the LLM?
    noise = [i for i, t in enumerate(truth) if t == "none"]
    screened = sum(1 for i in noise if predicted[i] == "none")
    screened_out_rate = screened / len(noise) if noise else 0.0

    confusion: dict[str, dict[str, int]] = {}
    for actual, pred in zip(truth, predicted):
        confusion.setdefault(actual, {})
        confusion[actual][pred] = confusion[actual].get(pred, 0) + 1

    return {
        "n": len(rows),
        "requirement_recall": round(recall, 4),
        "requirement_precision": round(precision, 4),
        "category_accuracy": round(category_accuracy, 4),
        "screened_out_rate": round(screened_out_rate, 4),
        "confusion": confusion,
    }


def recall_volume_curve(pipeline, rows: list[LabeledSentence]) -> list[dict]:
    """The tradeoff, measured rather than asserted: at each operating point,
    what recall do we hold and how much text does the LLM no longer read?"""
    curve = []
    for threshold in [0.1, 0.2, 0.3, 0.5, 0.7, 0.8, 0.9, 0.95, 0.99]:
        scored = evaluate(pipeline, rows, threshold)
        if scored:
            curve.append({
                "threshold": threshold,
                "requirement_recall": scored["requirement_recall"],
                "screened_out_rate": scored["screened_out_rate"],
            })
    return curve


def load_gold(path: Path) -> list[LabeledSentence]:
    """The independent check: hand-authored rows from the eval corpus."""
    rows: list[LabeledSentence] = []
    if not path.exists():
        return rows
    with path.open(encoding="utf-8") as handle:
        for record in csv.DictReader(handle):
            text = (record.get("verbatim_text") or "").strip()
            category = (record.get("category") or "").strip()
            if text and category:
                rows.append(LabeledSentence(text=text, category=category,
                                            family="gold", source="gold"))
    return rows


def train(out_path: Path, runs_root: Optional[Path] = None,
          per_phrasing: int = 6, seed: int = 17) -> dict:
    rows = build_corpus(runs_root, per_phrasing=per_phrasing, seed=seed)
    stats = CorpusStats.of(rows)
    train_rows, test_rows = split_by_family(rows)

    # Choose the operating point by cross-validation over TRAIN families.
    #
    # A single calibration split is too noisy here: with only a few dozen
    # families, one easy split reports a threshold that collapses on genuinely
    # novel sub-topics. Each fold holds out a different quarter of the
    # families, and the MOST CONSERVATIVE threshold across folds wins, because
    # the cost of screening too aggressively is a lost requirement.
    started = time.time()
    target = SCREEN_GATES["requirement_recall"][1]
    fold_thresholds: list[float] = []
    for fold_seed in (29, 31, 37, 41, 43):
        fit_rows, calib_rows = split_by_family(train_rows, holdout=0.25, seed=fold_seed)
        fold_pipeline = build_screen_pipeline()
        fold_pipeline.fit([r.text for r in fit_rows], [r.category for r in fit_rows])
        fold_thresholds.append(choose_none_threshold(fold_pipeline, calib_rows, target))
    threshold = max(fold_thresholds)

    # Refit the screen on all training data now that the operating point is
    # fixed, and fit the category model on the REQUIREMENT rows only.
    pipeline = build_screen_pipeline()
    pipeline.fit([r.text for r in train_rows], [r.category for r in train_rows])

    train_reqs = [r for r in train_rows if r.category != "none"]
    category_pipeline = build_category_pipeline()
    category_pipeline.fit([r.text for r in train_reqs],
                          [r.category for r in train_reqs])
    train_seconds = round(time.time() - started, 2)

    gold_path = Path(__file__).resolve().parents[2] / "evals" / "corpus_demo" / "gold_matrix.csv"
    primary = evaluate(pipeline, test_rows, threshold, category_pipeline)
    gold = evaluate(pipeline, load_gold(gold_path), threshold, category_pipeline)

    # Gate on the WORST split, not a single lucky one.
    #
    # With a few dozen families, which ones land on the test side swings the
    # result hard: measured across six splits this corpus ranged 0.884-1.000
    # on recall and 0.698-0.871 on category accuracy. Promoting off one split
    # would ship a model whose headline number was an artifact of the seed.
    per_split = [primary]
    for split_seed in EVAL_SPLIT_SEEDS:
        alt_train, alt_test = split_by_family(rows, seed=split_seed)
        alt_screen = build_screen_pipeline()
        alt_screen.fit([r.text for r in alt_train], [r.category for r in alt_train])
        alt_reqs = [r for r in alt_train if r.category != "none"]
        alt_category = build_category_pipeline()
        alt_category.fit([r.text for r in alt_reqs], [r.category for r in alt_reqs])
        per_split.append(evaluate(alt_screen, alt_test, threshold, alt_category))

    held_out = dict(primary)
    for metric in ("requirement_recall", "category_accuracy",
                   "requirement_precision", "screened_out_rate"):
        values = [m[metric] for m in per_split if metric in m]
        if values:
            held_out[metric] = round(min(values), 4)
            held_out[f"{metric}_mean"] = round(sum(values) / len(values), 4)
    held_out["splits_evaluated"] = len(per_split)
    held_out["aggregation"] = "worst across splits"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    import joblib

    joblib.dump({"screen": pipeline, "category": category_pipeline}, out_path)
    digest = hashlib.sha256(out_path.read_bytes()).hexdigest()

    # Gates are checked against the HELD-OUT families, not the training fit
    # and not the gold set (which is small enough that one row swings it).
    def _failures(gates: dict) -> dict:
        return {
            name: held_out.get(name)
            for name, (direction, bar) in gates.items()
            if held_out.get(name) is None
            or (direction == "min" and held_out[name] < bar)
            or (direction == "max" and held_out[name] > bar)
        }

    screen_failures = _failures(SCREEN_GATES)
    categorize_failures = _failures(CATEGORIZE_GATES)
    failures = {**screen_failures, **categorize_failures}

    record = {
        "trained_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "model_sha256": digest,
        "none_threshold": threshold,
        "threshold_folds": fold_thresholds,
        "threshold_rule": ("max over 5 family-held-out folds; conservative "
                           "because a dropped requirement costs the bid"),
        # Two independent capabilities, each earned on its own gate.
        "ships_screen": not screen_failures,
        "ships_categorize": not categorize_failures,
        "ships": not failures,
        "failed_gates": failures,
        "gates": {
            "screen": {k: {"direction": d, "threshold": t}
                       for k, (d, t) in SCREEN_GATES.items()},
            "categorize": {k: {"direction": d, "threshold": t}
                           for k, (d, t) in CATEGORIZE_GATES.items()},
        },
        "corpus": {
            "total": stats.total,
            "families": stats.families,
            "by_category": stats.by_category,
            "by_source": stats.by_source,
            "train_rows": len(train_rows),
            "test_rows": len(test_rows),
            "split": "grouped by template family (no row-level leakage)",
        },
        "held_out": held_out,
        "held_out_curve": recall_volume_curve(pipeline, test_rows),
        "gold_independent": gold,
        "train_seconds": train_seconds,
        **{k: v for k, v in held_out.items()
           if k in SCREEN_GATES or k in CATEGORIZE_GATES},
    }
    out_path.with_suffix(".metrics.json").write_text(
        json.dumps(record, indent=2), encoding="utf-8")
    return record


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="models/requirements.joblib")
    parser.add_argument("--runs", default=None,
                        help="Run root to mine captured requirements from")
    parser.add_argument("--per-phrasing", type=int, default=6)
    parser.add_argument("--seed", type=int, default=17)
    args = parser.parse_args(argv)

    record = train(Path(args.out),
                   Path(args.runs) if args.runs else None,
                   per_phrasing=args.per_phrasing, seed=args.seed)

    corpus = record["corpus"]
    held = record["held_out"]
    print(f"corpus: {corpus['total']} rows / {corpus['families']} families "
          f"({corpus['by_source']})")
    print(f"split:  {corpus['train_rows']} train / {corpus['test_rows']} test "
          f"— {corpus['split']}")
    print(f"held-out families (WORST of {held['splits_evaluated']} splits): "
          f"recall={held['requirement_recall']:.3f} "
          f"precision={held['requirement_precision']:.3f} "
          f"category_acc={held['category_accuracy']:.3f} "
          f"screened_out={held['screened_out_rate']:.3f}")
    print(f"                   mean: recall={held['requirement_recall_mean']:.3f} "
          f"category_acc={held['category_accuracy_mean']:.3f}")
    gold = record["gold_independent"]
    if gold:
        print(f"independent gold:  recall={gold['requirement_recall']:.3f} "
              f"category_acc={gold['category_accuracy']:.3f}  (n={gold['n']})")
    print(f"none-threshold: {record['none_threshold']} "
          f"(max over folds {record['threshold_folds']}, train-side only)")
    print(f"trained in {record['train_seconds']}s")

    if record["ships_screen"]:
        job = ("SCREEN + CATEGORIZE" if record["ships_categorize"] else "SCREEN only")
        print(f"PROMOTED for {job}. Serve with BIDPILOT_REQ_MODEL={args.out}")
        if not record["ships_categorize"]:
            print("  Categorization stays with the LLM — the model did not earn "
                  f"that job ({record['failed_gates']}).")
        return 0
    print(f"NOT PROMOTED — screening gate failed: {record['failed_gates']}")
    print("The artifact was written but will refuse to load.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
