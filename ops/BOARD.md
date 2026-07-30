# Agent board — improvement sprint 2026-07-29

Operating model per team-agent-orchestration: one owner per card, no
overlapping writes (writer cards get worktrees; review cards report to the
integrator), merge gate = full suite green + integrator diff review.
Integrator: main session.

| ID | Card | Owner | State | Scope | Acceptance | Merge gate |
|---|---|---|---|---|---|---|
| C1 | Security + API review of the web surface | security agent | merged | READ: bidpilot/server.py, routing.py, ml/, mle/ — report-only | severity-ranked findings w/ concrete fixes; no false-positive noise | integrator applies fixes; suite green |
| C2 | Competitive benchmark stage 2 (score 8 rivals) | research agent | merged | WRITE: docs/COMPETITIVE_BENCHMARK.md via integrator | 9-dimension scores + tension plot + primary-source verification | integrator review; sources cited |
| C3 | Prompt review (shredder/writers/QA vs recall target) | prompt agent | merged (doc only) | READ: bidpilot/agents/*.py prompts — proposals only, NOT applied | per-prompt findings + proposed rewrites + expected failure modes | BLOCKED-on-application: needs Phase-0 evals to validate; doc merges, prompts do not change |
| C4 | Browser e2e: paste listing -> gates -> export in real Chromium | e2e agent (worktree) | merged | WRITE: tests/test_browser_e2e.py (+ pyproject extra if needed) | new test passes headless vs real server+UI; skips cleanly without chromium; full suite green in worktree | integrator merges worktree; CI green |
| C5 | Multi-source discovery (Grants.gov/SLED behind discover) | integrator | merged | bidpilot/intake/, discover.py | pluggable OpportunitySource + 1 new source + mocked tests | shaped but deferred: verify Grants.gov API contract before assigning |
| C6 | Trust-invariants marketing surface | integrator | merged (draft) | site/README | copy that states fail-closed guarantees plainly | needs user's voice/brand input |

Evidence and handoffs land in docs/ (C1-C3) and the C4 worktree branch;
board updated by the integrator as cards move.

## Sprint outcome (2026-07-30)

- C1 merged: 7 findings (2 HIGH) -> fixes applied by integrator: UI XSS
  escaping, sensitive-file exclusion from web serving, gate-answer reset,
  api-key redaction in error paths, description-URL host allowlist, DTD
  rejection in FPDS XML, model-artifact sha256 verification before joblib
  load. HIGH-2 residual: no auth layer — server remains loopback-only by
  design; documented, revisit before any non-local deployment.
- C2 merged: docs/COMPETITIVE_BENCHMARK.md (8 rivals x 9 dims, tension
  plot; key finding: the enforced-trust quadrant is empty).
- C3 merged as doc only: docs/PROMPT_REVIEW.md (21 findings; application
  BLOCKED on Phase-0 evals, per gate).
- C4 merged: tests/test_browser_e2e.py (2 real-Chromium tests, worktree
  gate passed; agent hit session limit after committing — salvaged intact).

## Closeout (2026-07-30)

- C5 merged: `intake/sources.py` puts every feed behind one interface
  (SamGovSource + GrantsGovSource, public API, no key); a dead source never
  sinks the sweep; grants screen as `review` with set-aside/size marked
  not-applicable rather than silently passing. `bidpilot discover --grants`.
- C6 merged as a draft: README now states the fail-closed guarantees plainly,
  anchored to the Trust Manifest. Still wants the owner's voice pass before
  it becomes external marketing copy.
- Trust Manifest shipped (`bidpilot/trust.py`): the benchmark's #1
  recommendation - exportable proof of enforcement in every package.
- Board is clear. Everything remaining is input-blocked (keys, real
  solicitations, bid outcomes) - see ops/QUEUE.md.
