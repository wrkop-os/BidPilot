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

### The retrieval layer (A2, NFR-2)

Section writers query a dependency-free BM25-style index (`docproc/index.py`) over corpus chunks carrying doc/page metadata, pulling only the excerpts relevant to their section (~60K chars) instead of the full 200–400K-char corpus per parallel frontier call. Recall-critical agents — shredder, classifier, eligibility, submission — still read everything: recall beats cost there. The index sits behind the interface pgvector will occupy in production.

### The pricing engine (PRD §9)

The one module where "LLM writes a number" is unacceptable:

- **LLM:** WBS decomposition; hours per task with *recorded method* (analogy from KB actuals / parametric / bottom-up), rationale, and confidence; ODCs with `[QUOTE NEEDED]` flags; BOE narrative generated only from the recorded rationale.
- **Code (`pricing/rates.py`):** wrapped rates (direct → +fringe → +OH → +G&A → +fee), option-year escalation, totals, ±10% sensitivity — and **wage-determination floor enforcement**: WD tables are parsed from the attachments and every mapped labor category is checked per year; a violation is a hard error that blocks export.

### The Company Knowledge Base (PRD §6.5)

The system cannot write truthful proposals from nothing. The KB (per-tenant YAML directory in v1; pgvector/Postgres is the production target behind the same interface) holds the profile (UEI/CAGE, size data per NAICS, certifications, indirect rates, labor categories), past-performance records with historical actuals, personnel, and reusable content — each with an owner and a last-verified date. **The KB is the only permissible source for company facts:** writers cite entries by `kb_id` or emit `[NEEDS INPUT]`; uncited claims are hard QA failures and the build fails closed.

Two reference KBs ship with the repo: `kb.example/` (minimal, used by the test suite) and `kb.pro/` (production-grade template for a 52-person SDVOSB, with 2025-2026 market-researched labor rates, DCAA-style indirect structure inside competitive wrap bands, SCA compliance doctrine, estimating benchmarks, and BOE methodology — every number sourced in `kb.pro/MARKET_RESEARCH.md`). Copy either as a starting point: `cp -r kb.pro kb`.

### The Trust Manifest: proof, not promises

Every exported package carries `TRUST_MANIFEST.md` + `trust_manifest.json` — an
auditable record of what the system *enforced*, assembled from recorded facts
(never model-written):

- **Never signed, never submitted.** No code path transmits a proposal or
  answers a certification. Signature blocks and reps & certs come back blank.
- **No uncited company claims.** Every claim names the knowledge-base entry it
  came from or carries `[NEEDS INPUT]`. An uncited claim is a HARD failure that
  *blocks export* — so a package that exists has zero of them.
- **Arithmetic is code, not model output.** Wrapped rates, escalation,
  wage-determination floors, totals, coverage, and page counts each name the
  deterministic function that produced them; re-run `bidpilot reprice` and the
  numbers reproduce exactly.
- **Every model call is fingerprinted.** Model, prompt SHA-256, tokens, and
  duration land in `audit.jsonl`; the manifest reports the count and confirms
  each call carries a hash.

Competitors ground drafts in your content and give you citations you *may*
check. This is the difference between being able to verify and being unable to
ship without having verified.

### Everything is auditable (FR-18, NFR-5)

`audit.jsonl` per run records every model call (model, prompt SHA-256, tokens, duration), every stage transition, every human gate decision, and the export. Runs checkpoint after every stage and resume exactly where they stopped (FR-21).

## Setup

```bash
pip install -e ".[dev]"            # add ".[tables]" for pdfplumber table extraction

# Keys go in .env (gitignored) so they never land in a tracked file.
# An exported environment variable always wins over the file.
cat > .env <<'EOF'
ANTHROPIC_API_KEY=sk-ant-...          # or run `ant auth login` instead
SAM_GOV_API_KEY=...                   # api.data.gov key for the Opportunities API
EOF
chmod 600 .env

bidpilot doctor --network          # verifies the keys and the SAM.gov API contract

bidpilot init-kb                   # creates ./kb from the example
$EDITOR kb/profile.yaml kb/past_performance.yaml kb/personnel.yaml
bidpilot interview                 # onboarding agent: what the KB is still missing
```

### Running without the SAM.gov API

The API is the happy path, not a hard dependency. Point BidPilot at a folder of
solicitation documents instead:

```bash
bidpilot run --local ./RFQ_Service_Desk --kb kb.pro
```

Use it when the API is not available to you: an egress policy that blocks
`api.sam.gov` (common on contractor networks, and a security posture rather
than something to route around), a spent daily rate limit, login-gated
attachments the API lists but cannot fetch, or a package that was never on
SAM.gov at all — a teaming partner's RFP, an agency-emailed draft, a state or
commercial solicitation.

Metadata resolves in strict precedence: an optional `notice.yaml` in the folder,
then deterministic extraction from the documents (solicitation number, NAICS,
PSC, set-aside, deadline — regex, never an LLM), then nothing. Unresolved
fields are reported as human actions rather than guessed, ordered by
consequence:

