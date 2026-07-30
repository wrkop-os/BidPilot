"""Opportunity sources behind one interface (discovery breadth, C5).

SAM.gov is the primary federal notice feed, but small contractors also bid
grants and DIBBS/SLED work — Sweetspot's breadth is the loudest checkbox in
the market (docs/COMPETITIVE_BENCHMARK.md D1). Each source normalizes into
the same record shape the deterministic pre-screen already consumes, so
adding a source never touches `discover.prescreen`.

Contract: `search(naics_codes, days_back, limit)` -> list of dicts using
SAM.gov's field names (noticeId, title, naicsCode, responseDeadLine, ...),
plus a `source` key. Unknown/missing fields stay absent rather than guessed.
"""

from __future__ import annotations

import datetime as _dt
from typing import Optional, Protocol

import httpx

GRANTS_SEARCH_URL = "https://api.grants.gov/v1/api/search2"


class OpportunitySource(Protocol):
    name: str

    def search(self, naics_codes: list[str], days_back: int, limit: int) -> list[dict]:
        ...


class SamGovSource:
    """Adapter over the existing SAM client (unchanged behavior)."""

    name = "sam.gov"

    def __init__(self, client):
        self.client = client

    def search(self, naics_codes: list[str], days_back: int, limit: int) -> list[dict]:
        today = _dt.date.today()
        posted_from = (today - _dt.timedelta(days=days_back)).strftime("%m/%d/%Y")
        posted_to = today.strftime("%m/%d/%Y")
        out: list[dict] = []
        for naics in naics_codes or []:
            data = self.client.search_raw({
                "ncode": naics,
                "postedFrom": posted_from,
                "postedTo": posted_to,
                "limit": limit,
                "ptype": "o,k,p,r",
            })
            for record in data.get("opportunitiesData") or []:
                out.append({**record, "source": self.name})
        return out


class GrantsGovSource:
    """Grants.gov Search2 — public JSON API, no key required.

    Grants have no NAICS, so relevance comes from the profile's capability
    keywords; every record is marked `grant: True` so the pre-screen can
    treat set-aside/size logic as not-applicable rather than silently pass.
    """

    name = "grants.gov"

    def __init__(self, keywords: Optional[list[str]] = None,
                 transport: Optional[httpx.BaseTransport] = None,
                 timeout: float = 30.0):
        self.keywords = keywords or []
        self._http = httpx.Client(timeout=timeout, transport=transport)

    def search(self, naics_codes: list[str], days_back: int, limit: int) -> list[dict]:
        records: list[dict] = []
        for keyword in (self.keywords or [None]):
            payload = {"rows": limit, "oppStatuses": "posted"}
            if keyword:
                payload["keyword"] = keyword
            try:
                resp = self._http.post(GRANTS_SEARCH_URL, json=payload)
                resp.raise_for_status()
                data = resp.json()
            except Exception:
                continue  # a dead source must never break the sweep
            hits = ((data.get("data") or {}).get("oppHits")) or []
            for hit in hits:
                records.append(self._normalize(hit))
        return records

    @staticmethod
    def _normalize(hit: dict) -> dict:
        return {
            "noticeId": (hit.get("id") or hit.get("number") or ""),
            "title": hit.get("title"),
            "fullParentPathName": hit.get("agency") or hit.get("agencyName"),
            "naicsCode": None,              # grants carry no NAICS
            "typeOfSetAsideDescription": None,
            "postedDate": hit.get("openDate"),
            "responseDeadLine": hit.get("closeDate"),
            "type": "Grant",
            "source": GrantsGovSource.name,
            "grant": True,
            "url": (
                f"https://www.grants.gov/search-results-detail/{hit.get('id')}"
                if hit.get("id") else ""
            ),
        }


def search_all(sources: list, naics_codes: list[str], days_back: int,
               limit: int) -> list[dict]:
    """Sweep every source; one failing source never sinks the others."""
    records: list[dict] = []
    for source in sources:
        try:
            records.extend(source.search(naics_codes, days_back, limit))
        except Exception:
            continue
    return records
