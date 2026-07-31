"""Proactive opportunity discovery (PRD Phase 5.1): poll the Opportunities
API for recent notices matching the company's NAICS codes, pre-screen
eligibility deterministically (set-aside vs certifications, size standard,
future deadline), and rank. Built on Phase-1 components — turns the tool
from reactive into top-of-funnel."""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Optional

from .data import sba_size_standards
from .intake.samgov import SamGovClient
from .kb.schema import CompanyProfile

# Set-aside description fragments -> certification the profile must hold.
SET_ASIDE_CERT_MAP = {
    "8(a)": "8(a)",
    "hubzone": "HUBZone",
    "service-disabled": "SDVOSB",
    "sdvosb": "SDVOSB",
    "women-owned": "WOSB",
    "wosb": "WOSB",
    "edwosb": "EDWOSB",
    "economically disadvantaged women": "EDWOSB",
}


@dataclass
class ScreenedOpportunity:
    notice_id: str
    title: str
    agency: Optional[str]
    naics: Optional[str]
    set_aside: Optional[str]
    posted: Optional[str]
    deadline: Optional[str]
    notice_type: Optional[str]
    screen: str = "candidate"          # candidate | blocked | review
    reasons: list[str] = field(default_factory=list)
    url: str = ""
    pwin: Optional[float] = None       # heuristic advisory, ranks within a bucket
    source: str = "sam.gov"


def prescreen(record: dict, profile: CompanyProfile, today: Optional[_dt.date] = None) -> ScreenedOpportunity:
    """Deterministic eligibility pre-screen — no LLM. Conservative: 'blocked'
    only for unambiguous mismatches; unknowns become 'review'."""
    today = today or _dt.date.today()
    notice_id = (record.get("noticeId") or "").lower()
    opp = ScreenedOpportunity(
        notice_id=notice_id,
        title=record.get("title") or "(untitled)",
        agency=record.get("fullParentPathName"),
        naics=record.get("naicsCode"),
        set_aside=record.get("typeOfSetAsideDescription") or record.get("typeOfSetAside"),
        posted=record.get("postedDate"),
        deadline=record.get("responseDeadLine"),
        notice_type=record.get("type"),
        url=f"https://sam.gov/opp/{notice_id}/view" if notice_id else "",
    )

    opp.source = record.get("source") or "sam.gov"
    if record.get("url"):
        opp.url = record["url"]

    # 1. Deadline already passed -> blocked.
    deadline_date = _parse_date(opp.deadline)
    if deadline_date and deadline_date < today:
        opp.screen = "blocked"
        opp.reasons.append(f"deadline passed ({opp.deadline})")
        opp.pwin = 0.0
        return opp

    # Grants carry no NAICS or set-aside: the socioeconomic/size screens do
    # not apply, so say so rather than implying they passed.
    if record.get("grant"):
        opp.screen = "review"
        opp.reasons.append(
            "grant opportunity — set-aside and size-standard screens do not "
            "apply; eligibility is per the funding announcement"
        )
        opp.pwin = _quick_pwin(opp, profile)
        return opp

    # 2. Set-aside vs certifications.
    set_aside = (opp.set_aside or "").lower()
    if set_aside and "small business" not in set_aside:
        required = None
        for fragment, cert in SET_ASIDE_CERT_MAP.items():
            if fragment in set_aside:
                required = cert
                break
        if required:
            held = {c.upper() for c in profile.socioeconomic_certifications}
            if required.upper() not in held:
                opp.screen = "blocked"
                opp.reasons.append(f"set-aside requires {required}; not held")
                opp.pwin = 0.0
                return opp
            opp.reasons.append(f"set-aside matches held certification ({required})")

    # 3. Size standard under the notice's NAICS.
    if opp.naics:
        small = sba_size_standards.is_small(
            opp.naics, profile.annual_receipts_avg, profile.employee_count
        )
        if small is False and set_aside:  # any set-aside requires being small
            opp.screen = "blocked"
            opp.reasons.append(f"other-than-small under NAICS {opp.naics} on a set-aside notice")
            opp.pwin = 0.0
            return opp
        if small is None:
            opp.screen = "review"
            opp.reasons.append(f"size standard for NAICS {opp.naics} undetermined — human check")
        else:
            opp.reasons.append(f"small under NAICS {opp.naics}")

    # 4. NAICS in the company's registered codes? (soft signal)
    if opp.naics and profile.naics_codes and opp.naics not in profile.naics_codes:
        if opp.screen == "candidate":
            opp.screen = "review"
        opp.reasons.append(f"NAICS {opp.naics} not in the company's registered codes")

    opp.pwin = _quick_pwin(opp, profile)
    return opp


def _quick_pwin(opp: ScreenedOpportunity, profile: CompanyProfile) -> float:
    """Heuristic P(win) for top-of-funnel ranking — pre-eligibility, so it
    only sees notice metadata + profile. Advisory; blocked notices score 0."""
    from .ml.pwin import BASELINE_PWIN

    if opp.screen == "blocked":
        return 0.0
    p = BASELINE_PWIN
    set_aside = (opp.set_aside or "").lower()
    if set_aside and "small business" not in set_aside:
        p += 0.06  # a set-aside we survived screening for thins the field
    if opp.naics and opp.naics in profile.naics_codes:
        p += 0.03
    if opp.screen == "review":
        p -= 0.05  # unresolved screening questions
    return round(min(0.65, max(0.02, p)), 3)


def discover(
    sam: SamGovClient,
    profile: CompanyProfile,
    days_back: int = 7,
    limit_per_naics: int = 25,
    sources: Optional[list] = None,
) -> tuple[list[ScreenedOpportunity], list[tuple[str, str]]]:
    """Search recent opportunities across every configured source, pre-screen,
    and rank. `sources` defaults to SAM.gov alone; pass extras (grants, SLED)
    to widen the funnel without touching the screening logic.

    Returns (results, source_failures). Callers MUST surface the failures:
    an empty result list from a sweep that never reached its sources reads as
    'nothing to bid on this week', which is the most damaging wrong answer
    this command can give."""
    from .intake.sources import SamGovSource, search_all

    today = _dt.date.today()
    seen: set[str] = set()
    results: list[ScreenedOpportunity] = []
    feeds = sources if sources is not None else [SamGovSource(sam)]
    records, failures = search_all(feeds, profile.naics_codes or [], days_back, limit_per_naics)
    for record in records:
        notice_id = (record.get("noticeId") or "").lower()
        if not notice_id or notice_id in seen:
            continue
        seen.add(notice_id)
        results.append(prescreen(record, profile, today))
    order = {"candidate": 0, "review": 1, "blocked": 2}
    results.sort(
        key=lambda o: (order.get(o.screen, 3), -(o.pwin or 0.0), o.deadline or "9999")
    )
    return results, failures


def _parse_date(text: Optional[str]) -> Optional[_dt.date]:
    if not text:
        return None
    try:
        return _dt.date.fromisoformat(text[:10])
    except ValueError:
        return None
