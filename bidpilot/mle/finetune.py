"""Export collected examples as chat-format JSONL for fine-tuning.

Format: one {"messages": [{role, content}...]} per line — the interchange
accepted by OpenAI-compatible trainers and convertible to most others.
Preference pairs export the HUMAN-CORRECTED output as the assistant turn
(that is the behavior we want the custom model to learn); the raw machine
output rides along under "rejected" for DPO-style trainers.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Optional

from .datasets import Example


def export_chat_jsonl(
    examples: list[Example],
    out_dir: Path,
    *,
    stage_prefix: Optional[str] = None,
    val_fraction: float = 0.1,
    seed: int = 13,
) -> dict:
    """Write train.jsonl / val.jsonl; returns counts. Deterministic split."""
    rows = []
    for ex in examples:
        if stage_prefix and not (ex.stage or "").startswith(stage_prefix):
            continue
        target = ex.corrected_output if ex.kind == "preference" else ex.output
        if not target.strip() or not ex.prompt.strip():
            continue
        row = {
            "messages": [
                {"role": "system", "content": ex.system},
                {"role": "user", "content": ex.prompt},
                {"role": "assistant", "content": target},
            ],
            "meta": {"stage": ex.stage, "tier": ex.tier, "kind": ex.kind},
        }
        if ex.kind == "preference":
            row["rejected"] = ex.output
        rows.append(row)

    rng = random.Random(seed)
    rng.shuffle(rows)
    n_val = int(len(rows) * val_fraction)
    val, train = rows[:n_val], rows[n_val:]

    out_dir.mkdir(parents=True, exist_ok=True)
    _write(out_dir / "train.jsonl", train)
    _write(out_dir / "val.jsonl", val)
    counts = {
        "train": len(train),
        "val": len(val),
        "preference_pairs": sum(1 for r in rows if r["meta"]["kind"] == "preference"),
    }
    (out_dir / "DATASET_CARD.md").write_text(_card(counts, stage_prefix), encoding="utf-8")
    return counts


def _write(path: Path, rows: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _card(counts: dict, stage_prefix: Optional[str]) -> str:
    return f"""# BidPilot fine-tuning dataset

- train: {counts['train']} examples | val: {counts['val']} examples
- preference pairs (human-corrected): {counts['preference_pairs']}
- stage filter: {stage_prefix or '(all stages)'}

Provenance: captured from BidPilot runs (training_capture.jsonl) with
reviewer edits as preferred outputs. Solicitation text may be embedded in
prompts — treat the dataset with the same sensitivity as the source
proposals, and never train on runs containing CUI without authorization.

Serve the resulting model behind an OpenAI-compatible endpoint and set:
  BIDPILOT_CUSTOM_LLM_URL=https://host/v1
  BIDPILOT_CUSTOM_LLM_MODEL=<served model name>
  BIDPILOT_CUSTOM_LLM_TIERS=fast   # graduate to 'all' after eval gates pass

Ship gate: `python -m evals.harness <extracted> <gold>` recall >= 0.98 (G2).
"""
