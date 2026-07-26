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
docproc ─────── A2: parsing ladder (pypdf → pdfplumber tables → OCR flag), UCF A–M
  ▼             section detection, XLSX kept fillable, CUI/ITAR halt path
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
  ▼             A8 past performance · A9 pricing · A10 forms · A11 submission sheet + ICS
assemble ────── volumes merged per L, claims→source map, government XLSX template copied
  ▼             fillable & untouched
qa ──────────── A12: deterministic checks in code (coverage, fail-closed citations, page
  ▼             limits, WD re-assert) + bounded fix loop (max 2) + LLM audits (consistency,
  ▼             citation sampling) + mock evaluation vs Section M
  ◆ HUMAN GATE #2: final package — export BLOCKED while hard failures open
export ──────── versioned ZIP + manifest + audit bundle
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
# Phase-1 "Analyst MVP": bid/no-bid in minutes — eligibility report,
# compliance matrix, submission sheet. No drafting, no pricing.
bidpilot analyze "https://sam.gov/opp/<notice-id>/view"

# Full pipeline (resumes from the last checkpoint automatically)
bidpilot run "https://sam.gov/opp/<notice-id>/view" --actor "jrivera"

# Solicitation amended? Invalidate downstream stages, then re-run (FR-4)
bidpilot amend <notice-id> && bidpilot run <notice-id>

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
| `QA_REPORT.md` — findings + mock evaluation (strengths/weaknesses/deficiencies) | A12 |
| `REVIEW_CHECKLIST.md` — every human action required before submission | FR-19 |
| `audit.jsonl` + `package_*.zip` — audit bundle + versioned export | FR-18 |

## Model routing (PRD §6.2.4, NFR-2)

Fast tier (`claude-haiku-4-5`) for volume work: window extraction, classification. Frontier tier (`claude-opus-5`) for judgment: strategy, writing, red-team, pricing decomposition, the adversarial completeness pass. Override with `BIDPILOT_FRONTIER_MODEL` / `BIDPILOT_FAST_MODEL`. Cost telemetry per call is in the audit log (FR-22).

## Evals (PRD §13, Phase 0)

```bash
pytest                                              # 52 deterministic tests, no network
python -m evals.harness runs/<id>/compliance_matrix.json evals/corpus/<slug>/gold_matrix.csv
```

The harness scores requirement **recall** against hand-built gold matrices (G2 target ≥ 98% — misses are catastrophic, extra rows are cheap). Phase 0's frozen 25-solicitation corpus and 5 gold artifact sets live in `evals/corpus/` (not committed; see the PRD's Phase 0 plan — *do not skip it*).

## What's deliberately NOT here (v1 non-goals)

Autonomous submission (NG1 — hard product principle), CUI/classified processing (NG2 — halt-and-notify path instead), authenticated-portal flows like eBuy (NG3/v2), grants (NG4), teaming marketplace (NG5), win guarantees (NG6).

## Roadmap deltas vs. the PRD (v1 → production)

- DOCX/PDF rendering via docxtpl + LibreOffice headless with exact page-count verification (v1 uses a word-count heuristic and blocks on gross violations).
- pgvector retrieval behind `KnowledgeBase`; Postgres checkpoints behind `CheckpointStore`.
- Vision-LLM OCR for image-only PDFs (currently flagged for manual review).
- PDF form-field filling (pypdf) for SF-1449/SF-33 overlays.
- FastAPI + React reviewer UI (v1 reviews the run directory + exported files).
