"""Intake agent A1: URL -> NoticePackage with the FULL amendment chain.

Bidding off a stale version is a classic fatal error, so intake fetches all
notices sharing the solicitation number, orders them by posted date, marks
the latest, and downloads attachments from every notice in the chain
(amendment attachments supersede/extend base ones).
"""

from __future__ import annotations

from pathlib import Path

from ..models import AmendmentRecord, AttachmentRecord, NoticePackage
from .samgov import SamGovClient, parse_notice_id


def run_intake(sam: SamGovClient, url_or_id: str, dest_dir: Path) -> NoticePackage:
    notice_id = parse_notice_id(url_or_id)
    metadata = sam.notice_metadata(notice_id)

    # Amendment chain: every notice under the same solicitation number.
    chain = order_amendment_chain(
        sam.search_by_solicitation_number(metadata.solicitation_number)
        if metadata.solicitation_number
        else []
    )
    if not chain:
        chain = [AmendmentRecord(notice_id=notice_id, posted_date=metadata.posted_date, is_latest=True)]

    # If the linked notice is not the latest in the chain, re-anchor metadata on
    # the latest — its deadline and attachments govern.
    latest = next(rec for rec in chain if rec.is_latest)
    if latest.notice_id != notice_id:
        metadata = sam.notice_metadata(latest.notice_id)
        metadata.notice_id = latest.notice_id

    files = []
    for rec in chain:
        files.extend(sam.download_attachments(rec.notice_id, dest_dir))

    # De-duplicate identical files fetched from multiple notices in the chain.
    seen: set[str] = set()
    unique_files = []
    for f in files:
        key = f.sha256 or f.name
        if key in seen:
            continue
        seen.add(key)
        unique_files.append(f)

    return NoticePackage(
        metadata=metadata,
        files=unique_files,
        amendment_history=chain,
        restricted_files_flagged=any(f.restricted for f in unique_files),
    )


def register_manual_attachments(package: NoticePackage, dest_dir: Path) -> list[str]:
    """Pick up files a human dropped into the attachments dir (the manual-
    upload fallback for login-restricted attachments, FR-1/A1).

    A restricted attachment whose expected filename now exists on disk loses
    its `restricted` flag; any other unrecognized file is registered as a new
    attachment. Returns the names picked up."""
    import hashlib

    if not dest_dir.exists():
        return []
    known = {Path(f.local_path).name for f in package.files}
    picked_up: list[str] = []

    for record in package.files:
        path = Path(record.local_path)
        if record.restricted and path.exists() and path.stat().st_size > 0:
            record.restricted = False
            record.sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
            picked_up.append(record.name)

    for path in sorted(dest_dir.iterdir()):
        if not path.is_file() or path.name.startswith(".") or path.name in known:
            continue
        package.files.append(
            AttachmentRecord(
                name=path.name,
                local_path=str(path),
                sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                source_notice_id=None,  # manually supplied, not from a notice
            )
        )
        picked_up.append(path.name)

    package.restricted_files_flagged = any(f.restricted for f in package.files)
    return picked_up


def unprocessed_manual_attachments(package: NoticePackage, dest_dir: Path) -> list[str]:
    """Files on disk that docproc hasn't seen (dropped after docproc ran)."""
    if not dest_dir.exists():
        return []
    known = {Path(f.local_path).name for f in package.files if not f.restricted}
    return sorted(
        p.name for p in dest_dir.iterdir()
        if p.is_file() and not p.name.startswith(".") and p.name not in known
    )


def order_amendment_chain(records: list[dict]) -> list[AmendmentRecord]:
    """Order raw API records by posted date; mark the newest as latest."""
    def _key(r: dict) -> str:
        return r.get("postedDate") or ""

    ordered = sorted(records, key=_key)
    chain = [
        AmendmentRecord(
            notice_id=(r.get("noticeId") or r.get("noticeid") or "").lower(),
            posted_date=r.get("postedDate"),
            title=r.get("title"),
        )
        for r in ordered
        if r.get("noticeId") or r.get("noticeid")
    ]
    if chain:
        chain[-1].is_latest = True
    return chain
