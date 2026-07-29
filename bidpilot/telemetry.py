"""Cost telemetry (FR-22, NFR-2): per-run / per-stage / per-model spend,
computed from the audit log. The NFR-2 target is <= ~$25 model spend per
full-package run — this is how you know whether routing is holding it.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .audit import AuditLog

# $ per million tokens (input, output). Update when pricing changes.
MODEL_PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.00, 25.00),
    "claude-fable-5": (10.00, 50.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
}

NFR2_BUDGET_USD = 25.0


def _price_for(model: str) -> tuple[float, float]:
    for known, price in MODEL_PRICING.items():
        if model.startswith(known):
            return price
    return (5.00, 25.00)  # conservative default


@dataclass
class StageCost:
    calls: int = 0
    failures: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    duration_s: float = 0.0


@dataclass
class RunCost:
    by_stage: dict[str, StageCost] = field(default_factory=lambda: defaultdict(StageCost))
    by_model: dict[str, StageCost] = field(default_factory=lambda: defaultdict(StageCost))
    total: StageCost = field(default_factory=StageCost)

    def within_budget(self, budget: float = NFR2_BUDGET_USD) -> bool:
        return self.total.cost_usd <= budget


def compute_costs(audit_path: Path) -> RunCost:
    log = AuditLog(audit_path)
    run_cost = RunCost()
    for entry in log.entries():
        if entry.get("event") == "llm_call_failed":
            model = entry.get("model") or "unknown"
            stage = _stage_group(entry.get("stage") or "unknown")
            for bucket in (run_cost.by_stage[stage], run_cost.by_model[model], run_cost.total):
                bucket.failures += 1
                bucket.duration_s += float(entry.get("duration_s") or 0)
            continue
        if entry.get("event") != "llm_call":
            continue
        model = entry.get("model") or "unknown"
        tokens_in = int(entry.get("tokens_in") or 0)
        tokens_out = int(entry.get("tokens_out") or 0)
        in_price, out_price = _price_for(model)
        cost = tokens_in / 1_000_000 * in_price + tokens_out / 1_000_000 * out_price
        stage = _stage_group(entry.get("stage") or "unknown")
        for bucket in (run_cost.by_stage[stage], run_cost.by_model[model], run_cost.total):
            bucket.calls += 1
            bucket.tokens_in += tokens_in
            bucket.tokens_out += tokens_out
            bucket.cost_usd += cost
            bucket.duration_s += float(entry.get("duration_s") or 0)
    return run_cost


def _stage_group(stage: str) -> str:
    """Group sub-stages: 'shred.extract' -> 'shred', 'write.TECH-1' -> 'write'."""
    return stage.split(".", 1)[0]


def report_markdown(run_cost: RunCost) -> str:
    lines = [
        "# Cost Telemetry",
        "",
        f"**Total model spend: ${run_cost.total.cost_usd:.2f}** "
        f"(NFR-2 budget ${NFR2_BUDGET_USD:.0f} — "
        f"{'WITHIN' if run_cost.within_budget() else 'OVER'} budget)",
        f"Calls: {run_cost.total.calls} | failed: {run_cost.total.failures} | "
        f"tokens in: {run_cost.total.tokens_in:,} | "
        f"out: {run_cost.total.tokens_out:,} | model time: {run_cost.total.duration_s:.0f}s",
        "",
        "## By stage",
        "",
        "| Stage | Calls | Failed | Tokens in | Tokens out | Cost |",
        "|---|---|---|---|---|---|",
    ]
    for stage, cost in sorted(run_cost.by_stage.items(), key=lambda kv: -kv[1].cost_usd):
        lines.append(
            f"| {stage} | {cost.calls} | {cost.failures} | {cost.tokens_in:,} | "
            f"{cost.tokens_out:,} | ${cost.cost_usd:.2f} |"
        )
    lines += ["", "## By model", "", "| Model | Calls | Cost |", "|---|---|---|"]
    for model, cost in sorted(run_cost.by_model.items(), key=lambda kv: -kv[1].cost_usd):
        lines.append(f"| {model} | {cost.calls} | ${cost.cost_usd:.2f} |")
    return "\n".join(lines)
