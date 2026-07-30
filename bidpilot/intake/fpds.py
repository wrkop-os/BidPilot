"""FPDS award-history collector (price-to-win data path, docs/ML_ADOPTION.md).

FPDS-NG's public ATOM feed needs no API key. This client pulls historical
awards by NAICS (optionally agency-filtered) so pricing can be positioned
against what the government actually paid — deterministic analysis, never
an input to the rate engine (invariant 3: code computes).
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Optional
from xml.etree import ElementTree

import httpx
from pydantic import BaseModel

FPDS_ATOM = "https://www.fpds.gov/ezsearch/FEEDS/ATOM"


class AwardRecord(BaseModel):
    piid: Optional[str] = None
    vendor: Optional[str] = None
    agency: Optional[str] = None
    naics: Optional[str] = None
    signed_date: Optional[str] = None
    obligated: Optional[float] = None
    base_and_options: Optional[float] = None

    @property
    def value(self) -> Optional[float]:
        return self.base_and_options or self.obligated


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _attr(el: ElementTree.Element, name: str) -> Optional[str]:
    """Attribute lookup by local name (FPDS namespaces its attributes)."""
    for key, value in el.attrib.items():
        if _local(key) == name:
            return value
    return None


def parse_atom(xml_text: str) -> list[AwardRecord]:
    """Namespace-tolerant ATOM parse — FPDS versions its schemas, so match
    on local names only."""
    if "<!DOCTYPE" in xml_text[:4096] or "<!ENTITY" in xml_text[:4096]:
        # stdlib ElementTree expands internal DTD entities (billion-laughs
        # DoS). Legitimate FPDS ATOM never carries a DTD — reject outright.
        raise ValueError("Refusing to parse XML containing a DTD/entity declaration")
    root = ElementTree.fromstring(xml_text)
    records: list[AwardRecord] = []
    for entry in (e for e in root.iter() if _local(e.tag) == "entry"):
        rec = AwardRecord()
        for el in entry.iter():
            name = _local(el.tag)
            text = (el.text or "").strip()
            if name == "PIID" and text and not rec.piid:
                rec.piid = text
            elif name == "vendorName" and text and not rec.vendor:
                rec.vendor = text
            elif name == "principalNAICSCode" and not rec.naics:
                rec.naics = text or _attr(el, "description")
            elif name == "signedDate" and text and not rec.signed_date:
                rec.signed_date = text
            elif name == "obligatedAmount" and text:
                rec.obligated = _to_float(text) or rec.obligated
            elif name in ("baseAndAllOptionsValue", "totalBaseAndAllOptionsValue") and text:
                rec.base_and_options = _to_float(text) or rec.base_and_options
            elif name == "contractingOfficeAgencyID" and not rec.agency:
                rec.agency = _attr(el, "name") or text or None
        if rec.value:
            records.append(rec)
    return records


def _to_float(text: str) -> Optional[float]:
    try:
        return float(re.sub(r"[^0-9.\-]", "", text) or "0") or None
    except ValueError:
        return None


class FpdsClient:
    def __init__(self, cache_dir: Optional[Path] = None, timeout: float = 60.0,
                 transport: Optional[httpx.BaseTransport] = None):
        self.cache_dir = cache_dir
        if cache_dir:
            cache_dir.mkdir(parents=True, exist_ok=True)
        self._http = httpx.Client(timeout=timeout, follow_redirects=True, transport=transport)

    def search_awards(
        self, naics: str, agency: Optional[str] = None, pages: int = 3
    ) -> list[AwardRecord]:
        """Recent awards under a NAICS (10 entries/page in the public feed)."""
        q = f'PRINCIPAL_NAICS_CODE:"{naics}"'
        if agency:
            q += f' CONTRACTING_AGENCY_NAME:"{agency}"'
        records: list[AwardRecord] = []
        for page in range(pages):
            xml_text = self._fetch(q, start=page * 10)
            if not xml_text:
                break
            batch = parse_atom(xml_text)
            if not batch:
                break
            records.extend(batch)
        return records

    def _fetch(self, q: str, start: int) -> Optional[str]:
        key = hashlib.sha256(f"{q}:{start}".encode()).hexdigest()
        if self.cache_dir:
            cached = self.cache_dir / f"fpds_{key}.xml"
            if cached.exists():
                return cached.read_text(encoding="utf-8")
        try:
            resp = self._http.get(
                FPDS_ATOM, params={"FEEDNAME": "PUBLIC", "q": q, "start": start}
            )
            resp.raise_for_status()
        except Exception:
            return None
        if self.cache_dir:
            (self.cache_dir / f"fpds_{key}.xml").write_text(resp.text, encoding="utf-8")
        return resp.text


def price_position(our_total: float, awards: list[AwardRecord]) -> dict:
    """Where a priced total sits against award history. Advisory only."""
    values = sorted(a.value for a in awards if a.value and a.value > 0)
    if len(values) < 5:
        return {
            "n": len(values),
            "advisory": f"Only {len(values)} comparable awards found — no defensible position; price bottom-up.",
        }
    def pct(p: float) -> float:
        idx = min(len(values) - 1, max(0, int(p * (len(values) - 1))))
        return values[idx]
    below = sum(1 for v in values if v < our_total)
    percentile = below / len(values)
    return {
        "n": len(values),
        "median": pct(0.5),
        "p25": pct(0.25),
        "p75": pct(0.75),
        "percentile": round(percentile, 2),
        "advisory": (
            f"Priced total ${our_total:,.0f} sits at the {percentile:.0%} percentile of "
            f"{len(values)} comparable awards (median ${pct(0.5):,.0f}, IQR "
            f"${pct(0.25):,.0f}-${pct(0.75):,.0f}). Advisory only — award values mix "
            "scopes and periods; a human validates comparability."
        ),
    }


def save_awards(awards: list[AwardRecord], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([a.model_dump() for a in awards], indent=2), encoding="utf-8"
    )
