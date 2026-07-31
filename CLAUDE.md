# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Agentic AI system: SAM.gov opportunity URL → reviewed, compliant, priced
federal proposal package. Built to `README.md`'s PRD; read that first.

## Commands

```bash
pip install -e ".[dev,tables]"   # tables = pdfplumber; add [server] for the web UI
python -m pytest -q              # full suite: deterministic, no network/keys
python -m pytest -q -n auto --dist loadfile   # same suite ~2x faster locally (see docs/TEST_SUITE_PERF.md)
python -m pytest tests/test_rates.py::test_wrap_rate -q   # single test
bidpilot doctor                  # env checks (--network pings the SAM API)
bidpilot serve --port 8400       # web UI: paste listing URL, gates in browser
bidpilot mle collect             # sweep runs for captured training data
bidpilot kb-health --kb kb.pro   # KB quality gate (--strict exits 1 on issues)
bidpilot kb-gaps --out runs      # what runs needed that the KB could not supply
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
| Offline intake (folder of documents, no API) | `bidpilot/intake/local.py` — `run --local DIR`; metadata is extracted deterministically or reported missing, never guessed |
| Doc parsing ladder, OCR, retrieval index | `bidpilot/docproc/` (`index.py` = BM25-style retrieval; pgvector goes behind this interface) |
| Agents (one module per PRD roster entry) | `bidpilot/agents/` |
| Deterministic pricing (wrap rates, WD floors) | `bidpilot/pricing/rates.py` — pure code, never LLM |
| FAR compliance checks on the cost volume | `bidpilot/pricing/compliance.py` — read `docs/PRICING_COMPLIANCE.md` before changing a threshold or severity |
| SCA margin erosion projection | `bidpilot/pricing/sca_erosion.py` — what 52.222-43 compliance costs in unrecovered OH/G&A/fee |
| Local secrets (`.env`, gitignored) | `bidpilot/config.py` — a real env var always beats the file |
| Fail-closed QA checks | `bidpilot/qa_checks.py` + `agents/qa.py` (LLM half) |
| Rendering + exact page counts | `bidpilot/rendering.py` |
| Reviewer edit loop | `bidpilot/drafts.py` (`sync-drafts`, clobber protection) |
| Static domain data | `bidpilot/data/` (SBA size standards, clause regexes, portal playbooks) |
| KB knowledge ops (gap mining, health) | `bidpilot/kb/ops.py` — deterministic, read-only; never edits the KB |
| Company KB (sole source of company facts) | `bidpilot/kb/`; templates: `kb.example/` (minimal, used by tests) and `kb.pro/` (market-researched rates — sources in `kb.pro/MARKET_RESEARCH.md`) |
| Web UI/API (listing in → gates → package out) | `bidpilot/server.py` (`bidpilot serve`; gates block on browser approval) |
| Keyless model backend (any OpenAI-compatible endpoint) | `bidpilot/routing.py` `CustomLLMBackend` — retries 429/5xx/transport, tolerates prose-wrapped JSON; contract-tested over a real socket in `tests/test_custom_llm_live.py` |
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
- Regulatory applicability comes from the literal presence of a clause in the
  solicitation, never from contract value. Agencies delete and renumber FAR
  Part 15/22 provisions under Revolutionary FAR Overhaul class deviations, so
  a dollar threshold may only *explain an absence* — it may never manufacture
  a requirement. Dollar constants carry a cite and an `AS_OF`, and never
  hard-fail.
- Commit messages: plain ASCII quotes (shell-quote with single quotes).
- New failure discovered live → corpus item + test before the fix (PRD rule).
- `.claude/skills/` is a vendored snapshot of the ECC skill library
  (affaan-m/everything-claude-code, enumerated from its
  manifests/install-modules.json). Treat it as third-party vendor code:
  don't hand-edit individual skills; refresh by re-vendoring from upstream.
