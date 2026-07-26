# BidPilot

An agentic AI system for federal contract analysis & proposal generation, built to the [BidPilot PRD v1.0](https://docs.google.com/document/d/1-mtDfW1oh9v6KhHuKAbO1YLc1DAf2ft-TCM-K20b0qQ/edit).

**Input:** a SAM.gov opportunity URL.
**Output:** a reviewed, compliant, priced submission package — plus a machine-extracted Submission Instruction Sheet (who, where, how, by when, in what format) — with an auditable chain of reasoning.

Two non-negotiable design principles:

1. **Human-in-the-loop by design (§14).** BidPilot drafts, checks, and assembles; a human reviews, approves, signs, and submits. The system never signs, never submits, and is structurally incapable of emitting uncited company facts (the build fails closed — FR-10/FR-19).
2. **SAM.gov is where opportunities are posted, not where proposals are submitted.** Delivery is by email to the CO, or via PIEE / GSA eBuy / FedConnect / Unison, per each solicitation's instructions. The Submission Instruction Sheet is a first-class artifact.

## Architecture (PRD §6)

A **graph-based orchestrator** — not a free-form swarm. The workflow is a mostly-deterministic DAG with typed stage contracts, two hard human gates, an assumptions checkpoint, checkpointing after every stage, and swarm-style parallelism *inside* the production node. **LLMs decide and draft; code verifies and computes.**

```
SAM.gov URL
  ▼
intake ──────── A1: Opportunities API v2, attachment download, FULL amendment chain
  ▼             (bidding off a stale version is a classic fatal error)
docproc ─────── A2: parsing ladder (pypdf → pdfplumber tables → vision-LLM OCR of
  ▼             image-only pages), UCF A–M section detection, XLSX kept fillable,
  ▼             CUI/ITAR halt path; amendment diff + "what changed" report on re-runs
classify ────── A3: notice type / FAR regime / required artifact; fast model + rules,
  ▼             low confidence escalates to frontier; AI-disclosure clause detection
eligibility ─── A4: §2.3 checklist. Deterministic evidence: SBA size-standard lookup,
  ▼             clause scanner (52.219-x, DFARS 7012, WD, bonding…), Entity API self-check
  ◆ HUMAN GATE #1: bid / no-bid
shred ───────── A5: 3-pass compliance shredder — overlapping-window extraction (fast tier)
  ▼             → deterministic dedup/merge → adversarial "what did you miss?" (frontier)
strategy ────── A6: win strategy; every assumption surfaced, never silently guessed
  ◆ CHECKPOINT: assumptions
produce ─────── parallel swarm: A7 section writers (cite-or-[NEEDS INPUT], per L-outline)
  ▼             A8 past performance · A9 pricing (incl. filling the government's own
  ▼             XLSX template on a copy) · A10 forms (fillable-PDF admin prefill)
  ▼             A11 submission sheet + ICS
  ▼             — or, for a Sources Sought: a cited 2–5 page capability statement
assemble ────── volumes rendered to DOCX (+ PDF via LibreOffice when present, with
  ▼             EXACT page counts), named per convention; claims→source map
qa ──────────── A12: deterministic checks in code (coverage, fail-closed citations,
  ▼             renderer-owned page limits, WD re-assert) + bounded fix loop (max 2,
  ▼             re-renders after redrafts) + LLM audits + mock evaluation vs Section M
  ◆ HUMAN GATE #2: final package — export BLOCKED while hard failures open
export ──────── versioned ZIP + manifest + audit bundle + cost telemetry (NFR-2)
```

### The pricing engine (PRD §9)

The one module where "LLM writes a number" is unacceptable:

- **LLM:** WBS decomposition; hours per task with *recorded method* (analogy from KB actuals / parametric / bottom-up), rationale, and confidence; ODCs with `[QUOTE NEEDED]` flags; BOE narrative generated only from the recorded rationale.
- **Code (`pricing/rates.py`):** wrapped rates (direct → +fringe → +OH → +G&A → +fee), option-year escalation, totals, ±10% sensitivity — and **wage-determination floor enforcement**: WD tables are parsed from the attachments and every mapped labor category is checked per year; a violation is a hard error that blocks export.

### The Company Knowledge Base (PRD §6.5)

The system cannot write truthful proposals from nothing. The KB (per-tenant YAML directory in v1; pgvector/Postgres is the production target behind the same interface) holds the profile (UEI/CAGE, size data per NAICS, certifications, indirect rates, labor categories), past-performance records with historical actuals, personnel, and reusable content — each with an owner and a last-verified date. **The KB is the only permissible source for company facts:** writers cite entries by `kb_id` or emit `[NEEDS INPUT]`; uncited claims are hard QA failures and the build fails closed.

### Everything is auditable (FR-18, NFR-5)

`audit.jsonl` per run records every model call (model, prompt SHA-256, tokens, duration), every stage transition, every human gate decision, and the export. Runs checkpoint after every stage and resume exactly where they stopped (FR-21).

## Setup

```bash
pip install -e ".[dev]"            # add ".[tables]" for pdfplumber table extraction

export ANTHROPIC_API_KEY=sk-ant-...   # or `ant auth login`
export SAM_GOV_API_KEY=...            # free key via your SAM.gov account

bidpilot init-kb                   # creates ./kb from the example
$EDITOR kb/profile.yaml kb/past_performance.yaml kb/personnel.yaml
bidpilot interview                 # onboarding agent: what the KB is still missing
```

## Usage

```bash
# Environment / contract checks first (add --network to ping the SAM API, NFR-3)
bidpilot doctor

# Proactive discovery (Phase 5.1): recent notices matching your NAICS codes,
# pre-screened deterministically (set-aside vs certs, size standard, deadline)
bidpilot discover --days 7

# Phase-1 "Analyst MVP": bid/no-bid in minutes — eligibility report,
# compliance matrix, submission sheet. No drafting, no pricing.
bidpilot analyze "https://sam.gov/opp/<notice-id>/view"

# Full pipeline (resumes from the last checkpoint automatically)
bidpilot run "https://sam.gov/opp/<notice-id>/view" --actor "jrivera"

# Solicitation amended? Archive docs, invalidate, re-run → AMENDMENT_REPORT.md
# with per-document diffs and a "what to re-review" checklist (FR-4)
bidpilot amend <notice-id> && bidpilot run <notice-id>

# Estimator review loop (§9.7): edit hours in runs/<id>/pricing/pricing_model.json,
# then recompute every downstream number deterministically — no LLM calls
bidpilot reprice <notice-id>

# Per-stage / per-model spend for a run vs the NFR-2 $25 budget
bidpilot costs <notice-id>

# Non-interactive gates (CI/testing — still never signs or submits)
bidpilot run <notice-id> --yes
```

Each run lives in `runs/<notice_id>/`:

| Artifact | PRD ref |
|---|---|
| `ELIGIBILITY_REPORT.md` / `.json` — hard blockers, soft risks, missing info, bid rec | A4, FR-5 |
| `compliance_matrix.csv` / `.json` — verbatim requirements w/ citations & status | A5, FR-8 |
| `win_strategy.json` — themes, discriminators, assumptions | A6 |
| `volumes/*.md` + `claims_source_map.json` — drafts with claim→KB provenance | A7, G5 |
| `pricing/` — priced lines CSV, BOE, pricing model, gov template copy (fillable) | A9, §9 |
| `FORMS_CHECKLIST.md` — prefilled admin fields, signature flags (never signed) | A10 |
| `SUBMISSION_INSTRUCTIONS.md` + `deadlines.ics` (questions / T-48h / deadline) | A11, §10.6 |
| `rendered/*.docx` (+ `.pdf` with exact page counts when LibreOffice is present) | FR-15 |
| `pricing/FILLED_<template>.xlsx` — the government's own workbook, filled on a copy | FR-12 |
| `forms/PREFILLED_*.pdf` — fillable forms with admin fields prefilled (never certs) | A10 |
| `QA_REPORT.md` — findings + mock evaluation (strengths/weaknesses/deficiencies) | A12 |
| `dashboard.html` — self-contained reviewer dashboard: coverage, claims, QA, gates | A13 |
| `AMENDMENT_REPORT.md` + `amendment_diffs.json` — after `bidpilot amend` re-runs | FR-4 |
| `COST_TELEMETRY.md` — per-stage/per-model spend vs the NFR-2 budget | FR-22 |
| `REVIEW_CHECKLIST.md` — every human action required before submission | FR-19 |
| `audit.jsonl` + `package_*.zip` — audit bundle + versioned export | FR-18 |

## Model routing (PRD §6.2.4, NFR-2)

Fast tier (`claude-haiku-4-5`) for volume work: window extraction, classification. Frontier tier (`claude-opus-5`) for judgment: strategy, writing, red-team, pricing decomposition, the adversarial completeness pass. Override with `BIDPILOT_FRONTIER_MODEL` / `BIDPILOT_FAST_MODEL`. Cost telemetry per call is in the audit log (FR-22).

## Evals (PRD §13, Phase 0)

```bash
pytest                                              # 76 deterministic tests, no network
python -m evals.collect "https://sam.gov/opp/<id>/view" <slug>   # freeze a corpus item
python -m evals.harness runs/<id>/compliance_matrix.json evals/corpus/<slug>/gold_matrix.csv
```

The harness scores requirement **recall** against hand-built gold matrices (G2 target ≥ 98% — misses are catastrophic, extra rows are cheap). `evals/collect.py` snapshots a notice (API JSON + attachments + gold-matrix template + ROI-timing notes) into the frozen corpus. Phase 0's 25-item corpus and 5 gold artifact sets live in `evals/corpus/` (not committed; see the PRD's Phase 0 plan — *do not skip it*).

## What's deliberately NOT here (v1 non-goals)

Autonomous submission (NG1 — hard product principle), CUI/classified processing (NG2 — halt-and-notify path instead), authenticated-portal flows like eBuy (NG3/v2), grants (NG4), teaming marketplace (NG5), win guarantees (NG6).

## Roadmap deltas vs. the PRD (v1 → production)

- pgvector retrieval behind `KnowledgeBase`; Postgres checkpoints behind `CheckpointStore` (interfaces are in place; v1 uses YAML + JSON files).
- Multi-tenant isolation + per-tenant encryption (FR-20) — v1 is single-tenant by design (PRD open question #1 recommends a design partner first).
- FastAPI + React reviewer UI with tracked edits (v1 ships `dashboard.html` + the run directory, per the PRD's "pragmatic v1" reviewer note).
- Learning loop (Phase 5.2): debrief/win-loss ingestion into win-theme selection.
- Portal-specific checklists for PIEE/eBuy/FedConnect (Phase 5.3).
