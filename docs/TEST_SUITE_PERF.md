# Test-suite performance ledger — 2026-07-30

Bounded benchmark-optimization loop over the full deterministic suite
(`python -m pytest -q`, 159 tests). Correctness gate: 159 passed, no new
skips, repeatable. Metric: wall time on the dev container (4 cores, with
LibreOffice + playwright installed, so the heavy integration tests all
actually run — unlike CI's test job, see below).

## Evidence: where the time goes

The top ~16 integration tests account for ~42s of a ~44s serial run
(browser e2e 8.2s, orchestrator e2e ~1.6-5.6s each, rendering/docx,
drafts sync, server tests); the other 143 tests total ~2s. Independent
files + subprocess/IO-bound work → process parallelism is the lever.

## Variant table

| Variant | Hypothesis | Command | Wall | Correct? | Notes |
|---|---|---|---|---|---|
| baseline | serial | `python -m pytest -q` | 45.6s / 43.0s (repeats today: 40.9-66.6s) | yes | noisy box; band never overlaps winner |
| n4 | 1 worker/core | `-n 4` | 75.9s*, then 24.2 / 28.8 / 24.0 / 21.2s | 1 flake on first (cold) run, then 4x clean | *first run contaminated by cold caches/load; flake never reproduced in 6 later parallel runs |
| n8 | oversubscribe IO | `-n 8` | 26.4s | yes | startup+contention cancels gains |
| loadfile | file-scoped isolation | `-n 4 --dist loadfile` | 23.2s | yes | matches n4 within noise, strictly safer semantics |
| **winner** | portable cores + isolation | `python -m pytest -q -n auto --dist loadfile` | **22.8s** (confirm run, back-to-back with 43.0s baseline) | yes | promoted |

Best measured safe variant: `-n auto --dist loadfile` — ~1.9x. loadfile
keeps whole files on one worker, which respects in-file fixture scoping
(the threaded-uvicorn server fixture) and `test_browser_e2e`'s import of
helpers from `test_server`.

## Why CI stays serial

The CI `test` job finished the suite in 6-7s serially (run 30583394275:
no LibreOffice/playwright in that job, so the heavy tests degrade or
skip; render-exact and e2e cover them in their own jobs). xdist worker
startup would add more than it saves there. The promotion is scoped to
where the delta is real: the local dev loop.

## Rollback

Drop the `-n auto --dist loadfile` flags — the serial path is untouched
and remains the canonical correctness reference. pytest-xdist rides in
the `dev` extra.
