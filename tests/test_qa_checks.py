from bidpilot.kb.schema import CompanyProfile, KnowledgeBaseData, PastPerformanceRecord
from bidpilot.kb.store import KnowledgeBase
from bidpilot.models import (
    Citation,
    Claim,
    ComplianceMatrix,
    FormatConstraints,
    QASeverity,
    Requirement,
    RequirementCategory,
    RequirementStatus,
    SectionDraft,
)
from bidpilot.qa_checks import citation_check, coverage_check, format_check


def _kb() -> KnowledgeBase:
    return KnowledgeBase(
        KnowledgeBaseData(
            profile=CompanyProfile(name="Example LLC"),
            past_performance=[
                PastPerformanceRecord(kb_id="pp-1", customer="GSA", scope_narrative="Data platform work")
            ],
        )
    )


def _draft(claims=None, markdown="## Section\ntext", addressed=None):
    return SectionDraft(
        section_id="TECH-1", volume="Volume I", title="Tech",
        markdown=markdown, claims=claims or [],
        addressed_requirements=addressed or [], word_count=len(markdown.split()),
    )


def test_uncited_claim_is_hard_failure():
    drafts = [_draft(claims=[Claim(text="We delivered 14 systems for GSA.")])]
    findings = citation_check(drafts, _kb())
    assert any(f.severity == QASeverity.HARD and f.category == "fabrication" for f in findings)


def test_cited_claim_passes():
    drafts = [_draft(claims=[Claim(text="We delivered the GSA data platform.", kb_source_id="pp-1")])]
    assert citation_check(drafts, _kb()) == []


def test_claim_citing_nonexistent_kb_entry_is_hard():
    drafts = [_draft(claims=[Claim(text="We hold CMMC L2.", kb_source_id="pp-does-not-exist")])]
    findings = citation_check(drafts, _kb())
    assert any(f.severity == QASeverity.HARD for f in findings)
    assert "nonexistent" in findings[0].description


def test_needs_input_is_soft_not_hard():
    drafts = [_draft(claims=[Claim(text="[NEEDS INPUT: incumbent contract number]", needs_input=True)])]
    findings = citation_check(drafts, _kb())
    assert all(f.severity == QASeverity.SOFT for f in findings)


def _matrix():
    return ComplianceMatrix(
        requirements=[
            Requirement(
                req_id="L-1", verbatim_text="Offeror shall describe its approach.",
                source=Citation(doc="RFP"), category=RequirementCategory.CONTENT,
                owner_section="TECH-1",
            ),
            Requirement(
                req_id="L-2", verbatim_text="Offeror shall describe QC.",
                source=Citation(doc="RFP"), category=RequirementCategory.CONTENT,
                owner_section="TECH-1",
            ),
        ]
    )


def test_coverage_addressed_via_html_comment_and_list():
    matrix = _matrix()
    drafts = [_draft(markdown="## X\ntext <!-- addresses L-1 -->", addressed=["L-2"])]
    findings = coverage_check(matrix, drafts)
    assert [f for f in findings if f.severity == QASeverity.HARD] == []
    assert matrix.requirements[0].status == RequirementStatus.DRAFTED
    assert "TECH-1" in matrix.requirements[0].addressed_in


def test_coverage_unaddressed_content_req_is_hard():
    findings = coverage_check(_matrix(), [_draft()])
    hard = [f for f in findings if f.severity == QASeverity.HARD]
    assert len(hard) == 2
    assert "L-1" in hard[0].description


def test_format_check_page_limit():
    matrix = ComplianceMatrix(constraints=FormatConstraints(page_limits={"Volume I": 2}))
    long_md = "word " * 2000  # ~4.4 estimated pages
    findings = format_check(matrix, [_draft(markdown=long_md)])
    assert any(f.severity == QASeverity.HARD and f.category == "format" for f in findings)


def test_format_check_within_limit():
    matrix = ComplianceMatrix(constraints=FormatConstraints(page_limits={"Volume I": 10}))
    findings = format_check(matrix, [_draft(markdown="short draft")])
    assert findings == []
