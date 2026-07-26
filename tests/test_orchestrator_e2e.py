"""End-to-end orchestrator test with fake LLM router + fake SAM client.

Exercises the full graph: all 10 stages, both gates + assumptions checkpoint,
per-stage artifacts, deterministic QA, and the final export ZIP — no network,
no API keys.
"""

from pathlib import Path

from rich.console import Console

from bidpilot.audit import AuditLog
from bidpilot.kb.store import load_kb
from bidpilot.models import (
    BidRecommendation,
    Citation,
    Claim,
    Classification,
    ComplianceMatrix,
    EligibilityReport,
    FarRegime,
    FormatConstraints,
    FormItem,
    FormsPackage,
    MockEvaluation,
    NoticeMetadata,
    NoticeType,
    OutlineSection,
    PastPerformanceSelection,
    ProposalOutline,
    Requirement,
    RequirementCategory,
    ResponseArtifact,
    SectionDraft,
    SubmissionSheet,
    WinStrategy,
)
from bidpilot.orchestrator import RunContext, new_run, run
from bidpilot.pricing.models import (
    EstimationMethod,
    LaborEstimate,
    LaborLine,
    ODCItem,
    PricingStructure,
    WBSTask,
)
from bidpilot.state import Stage

NOTICE = "f" * 32
EXAMPLE_KB = Path(__file__).resolve().parent.parent / "kb.example"

DESCRIPTION = """SECTION L - INSTRUCTIONS TO OFFERORS
The offeror shall submit Volume I Technical. Proposals shall be emailed to co@agency.gov.
SECTION M - EVALUATION FACTORS
Technical approach will be evaluated for soundness."""


class FakeSam:
    def notice_metadata(self, notice_id):
        return NoticeMetadata(
            notice_id=notice_id,
            solicitation_number="W9123-26-R-0001",
            title="IT Support Services",
            response_deadline="2026-08-15",
            naics_code="541511",
            description_text=DESCRIPTION,
        )

    def search_by_solicitation_number(self, solnum):
        return [{"noticeId": NOTICE, "postedDate": "2026-06-01", "title": "Base RFP"}]

    def download_attachments(self, notice_id, dest_dir):
        return []

    def entity_status(self, uei):
        return {"found": True, "registration_status": "Active", "exclusion_status": "N"}


def _requirements():
    return [
        Requirement(
            req_id="L-1",
            verbatim_text="The offeror shall submit Volume I Technical.",
            source=Citation(doc="notice", section="L"),
            category=RequirementCategory.CONTENT,
            owner_section="TECH-1",
        )
    ]


class FakeRouter:
    """Dispatches canned responses by output schema name."""

    def __init__(self):
        self.calls = []

    def structured(self, tier, *, system, prompt, output_type, max_tokens=16000, stage=None):
        self.calls.append((stage, output_type.__name__))
        name = output_type.__name__
        if name == "Classification":
            return Classification(
                notice_type=NoticeType.SOLICITATION,
                far_regime=FarRegime.PART_15,
                response_artifact=ResponseArtifact.FULL_PROPOSAL,
                confidence=0.95,
            )
        if name == "EligibilityReport":
            return EligibilityReport(
                bid_recommendation=BidRecommendation.BID, confidence=0.9, rationale="Clean."
            )
        if name == "_ExtractedRequirements":
            # Adversarial pass returns nothing new (empty requirements on 2nd+ call)
            reqs = _requirements() if stage == "shred.extract" else []
            return output_type(requirements=reqs)
        if name == "_OutlineAndConstraints":
            return output_type(
                outline=ProposalOutline(
                    volumes=["Volume I - Technical"],
                    sections=[
                        OutlineSection(
                            section_id="TECH-1", title="Technical Approach",
                            volume="Volume I - Technical", assigned_requirements=["L-1"],
                        )
                    ],
                ),
                constraints=FormatConstraints(),
                owner_assignments={"L-1": "TECH-1"},
            )
        if name == "WinStrategy":
            return WinStrategy(
                solution_summary="Agile IT support.",
                assumptions=["We self-perform all work"],
                staffing_concept="PM + engineers",
            )
        if name == "SectionDraft":
            return SectionDraft(
                section_id="TECH-1", volume="Volume I - Technical", title="Technical Approach",
                markdown="## Technical Approach\nWe delivered the GSA platform. <!-- addresses L-1 -->",
                addressed_requirements=["L-1"],
                claims=[Claim(text="We delivered the GSA platform.", kb_source_id="pp-gsa-dataplatform")],
            )
        if name == "PastPerformanceSelection":
            return PastPerformanceSelection(
                references=[PastPerformanceSelection.Reference(
                    kb_id="pp-gsa-dataplatform", relevancy_score=0.9, relevancy_narrative="Similar scope.",
                )]
            )
        if name == "PricingStructure":
            return PricingStructure()
        if name == "LaborEstimate":
            return LaborEstimate(
                wbs=[WBSTask(task_id="T1", title="Support", description="IT support")],
                lines=[LaborLine(
                    task_id="T1", labor_category="Software Engineer", hours=100,
                    method=EstimationMethod.BOTTOM_UP, rationale="100 tickets x 1 hr",
                )],
            )
        if name == "_ODCList":
            return output_type(items=[ODCItem(description="Laptops", quote_needed=True)])
        if name == "FormsPackage":
            return FormsPackage(forms=[FormItem(form_name="SF-1449", purpose="Offer", signature_required=True)])
        if name == "SubmissionSheet":
            return SubmissionSheet(
                channel="email", destination="co@agency.gov",
                deadline="2026-08-15 14:00", deadline_timezone="ET",
            )
        if name == "MockEvaluation":
            return MockEvaluation(overall_assessment="Acceptable overall.")
        if name == "_FindingList":
            return output_type(findings=[])
        raise AssertionError(f"Unexpected schema requested: {name} at stage {stage}")

    def draft(self, *, system, prompt, max_tokens=64000, stage=None):
        self.calls.append((stage, "draft"))
        return "> DRAFT — pricing lead must validate\n\nBasis of estimate narrative."


