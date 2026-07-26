"""SAM.gov client: opportunity URL -> notice data + attachments.

SAM.gov is where opportunities are POSTED. Retrieval uses two surfaces:

* The public Get Opportunities API (``api.sam.gov/opportunities/v2/search``),
  which requires a free api.data.gov API key (``SAM_GOV_API_KEY`` env var).
* The un-keyed opportunity resources endpoints used by the sam.gov website
  itself, for listing and downloading attachments.

Nothing here submits anything anywhere — read-only ingestion.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

import httpx

from .models import AttachmentInfo, RawOpportunity

SEARCH_API = "https://api.sam.gov/opportunities/v2/search"
RESOURCES_API = "https://sam.gov/api/prod/opps/v3/opportunities/{notice_id}/resources"
DOWNLOAD_API = "https://sam.gov/api/prod/opps/v3/opportunities/resources/files/{resource_id}/download"

_NOTICE_ID_RE = re.compile(r"^[0-9a-f]{32}$", re.IGNORECASE)


def parse_notice_id(url_or_id: str) -> str:
    """Extract the 32-hex notice ID from a SAM.gov opportunity URL or bare ID.

    Accepts:
      https://sam.gov/opp/<id>/view
      https://sam.gov/workspace/contract/opp/<id>/view
      <bare 32-char hex id>
    """
    candidate = url_or_id.strip()
    if _NOTICE_ID_RE.match(candidate):
        return candidate.lower()
    m = re.search(r"/opp/([0-9a-f]{32})", candidate, re.IGNORECASE)
    if m:
        return m.group(1).lower()
    raise ValueError(
        f"Could not extract a SAM.gov notice ID from {url_or_id!r}. "
        "Expected a URL like https://sam.gov/opp/<32-hex-id>/view or a bare notice ID."
    )


class SamGovClient:
    def __init__(self, api_key: Optional[str] = None, timeout: float = 60.0):
        self.api_key = api_key or os.environ.get("SAM_GOV_API_KEY")
        self._http = httpx.Client(timeout=timeout, follow_redirects=True)

    # -- opportunity metadata -------------------------------------------------

    def fetch_opportunity(self, notice_id: str) -> RawOpportunity:
        record = self._fetch_api_record(notice_id)
        opp = RawOpportunity(notice_id=notice_id)
        if record:
            opp.solicitation_number = record.get("solicitationNumber")
            opp.title = record.get("title")
            org = record.get("fullParentPathName") or record.get("organizationName")
            opp.agency = org
            opp.notice_type = record.get("type")
            opp.posted_date = record.get("postedDate")
            opp.response_deadline = record.get("responseDeadLine")
            opp.naics_code = record.get("naicsCode")
            opp.set_aside = record.get("typeOfSetAsideDescription") or record.get("typeOfSetAside")
            opp.raw_api_record = record
            opp.description_text = self._fetch_description(record)
        return opp

    def _fetch_api_record(self, notice_id: str) -> Optional[dict]:
        if not self.api_key:
            return None
        # The v2 search API is the documented way to look up a single notice.
        # postedFrom/postedTo are mandatory params; use a wide window.
        params = {
            "api_key": self.api_key,
            "noticeid": notice_id,
            "postedFrom": "01/01/2015",
            "postedTo": "12/31/2099",
            "limit": 1,
        }
        resp = self._http.get(SEARCH_API, params=params)
        resp.raise_for_status()
        data = resp.json()
        opportunities = data.get("opportunitiesData") or []
        return opportunities[0] if opportunities else None

    def _fetch_description(self, record: dict) -> str:
        desc = record.get("description")
        if not desc:
            return ""
        # `description` is a URL to the full HTML description in v2 responses.
        if isinstance(desc, str) and desc.startswith("http"):
            try:
                url = desc
                if self.api_key and "api_key" not in url:
                    sep = "&" if "?" in url else "?"
                    url = f"{url}{sep}api_key={self.api_key}"
                resp = self._http.get(url)
                resp.raise_for_status()
                body = resp.json()
                text = body.get("description") if isinstance(body, dict) else None
                return _strip_html(text or "")
            except Exception:
                return ""
        return _strip_html(str(desc))

    # -- attachments ----------------------------------------------------------

    def download_attachments(self, notice_id: str, dest_dir: Path) -> list[AttachmentInfo]:
        dest_dir.mkdir(parents=True, exist_ok=True)
        attachments: list[AttachmentInfo] = []
        try:
            resp = self._http.get(RESOURCES_API.format(notice_id=notice_id))
            resp.raise_for_status()
            payload = resp.json()
        except Exception:
            return attachments

        for resource in _iter_resources(payload):
            resource_id = resource.get("resourceId") or resource.get("attachmentId")
            name = resource.get("name") or resource.get("fileName") or resource_id
            if not resource_id or not name:
                continue
            safe_name = os.path.basename(str(name))
            local_path = dest_dir / safe_name
            try:
                dl = self._http.get(DOWNLOAD_API.format(resource_id=resource_id))
                dl.raise_for_status()
                local_path.write_bytes(dl.content)
            except Exception:
                continue
            attachments.append(
                AttachmentInfo(
                    name=safe_name,
                    local_path=str(local_path),
                    mime_type=resource.get("mimeType"),
                )
            )
        return attachments

    def close(self) -> None:
        self._http.close()


def _iter_resources(payload: dict) -> list[dict]:
    """Normalize the attachments listing payload into a flat resource list."""
    embedded = payload.get("_embedded") or {}
    lists = embedded.get("opportunityAttachmentList") or []
    resources: list[dict] = []
    for entry in lists:
        resources.extend(entry.get("attachments") or [])
    # Some payload shapes put resources at the top level.
    if not resources and isinstance(payload.get("attachments"), list):
        resources = payload["attachments"]
    return resources


def _strip_html(text: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p\s*>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()
