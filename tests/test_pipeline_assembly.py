from pathlib import Path

from bidpilot.models import (
    EligibilityReport,
    EligibilityVerdict,
    RawOpportunity,
    SolicitationAnalysis,
    SubmissionInstructions,
)
from bidpilot.pipeline import PipelineResult, assemble_package, build_review_checklist, _slug


def _result() -> PipelineResult:
    return PipelineResult(
        opportunity=RawOpportunity(
            notice_id="a" * 32,
            title="IT Support Services",
            solicitation_number="W91234-26-R-0001",
            response_deadline="2026-08-15",
        ),
        analysis=SolicitationAnalysis(
            summary="IT support services procurement.",
            ambiguities_and_risks=["Page limit stated twice with different values"],
        ),
        eligibility=EligibilityReport(
            verdict=EligibilityVerdict.NEEDS_HUMAN_REVIEW,
            rationale="Size standard unclear.",
            human_review_items=["Verify revenue against $34M size standard"],
        ),
        submission=SubmissionInstructions(
            method="email",
            destination="ko@army.mil",
            deadline="2026-08-15 10:00 ET",
        ),
    )


def test_review_checklist_content():
    md = build_review_checklist(_result())
    assert "IT Support Services" in md
    assert "needs_human_review" in md
    assert "Verify revenue against $34M size standard" in md
    assert "ko@army.mil" in md
    assert "Page limit stated twice" in md
    assert "BidPilot never submits" in md


def test_assemble_package_writes_manifest(tmp_path: Path):
    result = _result()
    (tmp_path / "volumes").mkdir()
    (tmp_path / "volumes" / "technical.md").write_text("draft")
    manifest = assemble_package(tmp_path, result)
    assert manifest.notice_id == "a" * 32
    assert "volumes/technical.md" in manifest.files
    assert (tmp_path / "manifest.json").exists()
    assert (tmp_path / "REVIEW_CHECKLIST.md").exists()
    assert manifest.human_review_required is True


def test_slug():
    assert _slug("Volume I - Technical Approach") == "volume_i_technical_approach"
    assert _slug("///") == "volume"
