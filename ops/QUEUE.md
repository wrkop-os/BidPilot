# BidPilot ops queue

Persistent task queue for autonomous operation. Repo-committed so it
survives session boundaries — scheduled (Routine) sessions and interactive
sessions share state through this file, not through session context.

Conventions:
- One line per task, checkbox format. Date-stamp when added.
- Scheduled sweeps surface candidate work in their notifications; a human
  (or an interactive session directed by one) carries it in here and
  checks items off. Scheduled sessions never push commits.
- Keep under ~40 open items; archive completed blocks to ops/ARCHIVE.md.

## Active

- [ ] Competitive benchmark stage 2: score the 8 profiled rivals per
      benchmark-methodology (docs/COMPETITIVE_LANDSCAPE.md) (added 2026-07-29)

- [ ] Enable the daily sweep Routine once SAM_GOV_API_KEY is set on the
      environment (trigger: "BidPilot daily opportunity sweep", currently
      disabled) (added 2026-07-29)
- [ ] First live run: `bidpilot doctor --network`, then `bidpilot analyze`
      on a real listing (blocked on: SAM + Anthropic keys) (added 2026-07-29)
- [ ] Phase-0 corpus: freeze 25 solicitations, hand-build 5 gold matrices
      (blocked on: real listings) (added 2026-07-29)
- [ ] Start recording bid outcomes (`bidpilot outcome` / web buttons);
      P(win) training unlocks at 30 labeled (added 2026-07-29)

## Completed

- [x] CI green on the branch (gitignore fix c6d9804, verified 2026-07-29)
- [x] Amendment watch + session provisioning hook shipped (2026-07-29)
