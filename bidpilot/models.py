"""Core data models for the BidPilot pipeline.

Every artifact that moves between agents is a typed Pydantic model so that
each stage's output is validated before the next stage consumes it, and so
the whole pipeline can be serialized to disk for human review.

Schema note: these models are also used as structured-output schemas for
Claude API calls (via ``client.messages.parse``), so fields stay to plain
JSON-schema-friendly types.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Solicitation ingestion
# ---------------------------------------------------------------------------


class AttachmentInfo(BaseModel):
    """A solicitation attachment downloaded from SAM.gov."""

    name: str
    local_path: str
    mime_type: Optional[str] = None
    extracted: bool = False
    char_count: int = 0


class RawOpportunity(BaseModel):
    """The opportunity as fetched from SAM.gov, before any LLM analysis."""

    notice_id: str
    solicitation_number: Optional[str] = None
    title: Optional[str] = None
    agency: Optional[str] = None
    notice_type: Optional[str] = None
    posted_date: Optional[str] = None
    response_deadline: Optional[str] = None
    naics_code: Optional[str] = None
    set_aside: Optional[str] = None
    description_text: str = ""
    attachments: list[AttachmentInfo] = Field(default_factory=list)
    raw_api_record: Optional[dict] = None


# ---------------------------------------------------------------------------
# Structured solicitation analysis (Parser agent)
# ---------------------------------------------------------------------------


class KeyDate(BaseModel):
    label: str = Field(description="What the date is, e.g. 'Questions due', 'Proposal due'")
    date: str = Field(description="The date/time as stated in the solicitation, including timezone if given")


class EvaluationFactor(BaseModel):
    name: str
    description: str
    relative_importance: Optional[str] = Field(
        default=None, description="Stated relative importance/weighting, if any"
    )


class VolumeRequirement(BaseModel):
    name: str = Field(description="Volume name, e.g. 'Volume I - Technical'")
    page_limit: Optional[str] = None
    format_requirements: Optional[str] = Field(
        default=None, description="Font, margins, file format, and other formatting rules"
    )
    required_content: list[str] = Field(
        default_factory=list, description="Sections/content the volume must contain"
    )


class SolicitationAnalysis(BaseModel):
    """Structured understanding of the full solicitation (notice + attachments)."""

    summary: str = Field(description="2-4 paragraph plain-language summary of what is being procured")
    contract_type: Optional[str] = Field(default=None, description="e.g. FFP, T&M, IDIQ")
    place_of_performance: Optional[str] = None
    period_of_performance: Optional[str] = None
    naics_code: Optional[str] = None
    small_business_set_aside: Optional[str] = None
    key_dates: list[KeyDate] = Field(default_factory=list)
    scope_items: list[str] = Field(
        default_factory=list, description="Discrete work items from the SOW/PWS"
    )
    volumes: list[VolumeRequirement] = Field(default_factory=list)
    evaluation_factors: list[EvaluationFactor] = Field(default_factory=list)
    required_forms: list[str] = Field(
        default_factory=list,
        description="Government forms and representations required (e.g. SF-1449, SF-33, reps & certs)",
    )
    security_requirements: list[str] = Field(default_factory=list)
    ambiguities_and_risks: list[str] = Field(
        default_factory=list,
        description="Things a human should clarify with the CO or watch out for",
    )


# ---------------------------------------------------------------------------
# Eligibility (Eligibility agent)
# ---------------------------------------------------------------------------


class EligibilityVerdict(str, Enum):
    ELIGIBLE = "eligible"
    INELIGIBLE = "ineligible"
    NEEDS_HUMAN_REVIEW = "needs_human_review"


class EligibilityCheck(BaseModel):
    requirement: str
    company_position: str = Field(description="How the company profile answers this requirement")
    passes: Optional[bool] = Field(
        default=None, description="True/False if determinable, null if a human must decide"
    )
    notes: Optional[str] = None


class EligibilityReport(BaseModel):
    verdict: EligibilityVerdict
    rationale: str
    checks: list[EligibilityCheck] = Field(default_factory=list)
    blockers: list[str] = Field(
        default_factory=list, description="Hard blockers that make the bid ineligible"
    )
    human_review_items: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Compliance matrix (Compliance agent)
# ---------------------------------------------------------------------------


class ComplianceRow(BaseModel):
    requirement_id: str = Field(description="Short ID, e.g. 'L-3.2.1' or 'C-014'")
    source: str = Field(description="Where in the solicitation this comes from (section/page/attachment)")
    requirement_text: str
    category: str = Field(
        description="One of: submission, format, content, evaluation, contractual, forms"
    )
    proposal_location: Optional[str] = Field(
        default=None, description="Where in our proposal this is addressed"
    )
    status: str = Field(default="open", description="open | addressed | n/a | human_action_required")
    notes: Optional[str] = None


class ComplianceMatrix(BaseModel):
    rows: list[ComplianceRow] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Submission instructions (Submission agent) — the "who/where/how/when"
# ---------------------------------------------------------------------------


class SubmissionInstructions(BaseModel):
    """Machine-extracted submission instructions.

    SAM.gov is where opportunities are POSTED, not where proposals are
    SUBMITTED. This model captures exactly how this solicitation says the
    proposal must be delivered.
    """

    method: str = Field(
        description="Delivery method, e.g. 'email', 'PIEE', 'GSA eBuy', 'FedConnect', 'Unison Marketplace', 'mail'"
    )
    destination: str = Field(
        description="Email address, portal URL, or physical address where the proposal goes"
    )
    deadline: str = Field(description="Exact due date/time with timezone as stated")
    contacts: list[str] = Field(
        default_factory=list, description="Contracting Officer / Specialist names and contact info"
    )
    format_rules: list[str] = Field(
        default_factory=list,
        description="File formats, size limits, subject-line requirements, number of copies, etc.",
    )
    special_instructions: list[str] = Field(default_factory=list)
    confidence_notes: Optional[str] = Field(
        default=None,
        description="Anything ambiguous about these instructions a human must verify before submitting",
    )


# ---------------------------------------------------------------------------
# Cost estimate (Cost agent)
# ---------------------------------------------------------------------------


class CostLineItem(BaseModel):
    description: str
    labor_category: Optional[str] = None
    hours: Optional[float] = None
    rate: Optional[float] = None
    extended_cost: Optional[float] = None
    assumption: Optional[str] = Field(
        default=None, description="Basis-of-estimate assumption behind this line"
    )


class CostEstimate(BaseModel):
    """Rough-order-of-magnitude estimate with a defensible basis of estimate.

    Always requires human pricing review before submission.
    """

    total: Optional[float] = None
    currency: str = "USD"
    line_items: list[CostLineItem] = Field(default_factory=list)
    basis_of_estimate: str = Field(
        description="Narrative explaining methodology, assumptions, and data sources"
    )
    risks_and_exclusions: list[str] = Field(default_factory=list)
    human_pricing_actions: list[str] = Field(
        default_factory=list,
        description="Pricing decisions a human MUST make/verify (rates, escalation, fee, wrap rates)",
    )


# ---------------------------------------------------------------------------
# Forms & representations (Forms agent)
# ---------------------------------------------------------------------------


class FormItem(BaseModel):
    form_name: str
    purpose: str
    prefill: dict[str, str] = Field(
        default_factory=dict,
        description="Fields that can be pre-filled from the company profile (field -> value)",
    )
    human_actions: list[str] = Field(
        default_factory=list, description="What a human must complete, sign, or certify"
    )


class FormsPackage(BaseModel):
    forms: list[FormItem] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Final package manifest
# ---------------------------------------------------------------------------


class PackageManifest(BaseModel):
    notice_id: str
    title: Optional[str] = None
    generated_at: str
    output_dir: str
    files: list[str] = Field(default_factory=list)
    human_review_required: bool = True
    disclaimer: str = (
        "DRAFT — generated by BidPilot. A human must review, verify compliance, "
        "approve pricing, sign, and submit. BidPilot never submits proposals."
    )
