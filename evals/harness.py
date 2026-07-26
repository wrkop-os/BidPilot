"""Eval harness (Phase 0): score extracted compliance matrices against
hand-built gold matrices. Recall is the metric that matters — misses are
catastrophic, extra rows are cheap (G2: >= 98% recall).

Gold format: CSV with at least a `verbatim_text` column (the Phase-0 manual
matrix spreadsheets). Corpus layout:

    evals/corpus/<solicitation-slug>/
        attachments/...            # frozen solicitation files
        gold_matrix.csv            # hand-built matrix (5 of the 25)
        gold_eligibility.json      # optional
Run:  python -m evals.harness runs/<notice_id>/compliance_matrix.json evals/corpus/<slug>/gold_matrix.csv
"""

from __future__ import annotations

import csv
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", " ".join(text.lower().split()))


def _token_overlap(a: str, b: str) -> float:
    ta, tb = set(_normalize(a).split()), set(_normalize(b).split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


MATCH_THRESHOLD = 0.55


@dataclass
class MatrixScore:
    recall: float
    precision: float
    gold_total: int
    extracted_total: int
    missed: list[str]

    def passes_g2(self) -> bool:
        return self.recall >= 0.98


def score_matrix(extracted_texts: list[str], gold_texts: list[str]) -> MatrixScore:
    """Greedy fuzzy matching of gold requirements against extracted rows."""
    missed: list[str] = []
    matched_extracted: set[int] = set()
    hits = 0
    for gold in gold_texts:
        best_idx, best = -1, 0.0
        for i, ext in enumerate(extracted_texts):
            overlap = _token_overlap(gold, ext)
            if overlap > best:
                best, best_idx = overlap, i
        if best >= MATCH_THRESHOLD:
            hits += 1
            matched_extracted.add(best_idx)
        else:
            missed.append(gold)
    recall = hits / len(gold_texts) if gold_texts else 1.0
    precision = len(matched_extracted) / len(extracted_texts) if extracted_texts else 1.0
    return MatrixScore(
        recall=recall,
        precision=precision,
        gold_total=len(gold_texts),
        extracted_total=len(extracted_texts),
        missed=missed,
    )


def load_gold_csv(path: Path) -> list[str]:
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        column = "verbatim_text" if "verbatim_text" in (reader.fieldnames or []) else (reader.fieldnames or [""])[0]
        return [row[column] for row in reader if row.get(column, "").strip()]


def load_extracted_json(path: Path) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [r["verbatim_text"] for r in data.get("requirements", [])]


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    extracted = load_extracted_json(Path(argv[0]))
    gold = load_gold_csv(Path(argv[1]))
    score = score_matrix(extracted, gold)
    print(f"gold requirements:      {score.gold_total}")
    print(f"extracted requirements: {score.extracted_total}")
    print(f"recall:    {score.recall:.1%}  (G2 target >= 98%) {'PASS' if score.passes_g2() else 'FAIL'}")
    print(f"precision: {score.precision:.1%}  (informational — extra rows are cheap)")
    if score.missed:
        print("\nMISSED gold requirements:")
        for miss in score.missed:
            print(f"  - {miss[:160]}")
    return 0 if score.passes_g2() else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
