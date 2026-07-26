from pathlib import Path

from bidpilot.agents.pdf_forms import fill_pdf, list_form_fields
from bidpilot.dashboard import render_dashboard
from bidpilot.models import (
    BidRecommendation,
    Citation,
    Claim,
    ComplianceMatrix,
    EligibilityReport,
    HumanApproval,
    MockEvaluation,
    NoticeMetadata,
    NoticePackage,
    QAFinding,
    QAReport,
    QASeverity,
    Requirement,
    RequirementCategory,
    SectionDraft,
)
from bidpilot.state import ProposalState, Stage


def _make_fillable_pdf(path: Path):
    """Build a minimal fillable PDF with pypdf (text field 'CompanyName')."""
    from pypdf import PdfWriter
    from pypdf.generic import (
        ArrayObject,
        BooleanObject,
        DictionaryObject,
        FloatObject,
        NameObject,
        TextStringObject,
    )

    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    page = writer.pages[0]
    field = DictionaryObject()
    field.update({
        NameObject("/FT"): NameObject("/Tx"),
        NameObject("/T"): TextStringObject("CompanyName"),
        NameObject("/V"): TextStringObject(""),
        NameObject("/Type"): NameObject("/Annot"),
        NameObject("/Subtype"): NameObject("/Widget"),
        NameObject("/Rect"): ArrayObject([FloatObject(50), FloatObject(700), FloatObject(300), FloatObject(720)]),
    })
    field_ref = writer._add_object(field)
    page[NameObject("/Annots")] = ArrayObject([field_ref])
    writer._root_object[NameObject("/AcroForm")] = DictionaryObject({
        NameObject("/Fields"): ArrayObject([field_ref]),
        NameObject("/NeedAppearances"): BooleanObject(True),
    })
    with open(path, "wb") as fh:
        writer.write(fh)


def test_list_and_fill_pdf_fields(tmp_path):
    pdf = tmp_path / "sf1449.pdf"
    _make_fillable_pdf(pdf)
    fields = list_form_fields(pdf)
    assert fields == ["CompanyName"]

    dest = tmp_path / "PREFILLED_sf1449.pdf"
    filled = fill_pdf(pdf, {"CompanyName": "Example Federal Solutions LLC"}, dest)
    assert filled == dest and dest.exists()

    from pypdf import PdfReader

    values = PdfReader(str(dest)).get_fields()
    assert values["CompanyName"].get("/V") == "Example Federal Solutions LLC"


def test_list_fields_none_for_flat_pdf(tmp_path):
    from pypdf import PdfWriter

    pdf = tmp_path / "flat.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    with open(pdf, "wb") as fh:
        writer.write(fh)
    assert list_form_fields(pdf) is None


def test_fill_pdf_empty_mapping_returns_none(tmp_path):
    pdf = tmp_path / "x.pdf"
    _make_fillable_pdf(pdf)
    assert fill_pdf(pdf, {}, tmp_path / "out.pdf") is None


def test_dashboard_renders_full_state(tmp_path):
    state = ProposalState(run_id="r1", input_url="a" * 32, run_dir=str(tmp_path))
    state.notice = NoticePackage(metadata=NoticeMetadata(notice_id="a" * 32, title="IT Support <Services>"))
    state.eligibility = EligibilityReport(
        bid_recommendation=BidRecommendation.BID, rationale="Clean.", confidence=0.9,
        soft_risks=["Thin past performance"],
    )
    state.matrix = ComplianceMatrix(requirements=[
        Requirement(req_id="L-1", verbatim_text="Shall submit <Volume I>.",
                    source=Citation(doc="RFP", page=3), category=RequirementCategory.CONTENT),
    ])
    state.section_drafts = [SectionDraft(
        section_id="TECH-1", volume="Vol I", title="T", markdown="x",
        claims=[Claim(text="GSA work", kb_source_id="pp-1"),
                Claim(text="[NEEDS INPUT]", needs_input=True, input_note="incumbent value")],
    )]
    state.qa_report = QAReport(
        findings=[QAFinding(severity=QASeverity.SOFT, category="coverage", description="check L-1")],
        mock_evaluation=MockEvaluation(overall_assessment="Good."),
    )
    state.approvals = [HumanApproval(gate="bid_no_bid", approved=True, actor="jr", timestamp="2026-07-26T00:00:00Z")]
    state.completed_stages = [Stage.INTAKE]

    html = render_dashboard(state)
    assert "IT Support &lt;Services&gt;" in html          # escaping
    assert "Shall submit &lt;Volume I&gt;." in html
    assert "incumbent value" in html
    assert "bid_no_bid" in html
    assert "Mock evaluation" in html
    assert html.startswith("<!doctype html>")
