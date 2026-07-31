"""Local intake: a folder of solicitation documents instead of a SAM.gov URL.

The API path (`intake.run_intake`) is the happy path, but it is not always
available, and its absence should never make the product unusable:

- Restricted networks. Plenty of contractors work behind an egress policy that
  does not allow api.sam.gov. That is a security posture, not a bug to route
  around.
- Rate limits. Non-federal api.data.gov keys get a modest daily quota.
- Login-gated attachments. Some packages are only downloadable from the portal
  by a signed-in human — the API lists them but cannot fetch them.
- Documents that never had a SAM.gov listing at all: a teaming partner's RFP
  package, an agency emailing a draft, a state or commercial solicitation.

So intake accepts a directory. Everything downstream — shredder, eligibility,
pricing, QA, assembly — is unchanged, because they all consume a
`NoticePackage` and never know where it came from.

Metadata resolution, in strict precedence order:

  1. An explicit `notice.yaml` / `notice.json` (in the folder or passed in).
  2. Deterministic extraction from the documents (regex, never an LLM — the
     same rule that governs the clause scanner: code finds literal facts).
  3. `[NEEDS INPUT]`. Nothing is invented. A missing response deadline is
     reported as missing, because a guessed deadline is worse than none.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import re
import shutil
from pathlib import Path
from typing import Optional

from ..models import AmendmentRecord, AttachmentRecord, NoticeMetadata, NoticePackage

NEEDS_INPUT = "[NEEDS INPUT]"
METADATA_FILENAMES = ("notice.yaml", "notice.yml", "notice.json")

# Document types the parsing ladder can actually read.
READABLE_SUFFIXES = {".pdf", ".docx", ".doc", ".xlsx", ".xls", ".txt", ".md",
                     ".rtf", ".htm", ".html", ".csv"}

# Fields a human may set in notice.yaml. Anything else is ignored rather than
# silently written onto the model.
ALLOWED_META_FIELDS = {
    "notice_id", "solicitation_number", "title", "agency", "notice_type_raw",
    "posted_date", "response_deadline", "naics_code", "psc_code", "set_aside",
    "place_of_performance", "description_text",
}


class LocalIntakeError(ValueError):
    """The folder cannot produce a usable package."""


def local_run_key(source: Path) -> str:
    """A stable 32-hex run key derived from the folder's *content*.

    Must be 32 hex to satisfy the same run-directory contract a real notice ID
    does. Content-derived rather than random so that re-running the same folder
    resumes the same run (FR-21) instead of starting a new one, and so that
    adding an amendment document produces a genuinely new run.
    """
    digest = hashlib.sha256()
    for path in _document_files(source):
        digest.update(path.name.encode("utf-8"))
        digest.update(_sha256(path).encode("ascii"))
    if not digest.hexdigest():  # pragma: no cover — sha256 always yields
        raise LocalIntakeError(f"No readable documents in {source}")
    return digest.hexdigest()[:32]


def load_local_package(
    source: Path,
    dest_dir: Path,
    meta_path: Optional[Path] = None,
) -> NoticePackage:
    """Build a NoticePackage from a directory of solicitation documents."""
    source = Path(source)
    if not source.is_dir():
        raise LocalIntakeError(f"{source} is not a directory")
    documents = _document_files(source)
    if not documents:
        raise LocalIntakeError(
            f"No readable solicitation documents in {source}. Expected at least "
            f"one of: {', '.join(sorted(READABLE_SUFFIXES))}"
        )

    notice_id = local_run_key(source)
    dest_dir.mkdir(parents=True, exist_ok=True)

    files: list[AttachmentRecord] = []
    for path in documents:
        target = dest_dir / path.name
        if not target.exists() or _sha256(target) != _sha256(path):
            shutil.copy2(path, target)
        files.append(AttachmentRecord(
            name=path.name,
            local_path=str(target),
            mime_type=mimetypes.guess_type(path.name)[0],
            sha256=_sha256(target),
            restricted=False,
            source_notice_id=notice_id,
        ))

    declared = _read_metadata_file(meta_path or _find_metadata_file(source))
    metadata = _build_metadata(notice_id, declared, files, source)

    return NoticePackage(
        metadata=metadata,
        files=files,
        amendment_history=[AmendmentRecord(
            notice_id=notice_id,
            posted_date=metadata.posted_date,
            title=metadata.title,
            is_latest=True,
        )],
        restricted_files_flagged=False,
    )


def missing_metadata(metadata: NoticeMetadata) -> list[str]:
    """Fields that stayed unresolved — surfaced as human actions, never guessed.

    Ordered by how much damage the gap does: a wrong deadline loses the bid
    outright, and NAICS drives the size standard the whole eligibility check
    rests on.
    """
    checks = [
        ("response_deadline", metadata.response_deadline,
         "the submission deadline drives every date in the schedule"),
        ("naics_code", metadata.naics_code,
         "NAICS selects the SBA size standard the eligibility check depends on"),
        ("solicitation_number", metadata.solicitation_number,
         "identifies the procurement on every document you submit"),
        ("set_aside", metadata.set_aside,
         "decides whether you are even eligible to bid"),
        ("agency", metadata.agency, "addressee for the submission"),
        ("title", metadata.title, "used in file naming and the cover letter"),
    ]
    return [
        f"Confirm {field} in notice.yaml — {why}"
        for field, value, why in checks
        if not value or value == NEEDS_INPUT
    ]


# -- metadata resolution ------------------------------------------------------


def _find_metadata_file(source: Path) -> Optional[Path]:
    for name in METADATA_FILENAMES:
        candidate = source / name
        if candidate.is_file():
            return candidate
    return None


def _read_metadata_file(path: Optional[Path]) -> dict:
    if path is None:
        return {}
    text = path.read_text(encoding="utf-8")
    try:
        if path.suffix == ".json":
            data = json.loads(text)
        else:
            import yaml
            data = yaml.safe_load(text)
    except Exception as exc:  # noqa: BLE001 — a malformed file must say so
        raise LocalIntakeError(f"Could not parse {path.name}: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise LocalIntakeError(f"{path.name} must contain a mapping of fields")
    unknown = set(data) - ALLOWED_META_FIELDS
    if unknown:
        raise LocalIntakeError(
            f"{path.name} has unrecognized field(s): {', '.join(sorted(unknown))}. "
            f"Allowed: {', '.join(sorted(ALLOWED_META_FIELDS))}"
        )
    return {k: v for k, v in data.items() if v not in (None, "")}


# Deterministic extraction. Each pattern targets a label that solicitations
# print literally; none of them guess. A miss leaves the field unset.
# The label is matched case-insensitively, but the VALUE is not: with
# IGNORECASE applied to the capture group, "SOLICITATION\nSolicitation Number:"
# happily captures the word "Solicitation" itself. The scoped (?-i:) and the
# digit lookahead together require something that actually looks like an
# identifier, and the label word is mandatory rather than optional.
_SOL_NUMBER_RE = re.compile(
    r"(?:solicitation|RF[PQI]|IFB)\s*(?:number|no\.?|#)\s*[:\-]?\s*"
    r"((?-i:(?=[A-Z0-9\-]*\d)[A-Z0-9][A-Z0-9\-]{4,}))",
    re.IGNORECASE,
)
_NAICS_RE = re.compile(r"NAICS\s*(?:code)?\s*[:\-]?\s*(\d{6})", re.IGNORECASE)
_PSC_RE = re.compile(r"\b(?:PSC|product service code)\s*[:\-]?\s*([A-Z0-9]{4})\b", re.IGNORECASE)
# Captures the time and zone when they are present. A federal deadline is a
# timestamp, not a date — "2:00 PM ET" is the difference between on time and
# non-responsive, and dropping it would quietly manufacture a full extra day.
_DEADLINE_RE = re.compile(
    r"(?:offers?\s+due|responses?\s+due|response date|proposal(?:s)?\s+due|"
    r"closing date|due date|submission deadline)\s*[:\-]?\s*"
    r"((?:[A-Z][a-z]+ \d{1,2},? \d{4}|\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{2}-\d{2})"
    r"(?:\s*(?:at|,)?\s*\d{1,2}:\d{2}\s*(?:[AaPp]\.?[Mm]\.?)?"
    r"(?:\s*[A-Z]{2,4})?)?)",
    re.IGNORECASE,
)
_SET_ASIDE_RE = re.compile(
    r"\b(total small business set[- ]aside|small business set[- ]aside|"
    r"8\(a\) set[- ]aside|HUBZone set[- ]aside|SDVOSB set[- ]aside|"
    r"service[- ]disabled veteran[- ]owned small business set[- ]aside|"
    r"WOSB set[- ]aside|EDWOSB set[- ]aside|full and open competition)\b",
    re.IGNORECASE,
)


def _build_metadata(
    notice_id: str,
    declared: dict,
    files: list[AttachmentRecord],
    source: Path,
) -> NoticeMetadata:
    # Sample text for extraction. Only cheap-to-read formats: the full parsing
    # ladder (OCR, PDF tables) runs later in docproc and would be wasteful here.
    sample = _plaintext_sample(files)

    def resolve(field: str, extractor=None) -> Optional[str]:
        if declared.get(field):
            return str(declared[field])
        if extractor and sample:
            found = extractor(sample)
            if found:
                return found
        return None

    metadata = NoticeMetadata(
        notice_id=declared.get("notice_id") or notice_id,
        solicitation_number=resolve("solicitation_number", _first(_SOL_NUMBER_RE)),
        title=declared.get("title") or _title_from_folder(source),
        agency=declared.get("agency"),
        notice_type_raw=declared.get("notice_type_raw") or "Local document package",
        posted_date=declared.get("posted_date"),
        response_deadline=resolve("response_deadline", _first(_DEADLINE_RE)),
        naics_code=resolve("naics_code", _first(_NAICS_RE)),
        psc_code=resolve("psc_code", _first(_PSC_RE)),
        set_aside=resolve("set_aside", _first(_SET_ASIDE_RE)),
        place_of_performance=declared.get("place_of_performance"),
        description_text=declared.get("description_text") or "",
        raw_api_record={
            "source": "local",
            "source_dir": str(source),
            "document_count": len(files),
            "metadata_declared": sorted(declared),
        },
    )
    return metadata


def _first(pattern: re.Pattern):
    def extract(text: str) -> Optional[str]:
        match = pattern.search(text)
        return match.group(1).strip() if match else None
    return extract


def _plaintext_sample(files: list[AttachmentRecord], limit: int = 200_000) -> str:
    """Text from formats readable without the heavy parsing ladder.

    PDFs and Office documents are deliberately skipped: docproc parses them
    properly a moment later, and a half-parsed sample here risks extracting a
    field from garbled text. If nothing is directly readable, extraction simply
    finds nothing and the fields stay unset — which is the honest outcome.
    """
    chunks: list[str] = []
    total = 0
    for record in files:
        path = Path(record.local_path)
        if path.suffix.lower() not in {".txt", ".md", ".csv", ".htm", ".html"}:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        chunks.append(text[: limit - total])
        total += len(chunks[-1])
        if total >= limit:
            break
    return "\n".join(chunks)


def _title_from_folder(source: Path) -> str:
    cleaned = re.sub(r"[_\-]+", " ", source.name).strip()
    return cleaned or source.name


def _document_files(source: Path) -> list[Path]:
    return sorted(
        p for p in source.rglob("*")
        if p.is_file()
        and p.suffix.lower() in READABLE_SUFFIXES
        and p.name not in METADATA_FILENAMES
        and not p.name.startswith(".")
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()
