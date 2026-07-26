"""SAM.gov API clients (PRD §11 API specifics).

- Get Opportunities API v2: notice metadata + amendment-chain search by
  solicitation number. Requires a free api.data.gov key (SAM_GOV_API_KEY).
- Public opportunity resources endpoints for attachment listing/download.
- Entity Management API: verify the company's own registration/exclusions.

Daily rate limits are modest for non-federal accounts, so responses are
cached on disk and unchanged attachments are never re-fetched (keyed by
sha256 where the API provides one, else by resource id).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Optional

import httpx

from ..models import AmendmentRecord, AttachmentRecord, NoticeMetadata

SEARCH_API = "https://api.sam.gov/opportunities/v2/search"
RESOURCES_API = "https://sam.gov/api/prod/opps/v3/opportunities/{notice_id}/resources"
DOWNLOAD_API = "https://sam.gov/api/prod/opps/v3/opportunities/resources/files/{resource_id}/download"
ENTITY_API = "https://api.sam.gov/entity-information/v3/entities"

_NOTICE_ID_RE = re.compile(r"^[0-9a-f]{32}$", re.IGNORECASE)


def parse_notice_id(url_or_id: str) -> str:
    """Extract the 32-hex notice ID from a SAM.gov URL or a bare ID."""
    candidate = url_or_id.strip()
    if _NOTICE_ID_RE.match(candidate):
        return candidate.lower()
    m = re.search(r"/opp/([0-9a-f]{32})", candidate, re.IGNORECASE)
    if m:
        return m.group(1).lower()
    raise ValueError(
        f"Could not extract a SAM.gov notice ID from {url_or_id!r}. "
        "Expected https://sam.gov/opp/<32-hex-id>/view or a bare notice ID."
    )


class SamGovClient:
    def __init__(self, api_key: Optional[str] = None, cache_dir: Optional[Path] = None, timeout: float = 60.0):
        self.api_key = api_key or os.environ.get("SAM_GOV_API_KEY")
        self.cache_dir = cache_dir
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._http = httpx.Client(timeout=timeout, follow_redirects=True)

    # -- notice metadata -------------------------------------------------------

    def get_notice(self, notice_id: str) -> Optional[dict]:
        return self._search_one({"noticeid": notice_id})

    def search_by_solicitation_number(self, solnum: str) -> list[dict]:
        """Fetch ALL notices under a solicitation number — the amendment chain."""
        data = self._search({"solnum": solnum, "limit": 100})
        return data.get("opportunitiesData") or []

    def notice_metadata(self, notice_id: str) -> NoticeMetadata:
        record = self.get_notice(notice_id)
        meta = NoticeMetadata(notice_id=notice_id)
        if record:
            meta.solicitation_number = record.get("solicitationNumber")
            meta.title = record.get("title")
            meta.agency = record.get("fullParentPathName") or record.get("organizationName")
            meta.notice_type_raw = record.get("type")
            meta.posted_date = record.get("postedDate")
            meta.response_deadline = record.get("responseDeadLine")
            meta.naics_code = record.get("naicsCode")
            meta.psc_code = record.get("classificationCode")
            meta.set_aside = record.get("typeOfSetAsideDescription") or record.get("typeOfSetAside")
            pop = record.get("placeOfPerformance") or {}
            if isinstance(pop, dict):
                city = (pop.get("city") or {}).get("name") if isinstance(pop.get("city"), dict) else pop.get("city")
                state = (pop.get("state") or {}).get("code") if isinstance(pop.get("state"), dict) else pop.get("state")
                meta.place_of_performance = ", ".join(x for x in (city, state) if x) or None
            for poc in record.get("pointOfContact") or []:
                bits = [poc.get("fullName"), poc.get("email"), poc.get("phone")]
                meta.points_of_contact.append(" / ".join(b for b in bits if b))
            meta.raw_api_record = record
            meta.description_text = self._fetch_description(record)
        return meta

    # -- attachments -----------------------------------------------------------

    def list_resources(self, notice_id: str) -> list[dict]:
        try:
            resp = self._http.get(RESOURCES_API.format(notice_id=notice_id))
            resp.raise_for_status()
            payload = resp.json()
        except Exception:
            return []
        embedded = payload.get("_embedded") or {}
        lists = embedded.get("opportunityAttachmentList") or []
        resources: list[dict] = []
        for entry in lists:
            resources.extend(entry.get("attachments") or [])
        if not resources and isinstance(payload.get("attachments"), list):
            resources = payload["attachments"]
        return resources

    def download_attachments(self, notice_id: str, dest_dir: Path) -> list[AttachmentRecord]:
        dest_dir.mkdir(parents=True, exist_ok=True)
        records: list[AttachmentRecord] = []
        for resource in self.list_resources(notice_id):
            resource_id = resource.get("resourceId") or resource.get("attachmentId")
            name = resource.get("name") or resource.get("fileName") or resource_id
            if not resource_id or not name:
                continue
            safe_name = os.path.basename(str(name))
            local_path = dest_dir / safe_name
            restricted = bool(resource.get("restricted") or resource.get("accessLevel") == "restricted")
            if restricted:
                records.append(
                    AttachmentRecord(
                        name=safe_name, local_path=str(local_path),
                        restricted=True, source_notice_id=notice_id,
                    )
                )
                continue
            if not local_path.exists():  # never re-fetch unchanged attachments
                try:
                    dl = self._http.get(DOWNLOAD_API.format(resource_id=resource_id))
                    dl.raise_for_status()
                    local_path.write_bytes(dl.content)
                except Exception:
                    records.append(
                        AttachmentRecord(
                            name=safe_name, local_path=str(local_path),
                            restricted=True, source_notice_id=notice_id,
                        )
                    )
                    continue
            records.append(
                AttachmentRecord(
                    name=safe_name,
                    local_path=str(local_path),
                    mime_type=resource.get("mimeType"),
                    sha256=hashlib.sha256(local_path.read_bytes()).hexdigest(),
                    source_notice_id=notice_id,
                )
            )
        return records

    # -- entity management (FR-6) ---------------------------------------------

    def entity_status(self, uei: str) -> Optional[dict]:
        """Registration + exclusion status for the company's own UEI."""
        if not self.api_key:
            return None
        try:
            resp = self._http.get(ENTITY_API, params={"api_key": self.api_key, "ueiSAM": uei})
            resp.raise_for_status()
            data = resp.json()
            entities = data.get("entityData") or []
            if not entities:
                return {"found": False}
            reg = (entities[0].get("entityRegistration") or {})
            return {
                "found": True,
                "registration_status": reg.get("registrationStatus"),
                "exclusion_status": reg.get("exclusionStatusFlag"),
                "expiration_date": reg.get("registrationExpirationDate"),
            }
        except Exception:
            return None

    # -- internals -------------------------------------------------------------

    def _search_one(self, extra_params: dict) -> Optional[dict]:
        data = self._search({**extra_params, "limit": 1})
        opportunities = data.get("opportunitiesData") or []
        return opportunities[0] if opportunities else None

    def _search(self, extra_params: dict) -> dict:
        if not self.api_key:
            return {}
        params = {
            "api_key": self.api_key,
            "postedFrom": "01/01/2015",
            "postedTo": "12/31/2099",
            **extra_params,
        }
        cache_key = hashlib.sha256(
            json.dumps({k: v for k, v in params.items() if k != "api_key"}, sort_keys=True).encode()
        ).hexdigest()
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached
        resp = self._http.get(SEARCH_API, params=params)
        resp.raise_for_status()
        data = resp.json()
        self._cache_put(cache_key, data)
        return data

    def _fetch_description(self, record: dict) -> str:
        desc = record.get("description")
        if not desc:
            return ""
        if isinstance(desc, str) and desc.startswith("http"):
            try:
                url = desc
                if self.api_key and "api_key" not in url:
                    url += ("&" if "?" in url else "?") + f"api_key={self.api_key}"
                resp = self._http.get(url)
                resp.raise_for_status()
                body = resp.json()
                text = body.get("description") if isinstance(body, dict) else None
                return strip_html(text or "")
            except Exception:
                return ""
        return strip_html(str(desc))

    def _cache_get(self, key: str) -> Optional[dict]:
        if not self.cache_dir:
            return None
        path = self.cache_dir / f"{key}.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        return None

    def _cache_put(self, key: str, data: dict) -> None:
        if self.cache_dir:
            (self.cache_dir / f"{key}.json").write_text(json.dumps(data), encoding="utf-8")

    def close(self) -> None:
        self._http.close()


def strip_html(text: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p\s*>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()
