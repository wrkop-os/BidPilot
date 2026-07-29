"""Amendment watch (FR-4 automation): sweep every run directory, re-query
the amendment chain by solicitation number, and flag runs whose known chain
is stale. Deterministic and cron-able — exit code 1 when action is needed,
so a scheduler or Routine can alert on it. Detection only: applying an
amendment stays the human-triggered `bidpilot amend` (halts are never
bypassed, invariant 5).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .intake.samgov import SamGovClient
from .state import CheckpointStore


@dataclass
class WatchResult:
    notice_id: str
    solicitation_number: str | None
    known: int = 0
    live: int = 0
    new_notice_ids: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def stale(self) -> bool:
        return bool(self.new_notice_ids)


def check_run(sam: SamGovClient, run_dir: Path) -> WatchResult | None:
    state = CheckpointStore(run_dir).load()
    if state is None or state.notice is None:
        return None
    meta = state.notice.metadata
    result = WatchResult(
        notice_id=meta.notice_id, solicitation_number=meta.solicitation_number
    )
    known = {a.notice_id.lower() for a in state.notice.amendment_history}
    known.add(meta.notice_id.lower())
    result.known = len(known)
    if not meta.solicitation_number:
        result.error = "no solicitation number on record - cannot re-query the chain"
        return result
    try:
        records = sam.search_by_solicitation_number(meta.solicitation_number)
    except Exception as exc:
        result.error = f"SAM query failed: {type(exc).__name__}"
        return result
    live_ids = {
        (r.get("noticeId") or "").lower() for r in records if r.get("noticeId")
    }
    result.live = len(live_ids)
    result.new_notice_ids = sorted(live_ids - known)
    return result


def watch_all(sam: SamGovClient, output_root: Path) -> list[WatchResult]:
    results = []
    if not output_root.is_dir():
        return results
    for run_dir in sorted(output_root.iterdir()):
        if run_dir.is_dir() and (run_dir / "state.json").exists():
            checked = check_run(sam, run_dir)
            if checked is not None:
                results.append(checked)
    return results


def report_lines(results: list[WatchResult]) -> list[str]:
    lines = []
    for r in results:
        if r.error:
            lines.append(f"? {r.notice_id[:12]} ({r.solicitation_number or 'no solnum'}): {r.error}")
        elif r.stale:
            lines.append(
                f"! {r.notice_id[:12]} ({r.solicitation_number}): {len(r.new_notice_ids)} NEW "
                f"amendment notice(s) {', '.join(n[:12] for n in r.new_notice_ids)} - "
                f"run `bidpilot amend` for this notice"
            )
        else:
            lines.append(
                f"  {r.notice_id[:12]} ({r.solicitation_number}): chain current "
                f"({r.known} known / {r.live} live)"
            )
    return lines
