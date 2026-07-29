# BidPilot — contributor guide

Agentic AI system: SAM.gov opportunity URL → reviewed, compliant, priced
federal proposal package. Built to `README.md`'s PRD; read that first.

## Commands

```bash
pip install -e ".[dev,tables]"   # tables = pdfplumber (optional)
python -m pytest -q              # full suite: deterministic, no network/keys
bidpilot doctor                  # env checks (--network pings the SAM API)
python -m evals.harness evals/corpus_demo/sample_extracted_matrix.json evals/corpus_demo/gold_matrix.csv
```

Exact page counting requires `libreoffice-writer` (`soffice`); tests degrade
gracefully without it.

## Architecture map

| Concern | Where |
|---|---|
| Typed stage contracts (the blackboard) | `bidpilot/models.py`, `bidpilot/state.py` (`ProposalState`, `CheckpointStore`) |
| Graph orchestrator, gates, halt/resume semantics | `bidpilot/orchestrator.py` — read `run()`'s docstring before touching resume logic |
| Model routing + audit of every LLM call | `bidpilot/routing.py` (FAST=haiku for volume, FRONTIER=opus for judgment), `bidpilot/audit.py` |
| SAM.gov intake + amendment chain | `bidpilot/intake/` |
| Doc parsing ladder, OCR, retrieval index | `bidpilot/docproc/` (`index.py` = BM25-style retrieval; pgvector goes behind this interface) |
| Agents (one module per PRD roster entry) | `bidpilot/agents/` |
| Deterministic pricing (wrap rates, WD floors) | `bidpilot/pricing/rates.py` — pure code, never LLM |
| Fail-closed QA checks | `bidpilot/qa_checks.py` + `agents/qa.py` (LLM half) |
| Rendering + exact page counts | `bidpilot/rendering.py` |
| Reviewer edit loop | `bidpilot/drafts.py` (`sync-drafts`, clobber protection) |
| Static domain data | `bidpilot/data/` (SBA size standards, clause regexes, portal playbooks) |
| Company KB (sole source of company facts) | `bidpilot/kb/` |
| Web UI/API (listing in → gates → package out) | `bidpilot/server.py` (`bidpilot serve`; gates block on browser approval) |
| MLE loop (capture → collect → export → serve custom LLM) | `bidpilot/mle/`; router env: `BIDPILOT_CUSTOM_LLM_URL/_MODEL/_TIERS`, `BIDPILOT_CAPTURE_TRAINING_DATA` |

## Non-negotiable invariants (PRD §14)

1. **Never sign, never submit.** No code path may transmit a proposal or
   pre-answer a certification/representation.
2. **Fail closed on company facts (FR-10).** Writer claims cite KB entry IDs
   or carry `[NEEDS INPUT]`; uncited claims are HARD QA failures that block
   export. Don't weaken `qa_checks.citation_check`.
3. **Code computes, LLMs draft.** Rates, escalation, wage-determination
   floors, page limits, coverage, dedup, file naming = deterministic code.
   Never move these into prompts.
4. **Recall over cost for the shredder/eligibility/submission/classifier** —
   they read the full corpus. The retrieval index is for writers only.
5. **Halts are re-evaluated, never bypassed.** A halting stage is never
   marked done; declined gates re-ask on resume (see `run()`).
6. **Human edits are sacred.** Never overwrite an unsynced section file
   (`drafts.py` sidecar); never auto-redraft a `human_edited` section.

## Conventions

- Every new inter-agent artifact is a Pydantic model in `models.py` and a
  field on `ProposalState`; artifacts are written to the run dir by
  `assembly.write_stage_artifacts`.
- Every LLM call goes through `ModelRouter` (audit + tier routing). New
  stages record a `stage=` label — telemetry groups on the prefix before `.`.
- Deterministic logic gets unit tests; agent flows get fake-router e2e tests
  (see `tests/test_orchestrator_e2e.py` — extend `FakeRouter` by schema name).
- Commit messages: plain ASCII quotes (shell-quote with single quotes).
- New failure discovered live → corpus item + test before the fix (PRD rule).
