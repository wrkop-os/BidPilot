"""Pricing artifacts (PRD §9). Numbers are computed by code; the LLM's job
is decomposition, hour proposals with recorded method, and BOE narrative."""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class ContractType(str, Enum):
    FFP = "ffp"
    TM = "t_and_m"
    COST = "cost_reimbursable"
    UNKNOWN = "unknown"


class CLINItem(BaseModel):
    clin: str
    description: str
    contract_type: ContractType = ContractType.UNKNOWN
    quantity: Optional[float] = None
    unit: Optional[str] = None
    period: Optional[str] = Field(default=None, description="Base / Option Year N")


class PricingStructure(BaseModel):
    clins: list[CLINItem] = Field(default_factory=list)
    government_template_file: Optional[str] = Field(
        default=None, description="Path to the government's own XLSX pricing template, if provided"
    )
    igce_hints: list[str] = Field(default_factory=list)
    notes: Optional[str] = None


class EstimationMethod(str, Enum):
    ANALOGY = "analogy"          # historical actuals from the KB — strongest
    PARAMETRIC = "parametric"    # heuristic drivers
    BOTTOM_UP = "bottom_up"


class WBSTask(BaseModel):
    task_id: str
    title: str
    description: str
    sow_reference: Optional[str] = None
    deliverables: list[str] = Field(default_factory=list)


class LaborLine(BaseModel):
    task_id: str
    labor_category: str
    hours: float
    method: EstimationMethod
    rationale: str = Field(description="Why these hours — recorded per line for the BOE")
    confidence: str = Field(default="medium", description="low | medium | high")


class LaborEstimate(BaseModel):
    wbs: list[WBSTask] = Field(default_factory=list)
    lines: list[LaborLine] = Field(default_factory=list)


class ODCItem(BaseModel):
    description: str
    estimated_cost: Optional[float] = None
    source: Optional[str] = Field(default=None, description="KB catalog entry or rationale")
    quote_needed: bool = Field(default=False, description="[QUOTE NEEDED] — human must obtain")


class PricedLine(BaseModel):
    """A labor line after the deterministic rate engine has run."""

    task_id: str
    labor_category: str
    hours: float
    direct_rate: float
    wrapped_rate: float
    extended: float
    year: int = Field(default=0, description="0 = base year, 1..N = option years")
    wd_floor: Optional[float] = None
    wd_compliant: Optional[bool] = None


class WDViolation(BaseModel):
    labor_category: str
    direct_rate: float
    wd_floor: float
    detail: str


class SensitivityPoint(BaseModel):
    label: str
    total: float


class SCAErosionYear(BaseModel):
    year: int
    hours: float
    wage_delta_per_hour: float = Field(
        description="Assumed wage-determination increase over the base year, hours-weighted"
    )
    fully_loaded: float = Field(description="What that delta costs once wrapped")
    recoverable: float = Field(description="Wage delta + statutory burden — all 52.222-43(d) allows")
    unrecovered: float = Field(description="The OH/G&A/fee the clause does not restore")


class SCAErosion(BaseModel):
    """Projected margin erosion on SCA labor (bidpilot/pricing/sca_erosion.py).

    Holding covered categories flat is legally required; the clause's
    adjustment carries no overhead, G&A, or profit. This is what that costs.
    """

    assumed_wd_growth: float
    statutory_burden_rate: float
    statutory_burden_basis: str = ""
    covered_categories: list[str] = Field(default_factory=list)
    years: list[SCAErosionYear] = Field(default_factory=list)
    total_unrecovered: float = 0.0
    total_price: Optional[float] = None
    margin_points: Optional[float] = Field(
        default=None, description="Unrecovered as a fraction of proposed total price"
    )
    fee_uplift_needed: Optional[float] = Field(
        default=None, description="Additional fee rate across the job that would offset it"
    )


class ComplianceFinding(BaseModel):
    """A regulatory finding against the cost volume (bidpilot/pricing/compliance.py).

    `hard` blocks export, `soft` is a reviewer to-do, `info` is context the
    pricer should read before signing. Every finding names the FAR cite it
    rests on so a reviewer can check the system's reasoning against the book.
    """

    severity: str = Field(description="hard | soft | info")
    rule: str = Field(description="FAR/statutory cite the finding rests on")
    detail: str
    remedy: str = ""
    location: Optional[str] = None


class CellWrite(BaseModel):
    """One proposed write into the government's own XLSX pricing template."""

    sheet: str
    cell: str = Field(description="A1-style reference, e.g. 'D14'")
    value: str = Field(description="Value to write (numbers as plain digits)")
    note: str = Field(default="", description="What this cell is / why this value")


class TemplateFillProposal(BaseModel):
    writes: list[CellWrite] = Field(default_factory=list)
    unfillable_reason: Optional[str] = Field(
        default=None,
        description="Set when the template can't be safely machine-filled (macros, merged "
        "cells, unclear structure) — the fallback is 'human fills, system computes'",
    )


class PricingModel(BaseModel):
    structure: Optional[PricingStructure] = None
    estimate: Optional[LaborEstimate] = None
    odcs: list[ODCItem] = Field(default_factory=list)
    priced_lines: list[PricedLine] = Field(default_factory=list)
    total: Optional[float] = None
    wd_violations: list[WDViolation] = Field(
        default_factory=list, description="Wage-determination floor violations — hard errors"
    )
    sensitivity: list[SensitivityPoint] = Field(default_factory=list)
    boe_narrative: str = ""
    quote_needed: list[str] = Field(default_factory=list)
    human_pricing_actions: list[str] = Field(default_factory=list)
    template_fill: Optional[TemplateFillProposal] = None
    compliance_findings: list[ComplianceFinding] = Field(
        default_factory=list,
        description="Regulatory findings on the cost volume — see pricing/compliance.py",
    )
    pricing_obligations: list[str] = Field(
        default_factory=list,
        description="Plain-language duties triggered by the pricing clauses actually present",
    )
    sca_erosion: Optional[SCAErosion] = Field(
        default=None,
        description="Projected unrecovered OH/G&A/fee on SCA labor across option years",
    )
