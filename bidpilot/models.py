"""Typed contracts between agents (PRD §6.2.1: shared state, typed contracts).

Every artifact that flows through the ProposalState blackboard is defined
here. Agents read/write only their declared fields; these models ARE the
stage contracts, and they double as structured-output schemas for LLM calls.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Stage 1 — Intake (A1): NoticePackage
# ---------------------------------------------------------------------------


class AttachmentRecord(BaseModel):
    name: str
    local_path: str
    mime_type: Optional[str] = None
    sha256: Optional[str] = None
    restricted: bool = Field(
        default=False, description="Login-restricted on SAM.gov — needs manual download"
    )
    source_notice_id: Optional[str] = Field(
        default=None, description="Which notice in the amendment chain supplied this file"
    )


class NoticeMetadata(BaseModel):
    notice_id: str
    solicitation_number: Optional[str] = None
    title: Optional[str] = None
    agency: Optional[str] = None
    notice_type_raw: Optional[str] = None
    posted_date: Optional[str] = None
    response_deadline: Optional[str] = None
    naics_code: Optional[str] = None
    psc_code: Optional[str] = None
    set_aside: Optional[str] = None
    place_of_performance: Optional[str] = None
    points_of_contact: list[str] = Field(default_factory=list)
    description_text: str = ""
    raw_api_record: Optional[dict] = None


class AmendmentRecord(BaseModel):
    notice_id: str
    posted_date: Optional[str] = None
    title: Optional[str] = None
    is_latest: bool = False


class NoticePackage(BaseModel):
    """Output of the Intake agent: metadata + files + full amendment chain.

    Bidding off a stale version is a classic fatal error, so the chain is
    fetched by solicitation number, not just the linked notice.
    """

    metadata: NoticeMetadata
    files: list[AttachmentRecord] = Field(default_factory=list)
    amendment_history: list[AmendmentRecord] = Field(default_factory=list)
    restricted_files_flagged: bool = False


# ---------------------------------------------------------------------------
# Stage 2 — Document Processing (A2): DocTree
# ---------------------------------------------------------------------------


class TableData(BaseModel):
    doc_name: str
    page: Optional[int] = None
    rows: list[list[str]] = Field(default_factory=list)


class DocSection(BaseModel):
    section_id: str = Field(description="e.g. 'L', 'M', 'C', 'PWS-3.1', 'ATT-3'")
    title: str
    page_start: Optional[int] = None
    page_end: Optional[int] = None
    text: str = ""


class ParsedDoc(BaseModel):
    name: str
    kind: str = Field(description="pdf | docx | xlsx | image | other")
    page_count: int = 0
    sections: list[DocSection] = Field(default_factory=list)
    tables: list[TableData] = Field(default_factory=list)
    fillable_template: bool = Field(
        default=False, description="XLSX/PDF preserved as a fillable artifact, never flattened"
    )
    ocr_used: bool = False
    cui_markings: list[str] = Field(
        default_factory=list, description="CUI/ITAR/export-control markings found — triggers halt path"
    )
    full_text: str = ""


class DocTree(BaseModel):
    docs: list[ParsedDoc] = Field(default_factory=list)

    def corpus(self, max_chars_per_doc: int = 400_000) -> str:
        parts = []
        for doc in self.docs:
            parts.append(f"=== DOCUMENT: {doc.name} ===")
            parts.append(doc.full_text[:max_chars_per_doc])
        return "\n\n".join(parts)

    def cui_flags(self) -> list[str]:
        flags = []
        for doc in self.docs:
            flags.extend(f"{doc.name}: {m}" for m in doc.cui_markings)
        return flags


# ---------------------------------------------------------------------------
# Stage 3 — Classification (A3)
# ---------------------------------------------------------------------------


class NoticeType(str, Enum):
    SOURCES_SOUGHT = "sources_sought"
    PRESOLICITATION = "presolicitation"
    SOLICITATION = "solicitation"
    COMBINED_SYNOPSIS = "combined_synopsis_solicitation"
    AMENDMENT = "amendment"
    AWARD = "award"
    OTHER = "other"


class FarRegime(str, Enum):
    PART_15 = "far_15_negotiated"
    PART_12 = "far_12_commercial"
    PART_13 = "far_13_simplified"
    PART_14 = "far_14_sealed_bid"
    UNKNOWN = "unknown"


class ResponseArtifact(str, Enum):
    FULL_PROPOSAL = "full_proposal"
    QUOTE = "quote"
    CAPABILITY_STATEMENT = "capability_statement"
    BID = "sealed_bid"
    NONE = "none_monitor"


class KeyDate(BaseModel):
    label: str
    date: str = Field(description="As stated, timezone-explicit where given")
    timezone: Optional[str] = None


class Classification(BaseModel):
    notice_type: NoticeType
    far_regime: FarRegime
    response_artifact: ResponseArtifact
    submission_channel_hint: Optional[str] = None
    key_dates: list[KeyDate] = Field(default_factory=list)
    ai_disclosure_clause_detected: bool = Field(
        default=False, description="Solicitation asks offerors to disclose AI use (§14.2)"
    )
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    escalated: bool = Field(default=False, description="Low confidence → escalated to frontier model")
    rationale: Optional[str] = None


# ---------------------------------------------------------------------------
# Stage 4 — Eligibility (A4)
# ---------------------------------------------------------------------------


class BidRecommendation(str, Enum):
    BID = "bid"
    NO_BID = "no_bid"
    CONDITIONAL = "conditional"


class EligibilityCheck(BaseModel):
    dimension: str = Field(
        description="registration | set_aside | size_standard | special_regime | practical"
    )
    requirement: str
    company_position: str
    passes: Optional[bool] = Field(default=None, description="null when a human must decide")
    evidence: Optional[str] = None


class EligibilityReport(BaseModel):
    """Never a bare yes/no (FR-5)."""

    hard_blockers: list[str] = Field(default_factory=list)
    soft_risks: list[str] = Field(default_factory=list)
    missing_info: list[str] = Field(default_factory=list)
    checks: list[EligibilityCheck] = Field(default_factory=list)
    bid_recommendation: BidRecommendation
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    rationale: str


# ---------------------------------------------------------------------------
# Stage 5 — Compliance Shredder (A5)
# ---------------------------------------------------------------------------


class Citation(BaseModel):
    doc: str
    section: Optional[str] = None
    page: Optional[int] = None

    def render(self) -> str:
        bits = [self.doc]
        if self.section:
            bits.append(f"§{self.section}")
        if self.page is not None:
            bits.append(f"p.{self.page}")
        return " ".join(bits)


class RequirementCategory(str, Enum):
    FORMAT = "format"
    CONTENT = "content"
    ADMINISTRATIVE = "administrative"
    EVALUATION = "evaluation"


class RequirementStatus(str, Enum):
    UNADDRESSED = "unaddressed"
    DRAFTED = "drafted"
    VERIFIED = "verified"


class Requirement(BaseModel):
    req_id: str
    verbatim_text: str = Field(description="Binding language kept intact — never paraphrased away")
    source: Citation
    category: RequirementCategory
    owner_section: Optional[str] = Field(
        default=None, description="Outline section responsible for addressing this"
    )
    status: RequirementStatus = RequirementStatus.UNADDRESSED
    addressed_in: list[str] = Field(default_factory=list)
    notes: Optional[str] = None


class FormatConstraints(BaseModel):
    page_limits: dict[str, int] = Field(default_factory=dict, description="volume name -> pages")
    font: Optional[str] = None
    font_size: Optional[str] = None
    margins: Optional[str] = None
    file_formats: list[str] = Field(default_factory=list)
    naming_convention: Optional[str] = None
    copies: Optional[str] = None


class OutlineSection(BaseModel):
    section_id: str
    title: str
    volume: str
    assigned_requirements: list[str] = Field(default_factory=list, description="req_ids")
    guidance: Optional[str] = None


class ProposalOutline(BaseModel):
    """Derived strictly from Section L / 52.212-1 (FR-9)."""

    volumes: list[str] = Field(default_factory=list)
    sections: list[OutlineSection] = Field(default_factory=list)
    deviation_note: Optional[str] = Field(
        default=None, description="Set only when a user explicitly overrides the L-driven outline"
    )


class ComplianceMatrix(BaseModel):
    requirements: list[Requirement] = Field(default_factory=list)
    outline: Optional[ProposalOutline] = None
    constraints: FormatConstraints = Field(default_factory=FormatConstraints)


# ---------------------------------------------------------------------------
# Stage 6 — Production swarm artifacts
# ---------------------------------------------------------------------------


class WinStrategy(BaseModel):
    solution_summary: str
    win_themes: list[str] = Field(default_factory=list)
    discriminators: list[str] = Field(default_factory=list)
    risks_and_mitigations: list[str] = Field(default_factory=list)
    staffing_concept: str = ""
    assumptions: list[str] = Field(
        default_factory=list,
        description="Surfaced to the human at the assumptions checkpoint — never silently guessed",
    )


class Claim(BaseModel):
    """One company-factual statement in a draft, with its provenance (FR-10)."""

    text: str
    kb_source_id: Optional[str] = Field(
        default=None, description="KB entry ID backing this claim; null means uncited"
    )
    needs_input: bool = Field(
        default=False, description="Emitted as [NEEDS INPUT: ...] — a human must supply this fact"
    )
    input_note: Optional[str] = None


class SectionDraft(BaseModel):
    section_id: str
    volume: str
    title: str
    markdown: str
    addressed_requirements: list[str] = Field(default_factory=list, description="req_ids")
    claims: list[Claim] = Field(default_factory=list)
    word_count: int = 0
    human_edited: bool = Field(
        default=False,
        description="Set by sync-drafts when a reviewer edited this section on disk — "
        "the claim map may be stale; the reviewer owns the accuracy of their edits",
    )


class PastPerformanceSelection(BaseModel):
    class Reference(BaseModel):
        kb_id: str
        relevancy_score: float = Field(ge=0.0, le=1.0)
        relevancy_narrative: str

    references: list[Reference] = Field(default_factory=list)
    gaps: list[str] = Field(
        default_factory=list, description="e.g. 'solicitation wants 3 refs >= $1M; KB has 2'"
    )


class FormItem(BaseModel):
    form_name: str
    purpose: str
    source_file: Optional[str] = Field(
        default=None, description="Attachment filename containing this form, if identified"
    )
    prefill: dict[str, str] = Field(default_factory=dict)
    signature_required: bool = False
    human_actions: list[str] = Field(default_factory=list)
    filled_file: Optional[str] = Field(
        default=None, description="Path to the machine-prefilled copy (admin fields only)"
    )


class FormsPackage(BaseModel):
    forms: list[FormItem] = Field(default_factory=list)
    amendment_acknowledgments: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class SubmissionSheet(BaseModel):
    """One-page normalized submission instructions (A11) — a first-class artifact."""

    channel: str = Field(description="email | PIEE | GSA eBuy | FedConnect | Unison | sam_gov | physical | unknown")
    destination: str
    deadline: str
    deadline_timezone: Optional[str] = None
    questions_deadline: Optional[str] = None
    max_attachment_size: Optional[str] = None
    subject_line_requirements: Optional[str] = None
    file_format_rules: list[str] = Field(default_factory=list)
    copies: Optional[str] = None
    special_instructions: list[str] = Field(default_factory=list)
    confidence_notes: Optional[str] = Field(
        default=None, description="Ambiguities a human must verify before submitting"
    )


class RenderedVolumeInfo(BaseModel):
    """One rendered volume (FR-15). page_count is exact when LibreOffice
    converted the DOCX to PDF; estimated_pages is the always-available
    word-count heuristic."""

    volume: str
    docx_path: str
    pdf_path: Optional[str] = None
    page_count: Optional[int] = None
    estimated_pages: float = 0.0
    word_count: int = 0


# ---------------------------------------------------------------------------
# Stage 8 — QA / Red Team (A12)
# ---------------------------------------------------------------------------


class QASeverity(str, Enum):
    HARD = "hard"      # blocks export
    SOFT = "soft"
    INFO = "info"


class QAFinding(BaseModel):
    severity: QASeverity
    category: str = Field(
        description="coverage | citation | consistency | format | fabrication | evaluation"
    )
    description: str
    location: Optional[str] = None
    resolved: bool = False


class MockEvaluation(BaseModel):
    """Skeptical government-evaluator scoring against Section M."""

    class FactorScore(BaseModel):
        factor: str
        adjectival_rating: str = Field(description="e.g. Outstanding/Good/Acceptable/Marginal/Unacceptable")
        strengths: list[str] = Field(default_factory=list)
        weaknesses: list[str] = Field(default_factory=list)
        deficiencies: list[str] = Field(default_factory=list)

    factor_scores: list[FactorScore] = Field(default_factory=list)
    overall_assessment: str = ""


class QAReport(BaseModel):
    findings: list[QAFinding] = Field(default_factory=list)
    mock_evaluation: Optional[MockEvaluation] = None
    fix_iterations_used: int = 0

    def hard_failures(self) -> list[QAFinding]:
        return [f for f in self.findings if f.severity == QASeverity.HARD and not f.resolved]


# ---------------------------------------------------------------------------
# Human gates
# ---------------------------------------------------------------------------


class HumanApproval(BaseModel):
    gate: str = Field(description="bid_no_bid | assumptions | final_package")
    approved: bool
    actor: str
    timestamp: str
    notes: Optional[str] = None