```yaml
# RFQ_Service_Desk/notice.yaml — every field optional
title: Enterprise Service Desk Support Services
agency: General Services Administration, FAS
naics_code: "541519"
response_deadline: "March 14, 2027 at 2:00 PM ET"
```

An unrecognized field is an error, not a silent no-op — dropping `naics:`
because the field is `naics_code:` would screen the bid against the wrong size
standard.

The run key is derived from document content, so re-running the same folder
resumes the same run and adding an amendment document starts a new one. Two
caveats the CLI prints every time: local intake has **no amendment chain** to
check, so confirm you have the latest version yourself; and it never invents
metadata.

Every follow-up command accepts the folder in place of a URL, so you never have
to know the generated run key:

```bash
bidpilot status ./RFQ_Service_Desk
bidpilot costs ./RFQ_Service_Desk
bidpilot reprice ./RFQ_Service_Desk --kb kb.pro
```

`watch-amendments` reports local runs as **not verified** rather than up to
date, and exits non-zero — a cron that treated "could not check" as "all clear"
would never fire. `discover` behaves the same way: if every source failed, it
says so and exits 1 instead of reporting "no opportunities found", which would
read as *nothing to bid on this week*.

### The in-house domain model

Part of the backend is a model trained in this repo rather than called over an
API. It reads solicitation text and decides what binds you — the shredder's
job, and the pipeline's highest-volume model stage:

```bash
bidpilot model train            # ~20s on CPU, deterministic
export BIDPILOT_REQ_MODEL=models/requirements.joblib
bidpilot model status           # what it earned, and how it was measured
```

Worst-of-fourteen-splits recall is 0.966 (mean 0.994) on sub-topics it never saw in training. It
runs as a **recall net**: after the shredder, it re-reads the corpus and reports
requirements the matrix missed, as candidates in `HUMAN_ACTIONS.md`. Nothing is
auto-added and nothing is removed.

`BIDPILOT_REQ_PREFILTER=1` flips it into a cost lever that cuts roughly 40% of
the text before the LLM reads it — at the price of ~34 requirements per 1,000
dropped unrecoverably. Off by default. Read
[`docs/DOMAIN_MODEL.md`](docs/DOMAIN_MODEL.md) before turning it on, including
why a proposal-*drafting* model cannot be trained here.

### Running without an Anthropic key

`BIDPILOT_CUSTOM_LLM_URL` routes every model call to any OpenAI-compatible
chat endpoint - vLLM, TGI, Ollama, a fine-tuned deployment, an Azure or
Bedrock shim, or a gateway in front of your own key:

```bash
export BIDPILOT_CUSTOM_LLM_URL=http://localhost:11434/v1   # e.g. Ollama
export BIDPILOT_CUSTOM_LLM_MODEL=your-model
export BIDPILOT_CUSTOM_LLM_TIERS=all      # or "fast" to move only volume work
bidpilot run --local ./RFQ_Service_Desk --kb kb.pro
```

No Anthropic key is needed on this path - the client is built lazily and never
reached. `BIDPILOT_CUSTOM_LLM_TIERS=fast` runs a distilled model on extraction
and classification while judgment calls stay on the frontier tier.

This route is covered end to end by `tests/test_custom_llm_live.py`, which runs
the whole pipeline over a real socket against a real OpenAI-compatible server,
including the failures self-hosted inference actually produces: transient 503s,
rate limits, prose-wrapped JSON, and schema violations. The citation gate holds
here too - an uncited or fabricated KB reference blocks export exactly as it
does on the Anthropic path.

`doctor --network` distinguishes the three ways a SAM.gov call fails — a
rejected key, a spent rate limit, and a blocked network path — because they
look identical in a stack trace and need completely different fixes. Note that
the Opportunities API wants an **api.data.gov** key; a SAM.gov system-account
key is a different credential and will come back 403.

## Usage

