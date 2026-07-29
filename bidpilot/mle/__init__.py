"""MLE workflows: every production run generates training signal.

The loop:
  1. CAPTURE  — BIDPILOT_CAPTURE_TRAINING_DATA=1 makes ModelRouter log every
     (system, prompt, output) triple to training_capture.jsonl per run.
  2. COLLECT  — `bidpilot mle collect` sweeps run dirs into a dataset,
     pairing captured machine drafts with human-corrected section text
     (reviewer edits are preference data, PRD §6.5 governance applies).
  3. EXPORT   — `bidpilot mle export` emits chat-format JSONL (train/val
     split, per-stage filtering) for fine-tuning a custom model.
  4. SERVE    — point BIDPILOT_CUSTOM_LLM_URL at the fine-tuned deployment
     (vLLM/TGI/Ollama, OpenAI-compatible) and pick tiers with
     BIDPILOT_CUSTOM_LLM_TIERS; the router operates the backend with it.
  5. GATE     — `python -m evals.harness` scores the custom model's shred
     recall against gold matrices; ship only if recall >= 0.98 (G2).
"""

from .datasets import DatasetStats, collect_runs  # noqa: F401
from .finetune import export_chat_jsonl  # noqa: F401
