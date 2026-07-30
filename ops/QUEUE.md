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

- [ ] Merge the working branch into main so the CI workflow exists on the
      default branch — until then the nightly `schedule:` trigger cannot
      fire and main has never run CI. Needs the user's go-ahead for the
      PR/merge (added 2026-07-30, from automation audit)
- [ ] After the merge: enable branch protection on main requiring the CI
      checks (admin console action; neither branch is protected today)
      (added 2026-07-30, from automation audit)
- [ ] Enable the daily sweep Routine once SAM_GOV_API_KEY is set on the
      environment (trigger: "BidPilot daily opportunity sweep", currently
      disabled) (added 2026-07-29)
- [ ] First live run: `bidpilot doctor --network`, then `bidpilot analyze`
      on a real listing (blocked on: SAM + Anthropic keys) (added 2026-07-29)
- [ ] Phase-0 corpus: freeze 25 solicitations, hand-build 5 gold matrices
      (blocked on: real listings) (added 2026-07-29)
- [ ] Start recording bid outcomes (`bidpilot outcome` / web buttons);
      P(win) training unlocks at 30 labeled (added 2026-07-29)
- [ ] MailerLite connector is toggled on in chat but unauthenticated —
      finish OAuth in claude.ai connector settings, or switch it off
      (added 2026-07-30, from automation audit)
- [ ] Decide fate of the dead wrkop-os/Manager PR-watch loop: its
      send_later chain fired 2026-07-11 and never re-armed, so nothing is
      monitoring those PRs. Recreate the watch if they still matter,
      otherwise drop (added 2026-07-30, from automation audit)

## Completed

- [x] Automation audit + canonical CI lane (lint/e2e/evals/nightly) +
      dead-state cleanup; evidence in docs/AUTOMATION_AUDIT.md
      (2026-07-30) — earlier completed blocks moved to ops/ARCHIVE.md
