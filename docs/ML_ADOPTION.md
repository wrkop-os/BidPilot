# ML adoption plan (per the ml-adoption-playbook phases)

Candidate ML components in BidPilot, run through the playbook's five phases.
Rule inherited from CLAUDE.md invariant 3: models advise, deterministic code
and humans decide — no ML output may gate, sign, price, or submit.

## Component inventory

| Component | Phase 1 (framing) | Phase 2 (data) | Verdict |
|---|---|---|---|
| P(win) bid advisor | pass — metric: B&P efficiency (win rate per proposal dollar) | no outcome data captured anywhere today | **BUILD capture + heuristic now, train later** |
| Custom LLM per stage | pass — metric: G2 recall >= 0.98 at lower cost | capture loop built (`bidpilot/mle/`) | already implemented; this IS phases 3-5 for LLM stages |
| Price-to-win regression | pass — metric: evaluated-price delta vs winner | needs FPDS award-history ingestion (not built) | DEFER until an FPDS collector exists |
| Embedding retrieval (vs BM25) | weak — BM25 works; writers only | needs eval corpus to prove lift | DEFER; swap lives behind `SearchIndex` interface |

## P(win) advisor — the component built now

**Phase 1 — framing.** Heuristic check: yes, a heuristic exists (discovery
prescreen + eligibility evidence) and is the mandated starting point; it ships
as the baseline. Metric: bid/no-bid decision quality — wasted B&P on losses
(~1.5% of contract value per pursuit) vs pipeline lost to over-caution.
Mistake budget: an over-optimistic score wastes one proposal's B&P; an
over-pessimistic score kills pipeline silently — therefore the score is
ADVISORY ONLY, rendered as an INFO line on the eligibility report. It never
changes `bid_recommendation`, never blocks a gate, and the bid/no-bid gate
stays human (PRD §14).

**Phase 2 — data.** Sources: notice metadata + eligibility evidence + KB
(features), and human-reported outcomes (labels) — which BidPilot never
captured until now. `bidpilot outcome <notice> won|lost|no_bid` snapshots the
feature vector with the label into `ml_outcomes.jsonl` at the output root.
Data contract: `PwinFeatures` (pydantic) with `FEATURE_ORDER` as the frozen
vector layout shared by scorer and trainer; missing features default
pessimistic. Leakage rule: chronological split only (outcomes are
time-ordered; a random split would leak market drift).

**Phase 3 — decoupling.** `bidpilot/ml/pwin.py` is a service boundary:
`advisory_for()` is the single integration point (one line in the
eligibility stage, wrapped so any failure yields no advisory rather than a
failed stage). Fallback: if `BIDPILOT_PWIN_MODEL` is unset, missing, or the
model errors, the deterministic heuristic answers — the pipeline cannot
notice the difference. Feature flag: the env var IS the flag; unset = pure
heuristic.

**Phase 4 — implementation.** Baseline = logistic regression
(`bidpilot/ml/train_pwin.py`, `pip install -e ".[ml]"`): fixed seed,
chronological 80/20 split, feature matrix shape `(n, len(FEATURE_ORDER))`,
refuses to train on < 30 labeled outcomes, and reports Brier score against
the heuristic baseline — a model that cannot beat the heuristic does not
ship. Transforms and schema are unit-tested without sklearn; the training
loop test skips when sklearn is absent.

**Phase 5 — MLOps handoff.** Retraining cadence: rerun `train_pwin` as
outcomes accumulate; promote by pointing `BIDPILOT_PWIN_MODEL` at the new
artifact only when the metrics file shows Brier <= heuristic baseline. The
LLM-side equivalent (capture -> collect -> export -> serve -> eval gate)
lives in `bidpilot/mle/` and `evals/harness.py`; both loops share the same
ship rule — beat the incumbent on the eval or stay on the bench.

## Heuristic (the shipped baseline)

Anchored in `kb.pro/MARKET_RESEARCH.md` sourcing: baseline P(win) ~= 0.28
(1 / 3.6 average offerors, FY2024), adjusted by deterministic evidence:
set-aside certification match, registered NAICS, relevant past performance,
minus soft risks; hard blockers floor the score. Clamped to [0.02, 0.65] —
the heuristic is never allowed to sound certain.
