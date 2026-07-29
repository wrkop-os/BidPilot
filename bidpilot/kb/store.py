"""Knowledge base store: load, resolve citations, render for prompts.

v1 is a per-tenant YAML directory (see kb.example/). Production target per
PRD §11 is Postgres + pgvector with tenant-scoped retrieval; this class is
the interface boundary where that swap happens.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import yaml

from .schema import (
    CompanyProfile,
    KnowledgeBaseData,
    PastPerformanceRecord,
    PersonnelRecord,
    ReusableContent,
)


class KnowledgeBase:
    def __init__(self, data: KnowledgeBaseData):
        self.data = data
        self._index: dict[str, object] = {}
        self._index[data.profile.kb_id] = data.profile
        for record in data.past_performance + data.personnel + data.reusable_content:
            if record.kb_id in self._index:
                raise ValueError(f"Duplicate KB id: {record.kb_id}")
            self._index[record.kb_id] = record

    # -- citation resolution (FR-10 / G5) -------------------------------------

    def resolve(self, kb_id: str) -> Optional[object]:
        return self._index.get(kb_id)

    def known_ids(self) -> list[str]:
        return sorted(self._index.keys())

    # -- prompt rendering ------------------------------------------------------

    @property
    def profile(self) -> CompanyProfile:
        return self.data.profile

    def profile_text(self) -> str:
        return yaml.safe_dump(self.data.profile.model_dump(exclude_none=True), sort_keys=False)

    def labor_category_list(self) -> str:
        return "\n".join(
            f"- {lc.title}: ${lc.direct_rate:.2f}/hr direct" + (f" — {lc.description}" if lc.description else "")
            for lc in self.data.profile.labor_categories
        ) or "(no labor categories on file)"

    def direct_rates(self) -> dict[str, float]:
        return {lc.title: lc.direct_rate for lc in self.data.profile.labor_categories}

    def past_performance_text(self) -> str:
        blocks = []
        for pp in self.data.past_performance:
            blocks.append(f"[{pp.kb_id}] {pp.customer} — {pp.scope_narrative}")
            details = []
            if pp.value:
                details.append(f"value ${pp.value:,.0f}")
            if pp.period:
                details.append(pp.period)
            if pp.cpars_rating:
                details.append(f"CPARS: {pp.cpars_rating}")
            if pp.role:
                details.append(pp.role)
            if details:
                blocks.append("    " + ", ".join(details))
            if pp.historical_actuals:
                blocks.append(f"    actuals: {pp.historical_actuals}")
        return "\n".join(blocks) or "(no past performance records)"

    def personnel_text(self) -> str:
        return "\n".join(
            f"[{p.kb_id}] {p.name} — {p.role}. {p.resume_summary}"
            + (f" Certs: {', '.join(p.certifications)}" if p.certifications else "")
            for p in self.data.personnel
        ) or "(no personnel records)"

    def reusable_content_text(self) -> str:
        return "\n\n".join(
            f"[{c.kb_id}] {c.title} ({c.kind}):\n{c.text}" for c in self.data.reusable_content
        ) or "(no reusable content)"

    def citable_corpus(self) -> str:
        """Everything a writer may cite, with IDs. The writer prompt makes clear
        that these IDs are the only valid kb_source_id values."""
        return "\n\n".join(
            [
                "=== COMPANY PROFILE [profile] ===",
                self.profile_text(),
                "=== PAST PERFORMANCE ===",
                self.past_performance_text(),
                "=== KEY PERSONNEL ===",
                self.personnel_text(),
                "=== REUSABLE CONTENT ===",
                self.reusable_content_text(),
            ]
        )

    def stale_entries(self, max_age_days: int = 365) -> list[str]:
        """FR-7-style governance: entries whose last_verified is missing/old."""
        import datetime as dt

        stale = []
        cutoff = dt.date.today() - dt.timedelta(days=max_age_days)
        for kb_id, obj in self._index.items():
            gov = getattr(obj, "governance", None)
            verified = getattr(gov, "last_verified", None) if gov else None
            if not verified:
                stale.append(f"{kb_id}: never verified")
                continue
            try:
                if dt.date.fromisoformat(verified) < cutoff:
                    stale.append(f"{kb_id}: last verified {verified}")
            except ValueError:
                stale.append(f"{kb_id}: unparseable last_verified {verified!r}")
        return stale


DEFAULT_KB_DIRS = ("kb", "knowledge_base")


def load_kb(path: Optional[str] = None) -> KnowledgeBase:
    """Load a KB directory: profile.yaml + past_performance.yaml +
    personnel.yaml + reusable_content.yaml (all but profile optional)."""
    candidates = [path] if path else [str(Path.cwd() / p) for p in DEFAULT_KB_DIRS]
    kb_dir = next((c for c in candidates if c and os.path.isdir(c)), None)
    if kb_dir is None:
        raise FileNotFoundError(
            "No knowledge base directory found. Run `bidpilot init-kb` to create one "
            "from the example, or pass --kb PATH."
        )
    root = Path(kb_dir)

    def _load(name: str, default):
        f = root / name
        if not f.exists():
            return default
        return yaml.safe_load(f.read_text(encoding="utf-8")) or default

    profile_raw = _load("profile.yaml", None)
    if profile_raw is None:
        raise FileNotFoundError(f"{root}/profile.yaml is required.")
    data = KnowledgeBaseData(
        profile=CompanyProfile.model_validate(profile_raw),
        past_performance=[
            PastPerformanceRecord.model_validate(r) for r in _load("past_performance.yaml", [])
        ],
        personnel=[PersonnelRecord.model_validate(r) for r in _load("personnel.yaml", [])],
        reusable_content=[
            ReusableContent.model_validate(r) for r in _load("reusable_content.yaml", [])
        ],
    )
    return KnowledgeBase(data)
