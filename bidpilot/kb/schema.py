"""Company Knowledge Base schemas (PRD §6.5) — "the ingredient everyone forgets."

Governance rule: every entry has an ID, an owner, and a last-verified date,
and the KB is the ONLY permissible source for factual claims about the
company (FR-10). Writers cite entries by ID; the fail-closed gate rejects
claims whose kb_source_id doesn't resolve here.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from ..pricing.rates import IndirectRateStructure


class Governance(BaseModel):
    owner: Optional[str] = None
    last_verified: Optional[str] = Field(default=None, description="ISO date")


class LaborCategory(BaseModel):
    title: str
    direct_rate: float = Field(description="Direct hourly rate (unburdened)")
    description: Optional[str] = None


class PersonnelRecord(BaseModel):
    kb_id: str
    name: str
    role: str
    clearance: Optional[str] = None
    resume_summary: str = ""
    certifications: list[str] = Field(default_factory=list)
    governance: Governance = Field(default_factory=Governance)


class PastPerformanceRecord(BaseModel):
    kb_id: str
    customer: str
    contract_number: Optional[str] = None
    value: Optional[float] = None
    period: Optional[str] = Field(default=None, description="e.g. 2022-06 to 2025-05")
    scope_narrative: str = ""
    naics_codes: list[str] = Field(default_factory=list)
    cpars_rating: Optional[str] = None
    reference_contact: Optional[str] = None
    role: str = Field(default="prime", description="prime | subcontractor")
    historical_actuals: Optional[str] = Field(
        default=None, description="Hours/effort actuals usable for analogy estimating"
    )
    governance: Governance = Field(default_factory=Governance)


class ReusableContent(BaseModel):
    kb_id: str
    title: str
    kind: str = Field(description="quality_plan | management_approach | capability_statement | prior_proposal | odc_catalog | other")
    text: str = ""
    governance: Governance = Field(default_factory=Governance)


class CompanyProfile(BaseModel):
    kb_id: str = "profile"
    name: str
    uei: Optional[str] = None
    cage_code: Optional[str] = None
    address: Optional[str] = None
    poc_name: Optional[str] = None
    poc_email: Optional[str] = None
    poc_phone: Optional[str] = None

    naics_codes: list[str] = Field(default_factory=list)
    size_status_by_naics: dict[str, str] = Field(
        default_factory=dict, description="NAICS -> 'small' | 'other_than_small'"
    )
    annual_receipts_avg: Optional[float] = Field(
        default=None, description="Average annual receipts (for revenue-based size standards)"
    )
    employee_count: Optional[int] = None
    socioeconomic_certifications: list[str] = Field(
        default_factory=list, description="8(a), HUBZone, SDVOSB (VetCert), WOSB/EDWOSB"
    )
    sam_registration_active: bool = False
    facility_clearance: Optional[str] = None
    cmmc_level: Optional[str] = None
    bonding_capacity: Optional[float] = None
    size_data_verified_date: Optional[str] = Field(
        default=None, description="FR-7 staleness check: flag if > 12 months old"
    )

    capabilities: list[str] = Field(default_factory=list)
    labor_categories: list[LaborCategory] = Field(default_factory=list)
    indirect_rates: Optional[IndirectRateStructure] = None
    governance: Governance = Field(default_factory=Governance)


class KnowledgeBaseData(BaseModel):
    profile: CompanyProfile
    past_performance: list[PastPerformanceRecord] = Field(default_factory=list)
    personnel: list[PersonnelRecord] = Field(default_factory=list)
    reusable_content: list[ReusableContent] = Field(default_factory=list)
