"""ProposalState — the shared blackboard (PRD §6.2.1) — plus checkpointing.

A single typed state object flows through the graph. Every stage persists
the full state to the run directory so runs are resumable, replayable, and
auditable (FR-21), and amendments can invalidate downstream stages for
partial re-runs (FR-4).
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from .models import (
    Classification,
    ComplianceMatrix,
    DocTree,
    EligibilityReport,
    FormsPackage,
    HumanApproval,
    NoticePackage,
    PastPerformanceSelection,
    QAReport,
    RenderedVolumeInfo,
    SectionDraft,
    SubmissionSheet,
    WinStrategy,
)
from .pricing.models import PricingModel


class Stage(str, Enum):
    """Pipeline stages in execution order (PRD §6.1)."""

    INTAKE = "intake"
    DOCPROC = "docproc"
    CLASSIFY = "classify"
    ELIGIBILITY = "eligibility"
    SHRED = "shred"
    STRATEGY = "strategy"
    PRODUCE = "produce"
    ASSEMBLE = "assemble"
    QA = "qa"
    EXPORT = "export"


STAGE_ORDER: list[Stage] = list(Stage)


def stages_from(stage: Stage) -> list[Stage]:
    idx = STAGE_ORDER.index(stage)
    return STAGE_ORDER[idx:]


class ProposalState(BaseModel):
    run_id: str
    input_url: str
    run_dir: str

    # Stage outputs (each written by exactly one stage)
    notice: Optional[NoticePackage] = None
    doc_tree: Optional[DocTree] = None
    classification: Optional[Classification] = None
    eligibility: Optional[EligibilityReport] = None
    matrix: Optional[ComplianceMatrix] = None
    win_strategy: Optional[WinStrategy] = None
    section_drafts: list[SectionDraft] = Field(default_factory=list)
    past_performance: Optional[PastPerformanceSelection] = None
    pricing: Optional[PricingModel] = None
    forms: Optional[FormsPackage] = None
    submission_sheet: Optional[SubmissionSheet] = None
    rendered_volumes: list[RenderedVolumeInfo] = Field(default_factory=list)
    qa_report: Optional[QAReport] = None

    # Control
    completed_stages: list[Stage] = Field(default_factory=list)
    approvals: list[HumanApproval] = Field(default_factory=list)
    halted_reason: Optional[str] = Field(
        default=None, description="e.g. CUI/ITAR halt path, no-bid decision"
    )
    export_path: Optional[str] = None

    # -- control helpers -------------------------------------------------------

    def is_done(self, stage: Stage) -> bool:
        return stage in self.completed_stages

    def mark_done(self, stage: Stage) -> None:
        if stage not in self.completed_stages:
            self.completed_stages.append(stage)

    def invalidate_from(self, stage: Stage) -> list[Stage]:
        """Amendment lifecycle: invalidate this stage and everything after it."""
        invalidated = [s for s in stages_from(stage) if s in self.completed_stages]
        self.completed_stages = [s for s in self.completed_stages if s not in invalidated]
        return invalidated

    def approval_for(self, gate: str) -> Optional[HumanApproval]:
        for approval in reversed(self.approvals):
            if approval.gate == gate:
                return approval
        return None


class CheckpointStore:
    """File-backed checkpoints: state.json snapshot after every stage.

    (Production target per PRD §11 is Postgres; the interface is the same.)
    """

    def __init__(self, run_dir: Path):
        self.run_dir = run_dir
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = run_dir / "state.json"

    def save(self, state: ProposalState) -> None:
        tmp = self.state_path.with_suffix(".json.tmp")
        tmp.write_text(state.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(self.state_path)

    def load(self) -> Optional[ProposalState]:
        if not self.state_path.exists():
            return None
        return ProposalState.model_validate_json(self.state_path.read_text(encoding="utf-8"))
