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

## MLE workflow contracts (per the mle-workflow skill)

**Iteration Compact — P(win) iteration 1**

```text
Goal:               advisory win-probability on the eligibility report
Who cares:          capture lead (B&P budget), principal (pipeline)
Decision owner:     human at the bid/no-bid gate — always
Action changed:     none directly; informs the gate conversation
Success metric:     Brier score vs heuristic baseline on held-out outcomes
Guardrail metrics:  advisory renders in <1ms; zero pipeline failures from ML
Mistake budget:     over-optimism wastes ~1.5% of contract value in B&P;
                    over-pessimism silently kills pipeline (worse — advisory only)
Unacceptable:       score influencing bid_recommendation or any gate
Assumptions:        outcomes reported honestly via `bidpilot outcome`
Labels/snapshot:    ml_outcomes.jsonl; features frozen at record time;
                    dataset_sha256 stamped into training metrics
Baseline:           evidence-adjusted market heuristic (0.28 anchor)
Eval slices:        none until n >= 100 (note: set-aside type first slice)
Rollback/fallback:  unset BIDPILOT_PWIN_MODEL, or automatic — unpromoted or
                    failing artifacts fall back to the heuristic at load time
```

**Fail-closed promotion (both loops).** `bidpilot/mle/promotion.py`:
gates are declared before training and applied after. The P(win) scorer
refuses any artifact whose metrics sidecar is missing or says `ships:false`
(serves the heuristic instead). The custom-LLM loop promotes via
`bidpilot mle gate <extracted> <gold>` — G2 recall >= 0.98 or NOT PROMOTED,
with the promotion record written for the operator who would flip
`BIDPILOT_CUSTOM_LLM_TIERS`.

**Review-checklist status (honest gaps).** No monitoring/drift dashboards
(advisory-only stakes, deferred until a trained model actually serves); no
eval slices until outcome volume supports them; train/serve feature
equivalence holds by construction (one `build_features` function serves
both) — keep it that way.

## Heuristic (the shipped baseline)

Anchored in `kb.pro/MARKET_RESEARCH.md` sourcing: baseline P(win) ~= 0.28
(1 / 3.6 average offerors, FY2024), adjusted by deterministic evidence:
set-aside certification match, registered NAICS, relevant past performance,
minus soft risks; hard blockers floor the score. Clamped to [0.02, 0.65] —
the heuristic is never allowed to sound certain.
