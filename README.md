# BidPilot

An agentic AI system for federal contract analysis & proposal generation.

**Input:** a URL to a contract opportunity on SAM.gov.
**Output:** a draft, submission-ready federal proposal package — plus machine-extracted submission instructions (who, where, how, by when, in what format).

Built per the [BidPilot PRD](https://docs.google.com/document/d/1-mtDfW1oh9v6KhHuKAbO1YLc1DAf2ft-TCM-K20b0qQ/edit): between input and output, an orchestrated pipeline of specialized Claude agents retrieves and parses the full solicitation (notice text plus all attachments), determines whether the bidding entity is eligible to compete, builds a compliance matrix from the solicitation's instructions and evaluation criteria, drafts the technical and management volumes, generates a cost/price estimate with a defensible basis of estimate, prepares the required government forms and representations, and assembles everything into the structure the solicitation demands.

## Two design principles from the PRD

1. **Human-in-the-loop by design.** BidPilot drafts, checks, and assembles; a human reviews, approves, signs, and submits. This is a legal and practical necessity in federal contracting, not a stylistic choice. Every package ships with a `REVIEW_CHECKLIST.md`, drafts carry `[[HUMAN: ...]]` placeholders instead of invented facts, certifications are never pre-answered, and the pipeline stops at an eligibility gate.
2. **SAM.gov is where opportunities are *posted*, not where proposals are *submitted*.** Proposals typically go by email to the Contracting Officer or through PIEE, GSA eBuy, FedConnect, or Unison Marketplace, as each solicitation instructs. BidPilot therefore produces a submission package **plus extracted submission instructions** — it never uploads or submits anything.

## Architecture

```
SAM.gov URL
    │
    ▼
┌─ 1. Retrieve ────────── samgov.py: notice metadata (Get Opportunities API) + attachments
├─ 2. Parse ───────────── documents.py: PDF/DOCX text extraction → corpus
│                         agents/parser.py: structured SolicitationAnalysis
├─ 3. Eligibility ─────── agents/eligibility.py  ◄── GATE: stops on hard blockers
├─ 4. Compliance ──────── agents/compliance.py: requirement-by-requirement matrix (CSV+JSON)
├─ 5. Submission ──────── agents/submission.py: who/where/how/when instructions
├─ 6. Draft volumes ───── agents/technical.py: one draft per required volume (Section L structure)
├─ 7. Cost estimate ───── agents/cost.py: ROM + basis of estimate + human pricing actions
├─ 8. Forms & reps ────── agents/forms.py: checklist with safe admin-only pre-fill
└─ 9. Assemble ────────── pipeline.py: manifest + REVIEW_CHECKLIST.md
    │
    ▼
output/<notice_id>/   ← the package a human reviews, prices, signs, and submits
```

Every inter-agent artifact is a typed Pydantic model (`bidpilot/models.py`), produced through Claude structured outputs so each stage's contract is validated. Long-form drafting streams with server-side refusal fallbacks enabled.

## Setup

```bash
pip install -e ".[dev]"

# Claude API — key or `ant auth login` profile
export ANTHROPIC_API_KEY=sk-ant-...

# SAM.gov opportunity metadata (free key from api.data.gov / your SAM.gov profile)
export SAM_GOV_API_KEY=...

# Your company profile drives eligibility, pricing, and form pre-fill
bidpilot init-profile          # creates company_profile.yaml from the example
$EDITOR company_profile.yaml
```

## Usage

```bash
bidpilot run "https://sam.gov/opp/<notice-id>/view"

# non-interactive (CI): auto-continue past review gates — still never submits
bidpilot run <notice-id> --yes --out output/

# cheaper/faster analysis pass
bidpilot run <notice-id> --effort medium
```

Output lands in `output/<notice_id>/`:

| File | What it is |
|---|---|
| `corpus.txt` | Full extracted solicitation text (notice + attachments) |
| `solicitation_analysis.json` | Structured analysis: scope, dates, volumes, eval factors, risks |
| `eligibility.json` | Verdict, per-requirement checks, blockers, human-review items |
| `compliance_matrix.csv` / `.json` | Every shall/must/format/submission requirement, with status |
| `SUBMISSION_INSTRUCTIONS.md` | Who/where/how/when to deliver the proposal |
| `volumes/*.md` | Draft proposal volumes, one per required volume |
| `cost/cost_estimate.md` / `.json` | ROM estimate + basis of estimate + required pricing decisions |
| `FORMS_CHECKLIST.md` | Required forms/reps with admin pre-fill and human sign-off items |
| `REVIEW_CHECKLIST.md` | **Start here** — everything a human must do before submission |
| `manifest.json` | Package manifest |

## Development

```bash
pytest            # unit tests (no network, no API key needed)
```

Key environment variables: `ANTHROPIC_API_KEY`, `SAM_GOV_API_KEY`, `BIDPILOT_MODEL` (default `claude-opus-5`).

## Roadmap (from the PRD)

- **Phase 0 (recommended next step per the PRD):** assemble a 25-solicitation corpus and hand-build gold artifacts (analysis, matrix, submission instructions) for five of them; use those as regression evals before deepening the agents.
- Matrix back-fill: parse `<!-- addresses L-x.y -->` markers in volume drafts to auto-populate `proposal_location`.
- Portal-specific submission playbooks (PIEE, eBuy, FedConnect, Unison) as human instructions.
- DOCX/PDF rendering of volumes to the solicitation's exact format spec.
- Amendment monitoring: re-run the pipeline diff when a notice is amended.