def _make_ctx(tmp_path):
    state, checkpoints = new_run(NOTICE, tmp_path)
    audit = AuditLog(Path(state.run_dir) / "audit.jsonl")
    return RunContext(
        state=state,
        router=FakeRouter(),
        sam=FakeSam(),
        kb=load_kb(str(EXAMPLE_KB)),
        checkpoints=checkpoints,
        audit=audit,
        console=Console(quiet=True),
        confirm=lambda q: True,
        actor="test",
    )


def test_full_pipeline_e2e(tmp_path):
    ctx = _make_ctx(tmp_path)
    state = run(ctx)

    assert state.halted_reason is None
    assert all(state.is_done(s) for s in Stage)

    # Artifacts
    run_dir = Path(state.run_dir)
    assert (run_dir / "ELIGIBILITY_REPORT.md").exists()
    assert (run_dir / "compliance_matrix.csv").exists()
    assert (run_dir / "SUBMISSION_INSTRUCTIONS.md").exists()
    assert (run_dir / "pricing" / "basis_of_estimate.md").exists()
    assert (run_dir / "deadlines.ics").exists()
    assert (run_dir / "QA_REPORT.md").exists()

    # Deterministic pricing: 100h Software Engineer @ $58 direct, example-KB indirects
    assert state.pricing.total > 0
    assert state.pricing.wd_violations == []
    assert state.pricing.quote_needed == ["Laptops"]

    # QA passed with no hard failures -> export happened
    assert state.qa_report.hard_failures() == []
    assert state.export_path and Path(state.export_path).exists()

    # Gates recorded in approvals + audit
    gates = {a.gate for a in state.approvals}
    assert gates == {"bid_no_bid", "assumptions", "final_package"}
    events = [e["event"] for e in ctx.audit.entries()]
    assert events.count("human_gate") == 3
    assert "export" in events

    # Matrix updated by coverage check
    assert state.matrix.requirements[0].status.value == "drafted"
    assert "TECH-1" in state.matrix.requirements[0].addressed_in


def test_no_bid_gate_stops_run(tmp_path):
    ctx = _make_ctx(tmp_path)
    ctx.confirm = lambda q: "proceed to bid" not in q  # decline the bid gate only
    state = run(ctx)
    assert state.halted_reason == "gate_declined:bid_no_bid"
    assert not state.is_done(Stage.SHRED)
    assert state.export_path is None


def test_resume_skips_completed_stages(tmp_path):
    ctx = _make_ctx(tmp_path)
    run(ctx)
    # Re-create context from disk: everything is checkpointed, nothing re-runs.
    state2, checkpoints2 = new_run(NOTICE, tmp_path)
    assert all(state2.is_done(s) for s in Stage)
    router2 = FakeRouter()
    ctx2 = RunContext(
        state=state2, router=router2, sam=FakeSam(), kb=ctx.kb,
        checkpoints=checkpoints2, audit=ctx.audit, console=Console(quiet=True),
        confirm=lambda q: True, actor="test",
    )
    run(ctx2)
    assert router2.calls == []  # no LLM work on resume


def test_export_blocked_on_hard_qa_failure(tmp_path):
    class FabricatingRouter(FakeRouter):
        def structured(self, tier, **kw):
            result = super().structured(tier, **kw)
            if kw["output_type"].__name__ == "SectionDraft":
                # Uncited company fact -> FR-10 hard failure (and it survives the
                # fix loop because this router always fabricates).
                result.claims = [Claim(text="We hold CMMC Level 3.")]
            return result

    ctx = _make_ctx(tmp_path)
    ctx.router = FabricatingRouter()
    state = run(ctx)
    assert state.halted_reason == "qa_hard_failures"
    assert state.export_path is None
    assert any(f.category == "fabrication" for f in state.qa_report.hard_failures())
