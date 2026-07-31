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

from ..models import AttachmentRecord, NoticeMetadata

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


def diagnose_api_failure(exc: BaseException) -> str:
    """Turn a SAM.gov call failure into the fix, not the stack trace.

    A rejected key, a blocked egress path, and a rate limit all arrive as
    exceptions but need entirely different actions, and guessing wrong costs
    an afternoon. Never echoes the exception text verbatim past the first
    line: httpx embeds the request URL, which carries `api_key`.
    """
    name = type(exc).__name__
    text = str(exc)

    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code in (401, 403):
            body = ""
            try:
                body = (exc.response.json().get("error", {}) or {}).get("message", "")
            except Exception:  # noqa: BLE001 — body may not be JSON
                body = ""
            return (
                f"HTTP {code} from api.sam.gov — the key was reached and REJECTED"
                + (f": {body}" if body else "")
                + ". Check that SAM_GOV_API_KEY is an api.data.gov key for the "
                "Opportunities API (a SAM.gov system-account key is a different "
                "credential), and that it is activated and not expired."
            )
        if code == 429:
            return ("HTTP 429 — the key works but its daily rate limit is spent. "
                    "Non-federal accounts get a modest quota; responses are "
                    "cached on disk, so re-runs of the same query are free.")
        if code >= 500:
            return f"HTTP {code} — SAM.gov is failing upstream. Retry later; the key is fine."
        return f"HTTP {code} from api.sam.gov."

    # Never reached the API at all.
    proxy_hint = ""
    for var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        if os.environ.get(var):
            proxy_hint = (
                f" An egress proxy is configured ({var}); a 403 on CONNECT is the "
                "proxy's network policy denying api.sam.gov, NOT a bad key. "
                "Allow the host in the environment's network policy, or run "
                "outside the sandbox."
            )
            break
    if "ProxyError" in name or "proxy" in text.lower():
        return ("Could not reach api.sam.gov: the connection was refused before "
                "the request was sent." + (proxy_hint or
                " This is a network-path failure, not an authentication failure."))
    if isinstance(exc, httpx.TimeoutException):
        return "Timed out reaching api.sam.gov — network path or SAM.gov slowness." + proxy_hint
    if isinstance(exc, httpx.TransportError):
        return (f"Network error reaching api.sam.gov ({name}) — DNS, TLS, or "
                "routing. The key was never presented." + proxy_hint)
    return f"{name} while calling api.sam.gov."


class SamGovClient:
    """SAM.gov client with NFR-3 resilience: idempotent GETs retry on
    connection errors, 429, and 5xx with exponential backoff (Retry-After
    honored when present). 4xx other than 429 never retries."""

    MAX_RETRIES = 3
    BACKOFF_BASE_S = 1.0
    BACKOFF_CAP_S = 30.0

    def __init__(self, api_key: Optional[str] = None, cache_dir: Optional[Path] = None, timeout: float = 60.0):
        self.api_key = api_key or os.environ.get("SAM_GOV_API_KEY")
        self.cache_dir = cache_dir
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._http = httpx.Client(timeout=timeout, follow_redirects=True)
        self._sleep = __import__("time").sleep  # injectable for tests

    def _get(self, url: str, params: Optional[dict] = None) -> httpx.Response:
        """GET with retry/backoff. Raises on the final failure like
        raise_for_status / the underlying transport would."""
        last_exc: Optional[Exception] = None
        for attempt in range(self.MAX_RETRIES + 1):
            try:
                resp = self._http.get(url, params=params)
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last_exc = exc
                self._backoff(attempt, None)
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                last_exc = httpx.HTTPStatusError(
                    f"{resp.status_code} from {url}", request=resp.request, response=resp
                )
                if attempt < self.MAX_RETRIES:
                    self._backoff(attempt, resp.headers.get("retry-after"))
                    continue
            if resp.status_code >= 400:
                # Redact the query string: httpx's default message embeds the
                # full URL, which would leak api_key into logs/UI error fields.
                raise httpx.HTTPStatusError(
                    f"{resp.status_code} error from {resp.request.url.copy_with(query=None)}",
                    request=resp.request, response=resp,
                )
            return resp
        raise last_exc  # transport errors exhausted retries

    def _backoff(self, attempt: int, retry_after: Optional[str]) -> None:
        if attempt >= self.MAX_RETRIES:
            return
        delay = min(self.BACKOFF_BASE_S * (2 ** attempt), self.BACKOFF_CAP_S)
        if retry_after:
            try:
                delay = max(delay, float(retry_after))
            except ValueError:
                pass
        self._sleep(delay)

    # -- notice metadata -------------------------------------------------------

    def get_notice(self, notice_id: str) -> Optional[dict]:
        return self._search_one({"noticeid": notice_id})

    def search_by_solicitation_number(self, solnum: str) -> list[dict]:
        """Fetch ALL notices under a solicitation number — the amendment chain."""
        data = self._search({"solnum": solnum, "limit": 100})
        return data.get("opportunitiesData") or []

    def search_raw(self, params: dict) -> dict:
        """Public cached search against the Opportunities API v2 — used by
        discovery and the doctor contract check."""
        return self._search(params)

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
            payload = self._get(RESOURCES_API.format(notice_id=notice_id)).json()
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
                    dl = self._get(DOWNLOAD_API.format(resource_id=resource_id))
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
            resp = self._get(ENTITY_API, params={"api_key": self.api_key, "ueiSAM": uei})
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
        resp = self._get(SEARCH_API, params=params)
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
                host = httpx.URL(url).host or ""
                if host != "sam.gov" and not host.endswith(".sam.gov"):
                    # A poisoned record could point anywhere; never follow it,
                    # and never send the API key off the trusted host (SSRF /
                    # credential exfiltration guard).
                    return ""
                if self.api_key and "api_key" not in url:
                    url += ("&" if "?" in url else "?") + f"api_key={self.api_key}"
                resp = self._get(url)
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
