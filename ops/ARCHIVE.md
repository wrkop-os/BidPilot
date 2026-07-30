# BidPilot ops archive

Completed blocks moved out of `ops/QUEUE.md` (per the queue convention:
keep the queue under ~40 open items, archive finished work here with the
date it was verified done).

## Archived 2026-07-30

- [x] CI green on the branch (gitignore fix c6d9804, verified 2026-07-29)
- [x] Amendment watch + session provisioning hook shipped (2026-07-29)
- [x] Competitive benchmark stage 2: 8 rivals scored across 9 dimensions,
      tension plot, primary-source verification — merged as
      docs/COMPETITIVE_BENCHMARK.md (board card C2, integrated 76a3f76;
      the queue had gone stale on this item, caught by the 2026-07-30
      automation audit)
- [x] Automation audit (2026-07-30): full inventory of Routines, repo
      hooks, CI, connectors, plugins with live-state classification —
      docs/AUTOMATION_AUDIT.md
- [x] CI upgraded to canonical lane: ruff lint job (repo at zero
      violations), browser-e2e job (was silently skipping in CI),
      deterministic eval-harness step, nightly schedule + manual dispatch,
      actions/checkout@v5 + setup-python@v6 (2026-07-30)
- [x] Pruned merged leftover agent worktree
      .claude/worktrees/agent-ae21206720056a804 and its local branch
      (board card C4 residue; content already in 76a3f76 history)
      (2026-07-30)
- [x] Deleted the spent send_later one-shot Routine (2026-07-11 Manager
      PR check-in, ended_reason run_once_fired). The other spent one-shot
      (skills install) was created via http_api and only its owner can
      delete it — inert regardless (2026-07-30)
