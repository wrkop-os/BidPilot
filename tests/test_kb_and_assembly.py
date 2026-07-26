from pathlib import Path

import pytest

from bidpilot.assembly import review_checklist, write_stage_artifacts
from bidpilot.kb.schema import (
    CompanyProfile,
    Governance,
    KnowledgeBaseData,
    PastPerformanceRecord,
)
from bidpilot.kb.store import KnowledgeBase, load_kb
from bidpilot.models import (
    BidRecommendation,
    Claim,
    EligibilityReport,
    SectionDraft,
)
from bidpilot.state import ProposalState

EXAMPLE_KB = Path(__file__).resolve().parent.parent / "kb.example"


def test_example_kb_loads_and_resolves():
    kb = load_kb(str(EXAMPLE_KB))
    assert kb.profile.name == "Example Federal Solutions LLC"
    assert kb.resolve("pp-gsa-dataplatform") is not None
    assert kb.resolve("nonexistent") is None
    assert "profile" in kb.known_ids()
    assert kb.direct_rates()["Program Manager"] == 82.00
    assert kb.profile.indirect_rates is not None


def test_kb_duplicate_id_rejected():
    with pytest.raises(ValueError):
        KnowledgeBase(
            KnowledgeBaseData(
                profile=CompanyProfile(name="X"),
                past_performance=[
                    PastPerformanceRecord(kb_id="dup", customer="A", scope_narrative="a"),
                    PastPerformanceRecord(kb_id="dup", customer="B", scope_narrative="b"),
                ],
            )
        )


def test_kb_stale_entries():
    kb = KnowledgeBase(
        KnowledgeBaseData(
            profile=CompanyProfile(name="X", governance=Governance(last_verified="2020-01-01")),
            past_performance=[PastPerformanceRecord(kb_id="pp", customer="A", scope_narrative="a")],
        )
    )
    stale = kb.stale_entries()
    assert any("profile" in s for s in stale)
    assert any("pp: never verified" in s for s in stale)


def test_write_stage_artifacts_and_checklist(tmp_path):
    state = ProposalState(run_id="r", input_url="a" * 32, run_dir=str(tmp_path))
    state.eligibility = EligibilityReport(
        bid_recommendation=BidRecommendation.CONDITIONAL,
        rationale="Size standard needs verification.",
        missing_info=["Verify receipts against $34M standard"],
    )
    state.section_drafts = [
        SectionDraft(
            section_id="TECH-1", volume="Volume I - Technical", title="Approach",
            markdown="## Approach\n[NEEDS INPUT: incumbent contract number]",
            claims=[Claim(text="[NEEDS INPUT]", needs_input=True, input_note="incumbent contract number")],
            word_count=5,
        )
    ]
    write_stage_artifacts(state)
    assert (tmp_path / "ELIGIBILITY_REPORT.md").exists()
    assert (tmp_path / "volumes" / "volume_i_technical.md").exists()
    assert (tmp_path / "volumes" / "claims_source_map.json").exists()
    checklist = review_checklist(state)
    assert "conditional" in checklist
    assert "incumbent contract number" in checklist
    assert "never signs or submits" in checklist
    assert (tmp_path / "REVIEW_CHECKLIST.md").exists()
