# Automation audit — 2026-07-30

Evidence-first inventory of every automation surface touching BidPilot
(per the automation-audit-ops discipline: nothing below is claimed live
without a proof path). Produced by three parallel audit agents (Routines
dump, repo surface, live GitHub state) plus direct session checks, then
acted on the same day — the RESOLUTION column records what changed.

## Current surface

| Automation | Surface | Live state | Proof | Resolution |
|---|---|---|---|---|
| CI workflow (`.github/workflows/ci.yml`, Actions id 321045787) | repo CI | live on branch; **absent from main** (main has no `.github/` dir, zero runs ever) | run 30568404018 success on 76a3f76; `GET .github/` on main = not found | KEEP + FIX: lint/e2e/eval jobs, nightly schedule, dispatch, action bumps added; merge to main still pending user go-ahead |
| SessionStart hook `scripts/session_setup.sh` | local runtime | live (async, 600s, conditional provisioning) | `.claude/settings.json:13-27` | KEEP |
| Ops queue/board protocol (`ops/QUEUE.md`, `ops/BOARD.md`) | repo state | live by convention (nothing reads it programmatically); queue was stale (C2 listed Active though merged); mandated `ops/ARCHIVE.md` missing | QUEUE.md:12,16-17 vs BOARD.md:11,28 | FIX: archive created, queue refreshed |
| Routine "BidPilot daily opportunity sweep" `trig_01UgAUxdFwqRP2AAnxHhadXM` | scheduler | PAUSED by design (cron 30 11 * * 1-5, disabled pending SAM_GOV_API_KEY) | trigger dump; docs/OPS_HARNESS.md:12 | KEEP paused; enable per checklist |
| Routine "Daily attention dashboard refresh" `trig_01UaWNnGDV4S7TJNXczNqd43` | scheduler (Cowork) | LIVE (cron 0 12 * * *, fired successfully 2026-07-30T12:10Z) | trigger dump: enabled true, next_run 2026-07-31 | KEEP |
| send_later one-shot `trig_01Bw4CAVLJYPTVPg6px6ZxmS` (Manager PR check-in) | scheduler | ENDED (run_once_fired 2026-07-11) — and its self-re-arming chain is DEAD: no successor exists, so nothing watches those PRs | trigger dump: ended_reason run_once_fired | CUT (deleted); "recreate or drop the watch" queued for user |
| One-shot `trig_017r7riTYtCCeMBR3u4oTxCw` (skills install) | scheduler | ENDED (run_once_fired 2026-06-30) | trigger dump | CUT — but created via http_api, so agents cannot delete it; inert either way, user can remove it in claude.ai Routines |
| Browser e2e (`tests/test_browser_e2e.py`) | repo CI | wired locally, **silently skipped on every CI run** (e2e extra/playwright never installed in CI) | old ci.yml install line `.[dev,tables,ml]`; importorskip at test_browser_e2e.py:22-25 | FIX: dedicated CI e2e job; 2 passed locally 2026-07-30 |
| Lint gate | repo CI | missing (no ruff/pre-commit config or CI step; 13 latent violations) | `ruff check .`: 8 E741 + 5 F401 | FIX: violations fixed to zero; ruff job added |
| MLE capture loop (`bidpilot/mle/`) | local runtime | manual & off by default (BIDPILOT_CAPTURE_TRAINING_DATA opt-in; nothing schedules collect/export) | routing.py:145-146; cli.py:260 | KEEP as-is; goes live with real runs per OPS_HARNESS checklist |
| Leftover agent worktree `agent-ae21206720056a804` | local runtime | stale (branch fully merged into 76a3f76 history) | `git worktree list`; BOARD.md:33 | CUT (pruned) |
| Session crons | scheduler | none exist | CronList empty | n/a |
| Connectors (22 installed) | external systems | 13 connected+enabled; **MailerLite enabled but unauthenticated**; 9 in unknown/disabled state (Linear, n8n, Stripe, Intercom, Cloudflare, DataHub, BasicOps, Kamai AI, HyperFrames) | ListConnectors 2026-07-30 | FIX NEXT (user): MailerLite OAuth or switch off; review the 9 dormant installs |
| Plugins (33 enabled account-wide) | external systems | configured; per-repo usefulness unreviewed | ListPlugins 2026-07-30 | FIX NEXT (user): keep/cut pass, out of repo scope |

## Findings

- **Biggest structural gap:** all CI history (23 runs) lives on
  `claude/analyze-and-build-attqzm`; main has no workflow file, no runs,
  no PR has ever existed, and no branch protection ties merging to the
  gate. The nightly `schedule:` added today cannot fire until the
  workflow reaches the default branch.
- **Historic failure streak explained:** CI runs #1-#17 all failed from a
  single root cause (`ModuleNotFoundError: No module named 'bidpilot.kb'`,
  14 collection errors, exit 2 — packaging, fixed at run #18/c6d9804);
  #18-#23 green.
- **Silent-skip hazard:** the C4 browser-e2e deliverable never actually
  executed in CI — a green checkmark was quietly weaker than it looked.
- **Dead monitoring chain:** the Manager PR babysitting loop ended
  without re-arming; ended one-shots look harmless in a trigger list but
  this one represented monitoring that stopped.
- **No overlaps found** among the four Routines — they cover disjoint
  jobs (SAM sweep, attention dashboard, and two spent one-shots).
- Runner deprecation notice: checkout@v4 / setup-python@v5 target Node 20
  (bumped to v5/v6 today).

## Canonical lane going forward

One workflow (`CI`) is the single quality gate: lint → tests+evals →
browser e2e → exact-render, on push/PR/dispatch plus a nightly scheduled
run (default-branch only). The claude.ai Routine layer stays minimal: one
LIVE dashboard refresh, one PAUSED sweep that goes live with the API key
per `docs/OPS_HARNESS.md`. `ops/QUEUE.md` remains the sole cross-session
handoff file, now with the mandated `ops/ARCHIVE.md`.
