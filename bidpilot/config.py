"""Company profile configuration.

The bidding entity's profile drives eligibility checks, cost estimation
(labor categories and rates), and form pre-fill. It lives in a YAML file the
user maintains — see ``company_profile.example.yaml`` at the repo root.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field


class LaborCategory(BaseModel):
    title: str
    hourly_rate: float
    description: Optional[str] = None


class CompanyProfile(BaseModel):
    name: str
    uei: Optional[str] = Field(default=None, description="SAM.gov Unique Entity ID")
    cage_code: Optional[str] = None
    duns: Optional[str] = None
    address: Optional[str] = None
    poc_name: Optional[str] = None
    poc_email: Optional[str] = None
    poc_phone: Optional[str] = None

    naics_codes: list[str] = Field(default_factory=list)
    small_business: bool = True
    socioeconomic_certifications: list[str] = Field(
        default_factory=list,
        description="e.g. 8(a), WOSB, SDVOSB, HUBZone",
    )
    sam_registration_active: bool = False
    facility_clearance: Optional[str] = None

    capabilities: list[str] = Field(default_factory=list)
    past_performance: list[str] = Field(
        default_factory=list,
        description="Short summaries of relevant past contracts (customer, scope, value, period)",
    )
    key_personnel: list[str] = Field(default_factory=list)
    labor_categories: list[LaborCategory] = Field(default_factory=list)

    def summary_text(self) -> str:
        """Render the profile as text for inclusion in agent prompts."""
        return yaml.safe_dump(self.model_dump(exclude_none=True), sort_keys=False)


DEFAULT_PROFILE_PATHS = ("company_profile.yaml", "company_profile.yml")


def load_profile(path: Optional[str] = None) -> CompanyProfile:
    """Load the company profile from an explicit path or default locations."""
    candidates = [path] if path else [str(Path.cwd() / p) for p in DEFAULT_PROFILE_PATHS]
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            with open(candidate, "r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
            return CompanyProfile.model_validate(data)
    raise FileNotFoundError(
        "No company profile found. Copy company_profile.example.yaml to "
        "company_profile.yaml and fill it in, or pass --profile PATH."
    )