```bash
# Web UI (pip install -e ".[server]"): paste a listing URL in the browser —
# the listing is analyzed, the needed response package is decided from its
# classification, and every human gate becomes an approval button.
bidpilot serve --port 8400

# Environment / contract checks first (add --network to ping the SAM API, NFR-3)
bidpilot doctor

# Proactive discovery (Phase 5.1): recent notices matching your NAICS codes,
# pre-screened deterministically (set-aside vs certs, size standard, deadline)
bidpilot discover --days 7            # add --grants to sweep Grants.gov too (no key)

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

# Reviewer edit loop (A13): edit runs/<id>/volumes/sections/*.md, then import
# the edits and re-run assemble+QA+export. The pipeline never clobbers an
# unsynced edit; edited sections are flagged in QA and the review checklist.
bidpilot sync-drafts <notice-id> && bidpilot run <notice-id>

# Targeted re-run of one stage (and everything after it)
bidpilot redo qa <notice-id> && bidpilot run <notice-id>

# Where is this run? Stages, gates, blockers, deliverables — no keys needed
bidpilot status <notice-id>

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

## MLE loop: train a custom model to operate the backend

Every run can generate training signal, closing the loop from production use to a fine-tuned model that runs the pipeline:

```bash
BIDPILOT_CAPTURE_TRAINING_DATA=1 bidpilot run <listing-url>   # 1. capture (system,prompt,output) per call
bidpilot mle collect                                          # 2. sweep runs; reviewer section edits become preference pairs
bidpilot mle export --stage-prefix shred                      # 3. train/val chat JSONL (+ DPO 'rejected' fields)
# 4. fine-tune (LoRA on an open model, or a provider fine-tune) and serve it
#    behind any OpenAI-compatible endpoint (vLLM / TGI / Ollama), then:
export BIDPILOT_CUSTOM_LLM_URL=https://your-host/v1
export BIDPILOT_CUSTOM_LLM_MODEL=bidpilot-ft-1
export BIDPILOT_CUSTOM_LLM_TIERS=fast        # graduate to 'all' when evals pass
# 5. ship gate: python -m evals.harness ... — recall >= 0.98 (G2) or it doesn't ship
```

The router keeps auditing every call (model, prompt hash, tokens) regardless of backend, structured outputs are schema-validated with one retry, and vision/OCR stays on the Anthropic API until the custom deployment is vision-capable. Human edits are the highest-value data: the export marks the reviewer's text as the preferred output and the machine draft as `rejected`.

## Evals (PRD §13, Phase 0)

```bash
pytest                                              # 76 deterministic tests, no network
python -m evals.collect "https://sam.gov/opp/<id>/view" <slug>   # freeze a corpus item
python -m evals.harness runs/<id>/compliance_matrix.json evals/corpus/<slug>/gold_matrix.csv
```

The harness scores requirement **recall** against hand-built gold matrices (G2 target ≥ 98% — misses are catastrophic, extra rows are cheap). `evals/collect.py` snapshots a notice (API JSON + attachments + gold-matrix template + ROI-timing notes) into the frozen corpus. Phase 0's 25-item corpus and 5 gold artifact sets live in `evals/corpus/` (not committed; see the PRD's Phase 0 plan — *do not skip it*).

## State of the build

Everything above is implemented and covered by 150 deterministic tests (no
network, no keys), including end-to-end pipeline runs with fake models and
with real DOCX/XLSX/fillable-PDF fixture attachments — plus, since the
initial build: the web UI/API (`bidpilot serve`, gates as browser
approvals, outcome capture), the MLE loop (training-data capture,
fine-tune export, custom-LLM serving with fail-closed promotion gates),
the P(win) advisor (heuristic now, trainable once outcomes accumulate),
the FPDS price-position collector (`bidpilot benchmark-price`, no key
needed), P(win)-ranked discovery, and the `kb.pro` market-researched KB
template. What the codebase now needs to advance is **real-world input,
not more code**:

1. **Keys** — `SAM_GOV_API_KEY` (free, via a SAM.gov account) and
   `ANTHROPIC_API_KEY`, then a first live run: `bidpilot doctor --network`,
   `bidpilot discover`, `bidpilot analyze <url>`.
2. **Phase 0** — freeze the 25-solicitation corpus (`python -m evals.collect`)
   and hand-build the 5 gold artifact sets; wire the recall sweep into the
   merge cadence. *The PRD is explicit: do not skip this.*
3. **A design partner** — live opportunities through the Analyst MVP first,
   then drafting; every live failure becomes a corpus item + test.
4. **Infra when scale demands it** — Postgres/pgvector behind
   `CheckpointStore`/`KnowledgeBase`/`SearchIndex`, the React reviewer UI,
   multi-tenancy (Phase 4).

## What's deliberately NOT here (v1 non-goals)

Autonomous submission (NG1 — hard product principle), CUI/classified processing (NG2 — halt-and-notify path instead), authenticated-portal flows like eBuy (NG3/v2), grants (NG4), teaming marketplace (NG5), win guarantees (NG6).

## Roadmap deltas vs. the PRD (v1 → production)

- pgvector retrieval behind `KnowledgeBase`; Postgres checkpoints behind `CheckpointStore` (interfaces are in place; v1 uses YAML + JSON files).
- Multi-tenant isolation + per-tenant encryption (FR-20) — v1 is single-tenant by design (PRD open question #1 recommends a design partner first).
- FastAPI + React reviewer UI with tracked edits (v1 ships `dashboard.html` + the run directory, per the PRD's "pragmatic v1" reviewer note).
- Learning loop (Phase 5.2): win/loss capture ships (`bidpilot outcome`, web outcome buttons -> P(win) training data); debrief ingestion into win-theme selection remains.
- Portal-specific checklists for PIEE/eBuy/FedConnect (Phase 5.3).
