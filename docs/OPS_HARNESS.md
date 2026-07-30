# BidPilot autonomous ops harness

How BidPilot runs itself between human sessions, built ONLY from native
Claude Code capabilities that exist in this workspace (Routines/scheduled
triggers, repo-committed state, SessionStart hooks, deterministic CLI
commands). No external agent framework, no speculative MCP servers.

## Architecture (what actually exists)

| Harness layer | Implementation | State |
|---|---|---|
| Scheduler | claude.ai Routine "BidPilot daily opportunity sweep" (weekday cron, fresh session per fire) | created, DISABLED until SAM_GOV_API_KEY exists |
| Event watch | `bidpilot watch-amendments` (exit 1 on stale chains — alertable) inside the sweep | live code, tested |
| Discovery | `bidpilot discover` ranked by heuristic P(win) | live code, tested |
| Persistent memory | repo-committed files: `ops/QUEUE.md` (task queue), run dirs (checkpointed state), `ml_outcomes.jsonl` + `training_capture.jsonl` (learning signal) | live |
| Self-provisioning | SessionStart hook `scripts/session_setup.sh` (extras, cffi, LibreOffice — async, conditional) | live, pipe-tested |
| Learning loop | capture -> `mle collect/export` -> fine-tune -> `mle gate` (fail-closed) ; outcomes -> `train_pwin` (fail-closed) | live code; waiting on real data |
| Quality gate | CI on every push/PR + manual dispatch + nightly schedule (default-branch only): ruff lint (rule set pinned in pyproject), 159-test suite (its 2 browser-e2e tests execute in a dedicated Chromium job), demo-corpus eval harness, exact-render probe | live on branch; schedule fires once merged to main (see docs/AUTOMATION_AUDIT.md) |

Session isolation rule: scheduled sessions share NOTHING with interactive
sessions except what is committed to the repo or written to run
directories. Every autonomous surface therefore reads/writes repo files —
`ops/QUEUE.md` is the handoff point.

## Consent and safety boundaries (explicit, per the harness skill)

Approved and active:
- read-only daily sweep (report + queue append; Routine currently disabled
  pending keys)
- session self-provisioning (dependency install only)
- CI on push

Structurally forbidden regardless of automation (PRD invariants — these
bind scheduled sessions exactly as they bind humans-in-the-loop sessions):
- no autonomous pipeline runs past a human gate; gates block, always
- never sign, never submit, never answer certifications
- no autonomous `bidpilot amend` — the sweep DETECTS stale chains; a human
  triggers re-processing
- no model promotion without the fail-closed gates (`mle gate`, P(win)
  metrics sidecar)
- scheduled sessions are report-only: their sole output is the run's
  summary notification. They do not push commits — ops/QUEUE.md is updated
  by interactive sessions acting on those reports (an ephemeral write
  without a push would die with the container anyway)

Not enabled (would need explicit approval + setup): computer use, external
posting, write-access to third-party systems, autonomous git pushes from
scheduled sessions.

## Enable checklist (the whole harness goes live in ~5 minutes)

1. Add `SAM_GOV_API_KEY` (and `ANTHROPIC_API_KEY` for drafting stages) to
   the environment's config.
2. Enable the Routine "BidPilot daily opportunity sweep" in claude.ai
   Routines (or ask a session to enable trigger
   `trig_01UgAUxdFwqRP2AAnxHhadXM`).
3. Optional: set `BIDPILOT_CAPTURE_TRAINING_DATA=1` on the environment so
   every run feeds the fine-tune dataset.
4. Watch the Routine's completion notifications each weekday morning;
   carry anything actionable into `ops/QUEUE.md` (or ask a session to).

## Verification discipline

A scheduled task that fails silently is worse than no task. The sweep
prompt requires an explicit failure report when keys are missing; Routine
completion notifications surface each run; and the queue file is the
durable record — if it stops changing on weekdays, the harness is broken
and `bidpilot doctor` plus the Routine run history are the first two
places to look.
