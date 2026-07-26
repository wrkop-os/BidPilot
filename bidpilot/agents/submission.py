"""Submission Instructions agent (A11): who / where / how / by when / in what
format — normalized into a one-page SubmissionInstructionSheet plus ICS
calendar entries (questions deadline, T-48h internal deadline, deadline).

SAM.gov is where opportunities are POSTED, not where proposals are
SUBMITTED. Dual extraction + cross-check on the deadline because timezone
bugs have literally lost real bids."""

from __future__ import annotations

import datetime as _dt
import re
from typing import Optional

from ..models import DocTree, NoticeMetadata, SubmissionSheet
from ..routing import ModelRouter, Tier

SYSTEM = """You extract proposal submission instructions from federal solicitations
with zero tolerance for guessing.
- Quote destinations (email/portal/address), deadlines, size limits, subject-line
  requirements, and format rules exactly as written.
- deadline_timezone: the timezone EXACTLY as stated; if none is stated, null.
- The SAM.gov metadata deadline is provided for CROSS-CHECK: if it conflicts
  with the solicitation text, report the solicitation's version and flag the
  conflict in confidence_notes (the latest amendment governs).
- Never invent a destination: if none is found, channel='unknown' and
  destination='NOT FOUND — human must contact the Contracting Officer'."""


def extract_submission(
    router: ModelRouter, metadata: NoticeMetadata, doc_tree: DocTree
) -> SubmissionSheet:
    prompt = f"""Extract the submission instructions.

=== SAM.GOV METADATA (cross-check only) ===
Response deadline per SAM.gov: {metadata.response_deadline or "unknown"}
Points of contact: {metadata.points_of_contact}

=== SOLICITATION CORPUS ===
{doc_tree.corpus()[:300_000]}"""
    sheet = router.structured(
        Tier.FRONTIER, system=SYSTEM, prompt=prompt, output_type=SubmissionSheet, stage="submission",
    )
    # Deterministic cross-check: flag SAM-vs-solicitation deadline mismatch.
    if metadata.response_deadline and metadata.response_deadline[:10] not in sheet.deadline:
        note = (
            f"SAM.gov metadata deadline ({metadata.response_deadline}) does not appear in the "
            f"extracted deadline ({sheet.deadline}) — verify against the latest amendment."
        )
        sheet.confidence_notes = f"{sheet.confidence_notes} {note}".strip() if sheet.confidence_notes else note
    return sheet


def sheet_to_markdown(sheet: SubmissionSheet) -> str:
    lines = [
        "# Submission Instruction Sheet",
        "",
        f"- **Channel:** {sheet.channel}",
        f"- **Destination:** {sheet.destination}",
        f"- **Deadline:** {sheet.deadline}" + (f" ({sheet.deadline_timezone})" if sheet.deadline_timezone else " ⚠️ no timezone stated — verify"),
        f"- **Questions due:** {sheet.questions_deadline or 'not stated'}",
        f"- **Max attachment size:** {sheet.max_attachment_size or 'not stated'}",
        f"- **Subject line:** {sheet.subject_line_requirements or 'not stated'}",
        f"- **Copies:** {sheet.copies or 'not stated'}",
    ]
    if sheet.file_format_rules:
        lines.append("\n## File format rules")
        lines += [f"- {r}" for r in sheet.file_format_rules]
    if sheet.special_instructions:
        lines.append("\n## Special instructions")
        lines += [f"- {s}" for s in sheet.special_instructions]
    if sheet.confidence_notes:
        lines.append(f"\n## ⚠️ Verify before submitting\n{sheet.confidence_notes}")
    lines.append(
        "\n---\n*BidPilot never submits. A human verifies these instructions "
        "against the latest amendment and delivers the package.*"
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# ICS calendar artifact (deterministic)
# ---------------------------------------------------------------------------


_DT_PATTERNS = ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d")


def _try_parse(dt_text: str) -> Optional[_dt.datetime]:
    cleaned = dt_text.strip()
    for pattern in _DT_PATTERNS:
        try:
            return _dt.datetime.strptime(cleaned, pattern)
        except ValueError:
            continue
    m = re.search(r"(\d{4}-\d{2}-\d{2})[T ]?(\d{2}:\d{2})?", cleaned)
    if m:
        date_part = m.group(1)
        time_part = m.group(2) or "12:00"
        try:
            return _dt.datetime.strptime(f"{date_part} {time_part}", "%Y-%m-%d %H:%M")
        except ValueError:
            return None
    return None


def _ics_event(uid: str, summary: str, dt: _dt.datetime) -> str:
    stamp = dt.strftime("%Y%m%dT%H%M%S")
    return "\n".join(
        [
            "BEGIN:VEVENT",
            f"UID:{uid}@bidpilot",
            f"DTSTART:{stamp}",
            f"DTEND:{stamp}",
            f"SUMMARY:{summary}",
            "END:VEVENT",
        ]
    )


def build_ics(sheet: SubmissionSheet, title: str) -> Optional[str]:
    """Calendar entries: questions deadline, submission deadline − 48h
    internal deadline, submission deadline (PRD §10.6). Returns None when the
    deadline can't be parsed deterministically — no guessed calendar entries."""
    deadline = _try_parse(sheet.deadline)
    if deadline is None:
        return None
    events = [
        _ics_event("internal", f"INTERNAL deadline (T-48h): {title}", deadline - _dt.timedelta(hours=48)),
        _ics_event("submission", f"SUBMISSION DEADLINE: {title}", deadline),
    ]
    if sheet.questions_deadline:
        questions = _try_parse(sheet.questions_deadline)
        if questions:
            events.insert(0, _ics_event("questions", f"Questions due: {title}", questions))
    body = "\n".join(events)
    return f"BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//BidPilot//EN\n{body}\nEND:VCALENDAR\n"
