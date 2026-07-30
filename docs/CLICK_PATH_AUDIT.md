# Click-path audit — web UI (bidpilot/server.py)

Method: /click-path-audit. Scope: the single-page operator UI and the
server-side state it drives. Date 2026-07-30. All findings below were
reproduced before fixing and carry a regression test.

## Step 1 — state map

The "store" here is `RunHandle` (mutated by the pipeline thread, read by the
API thread) plus the client `setInterval(refresh, 2000)` poll that replaces
`#runs` innerHTML wholesale.

```
RunHandle
  confirm(question)   [pipeline thread] sets {pending_gate, gate_token(new),
                      _gate_answer:None} clears event; WAITS; then RESETS
                      {pending_gate:None, gate_token:None}
  answer_gate(ok,tok) [API thread] guards pending_gate, matches token,
                      sets {_gate_answer}, sets event
  running             thread.is_alive()

create_app
  start_run           creates handle, builds ctx, registers in `runs`
  _status             READS ctx.state (live, mutating) + rglob(run_dir)

client refresh()      replaces the entire #runs subtree every 2s
```

Dangerous interactions: `_status` reads state another thread is writing;
`refresh()` destroys any DOM state it does not re-create; an answer written
by one thread is consumed by another after a wait.

## Findings

**CP-001 CRITICAL — Missing State Transition / cascading dead path.**
`start_run` registered the handle in `runs` *before* assigning `ctx`. A
`ctx_builder` failure (`load_kb()` raises FileNotFoundError when `./kb` is
absent — the likeliest first-run error) left a ctx-less handle, so every
later `GET /api/runs` hit `handle.ctx.state` -> AttributeError -> 500. The
poll then threw on `.json()` and **the dashboard was permanently dead for
every run until restart**. Reproduced: POST 500 -> GET /api/runs 500.
Fix: build ctx before registering; failures return a 500 with the reason and
leave `runs` untouched. Test: `test_failed_ctx_builder_does_not_brick_the_dashboard`.

**CP-002 HIGH — Async race / answer not bound to the question (FR-13).**
`answer_gate` accepted any answer whenever *some* gate was pending. The UI
polls every 2s, so it can still display gate N after the pipeline has moved
to gate N+1; a second click (double-click, or "did that register?") answered
the *next* question — up to and including the final-package gate — without
the operator ever reading it. Fix: `confirm()` mints a `gate_token` per gate,
status exposes it, the UI echoes it, and a mismatch is refused with 409.
Test: `test_stale_gate_answer_cannot_answer_the_next_gate`.

**CP-003 MEDIUM — Sequential undo in training data.**
Outcome buttons append to `ml_outcomes.jsonl` with no identity collapse, so a
double-click double-weighted a bid and a Won->Lost correction fed the trainer
two contradictory labels for one notice. Fix: `load_outcomes` collapses to the
newest row per notice (append-only audit trail preserved on disk via
`latest_only=False`). Test: `test_corrected_outcome_supersedes_earlier_label`.

**CP-004 MEDIUM — Poll interference with operator intent.**
The 2s wholesale innerHTML replacement slammed shut any `<details>` panel the
operator had opened — including the live log they were reading to follow a
run. Fix: capture open panel ids before render, re-apply after.

**CP-005 LOW — Missing state transition on outcome buttons.**
Success was an `alert()` only; after dismissing it the UI looked identical, so
the natural response was to click again (feeding CP-003). Fix: status carries
`outcome_recorded` and the panel shows "outcome recorded: won (click to
correct)". The browser e2e now asserts this visible transition rather than the
alert.

**CP-006 LOW — Non-JSON error responses.**
`(await r.json()).detail` threw on any non-JSON error body, leaving the start
error blank and skipping `refresh()`. Fix: try/catch with an HTTP-status
fallback message.

## Verified sound

- Approve/Decline write the same field via one guarded path; no second call
  resets it (the classic sequential-undo shape is absent).
- `start_run` 409-on-running prevents concurrent handles for one notice.
- Artifact links are plain hrefs — no state mutation, no dead links (sensitive
  files are excluded from the listing *and* refused by the endpoint).
- `analyze_only` is read at submit time; no stale-closure capture.
