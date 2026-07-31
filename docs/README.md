# Documentation index

One canonical home per fact set. If you are about to write a doc, check
whether it belongs in an existing one first.

## Product & strategy

| Doc | What it holds | Read it when |
|---|---|---|
| [`../README.md`](../README.md) | The PRD-level product description, setup, usage, state of the build | Start here |
| [`COMPETITIVE_LANDSCAPE.md`](COMPETITIVE_LANDSCAPE.md) | Scoped competitor set: positioning brief, 11 candidates tiered Direct/Adjacent/Aspirational | Deciding who we are actually up against |
| [`COMPETITIVE_BENCHMARK.md`](COMPETITIVE_BENCHMARK.md) | 8 rivals scored on 9 dimensions, automation × trust tension plot, white-space statement | Prioritizing what to build next |

## Engineering & architecture

| Doc | What it holds | Read it when |
|---|---|---|
| [`../CLAUDE.md`](../CLAUDE.md) | Architecture map, non-negotiable invariants, conventions | Before changing anything |
| [`PRICING_COMPLIANCE.md`](PRICING_COMPLIANCE.md) | The regulatory layer under the estimate: which FAR clauses change the arithmetic, dated thresholds, evidence quality | Touching `bidpilot/pricing/` or arguing with a compliance finding |
| [`DOMAIN_MODEL.md`](DOMAIN_MODEL.md) | The in-house trained requirement model: corpus, grouped split, measured results, and what a from-scratch drafting LLM would actually take | Touching `bidpilot/ml/` or asking why the backend still calls an LLM |
| [`ML_ADOPTION.md`](ML_ADOPTION.md) | ML component inventory, P(win) iteration compact, promotion contracts | Touching `bidpilot/ml/` or `bidpilot/mle/` |
| [`OPS_HARNESS.md`](OPS_HARNESS.md) | The autonomous ops harness and its consent boundaries | Adding scheduled/automated behavior |
| [`TEST_SUITE_PERF.md`](TEST_SUITE_PERF.md) | Test-suite timing and parallelization notes | The suite feels slow |

## Audits (point-in-time findings)

| Doc | Date | Status |
|---|---|---|
| [`PROMPT_REVIEW.md`](PROMPT_REVIEW.md) | 2026-07-30 | 21 findings; **rewrites deliberately unapplied** until Phase-0 evals can prove them |
| [`CLICK_PATH_AUDIT.md`](CLICK_PATH_AUDIT.md) | 2026-07-30 | 6 UI/state bugs — all fixed, each with a regression test |
| [`AUTOMATION_AUDIT.md`](AUTOMATION_AUDIT.md) | 2026-07-30 | Automation inventory; CI-never-green root cause found and fixed |

## Operational state (living, not archival)

| File | What it holds |
|---|---|
| [`../ops/QUEUE.md`](../ops/QUEUE.md) | The task queue shared between scheduled and interactive sessions |
| [`../ops/BOARD.md`](../ops/BOARD.md) | Agent board: cards, owners, merge gates, sprint outcomes |
| [`../ops/ARCHIVE.md`](../ops/ARCHIVE.md) | Completed queue items, moved out to keep the queue readable |

## Knowledge base (the product's own knowledge layer)

`kb.example/` is the minimal template the test suite uses; `kb.pro/` is the
market-researched production template, with every rate sourced in
[`../kb.pro/MARKET_RESEARCH.md`](../kb.pro/MARKET_RESEARCH.md).

Keep the KB honest with the two knowledge-ops commands:

```bash
bidpilot kb-health --kb kb.pro --strict   # staleness, duplicates, broken refs, thin records
bidpilot kb-gaps --out runs               # what runs needed and the KB could not supply
```

`kb-gaps` is the learning loop: every `[NEEDS INPUT]` and uncited claim across
past runs, clustered and ranked, so the next fact you add is the one that has
been costing you the most drafts.
