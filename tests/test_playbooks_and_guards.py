from pathlib import Path

from bidpilot.agents.submission import sheet_to_markdown
from bidpilot.data.portal_playbooks import playbook_for, playbook_markdown
from bidpilot.models import (
    Citation,
    QAFinding,
    QASeverity,
    SectionDraft,
    SubmissionSheet,
)
from bidpilot.orchestrator import _fixable_sections
from bidpilot.state import ProposalState
from evals.harness import load_extracted_json, load_gold_csv, score_matrix

DEMO = Path(__file__).resolve().parent.parent / "evals" / "corpus_demo"


# -- portal playbooks --------------------------------------------------------


def test_playbook_exact_and_alias_matching():
    assert playbook_for("email").channel == "email"
    assert playbook_for("PIEE Solicitation Module").channel == "piee"
    assert playbook_for("Submission via GSA eBuy RFQ").channel == "ebuy"
    assert playbook_for("Unison Marketplace reverse auction").channel == "unison"
    assert playbook_for("hand-carry to Building 4").channel == "physical"
    assert playbook_for("carrier pigeon") is None
    assert playbook_for(None) is None


def test_playbook_markdown_renders_steps_and_gotchas():
    md = playbook_markdown(playbook_for("piee"))
    assert "PIEE" in md and "1." in md and "gotchas" in md.lower()


def test_submission_sheet_includes_playbook():
    sheet = SubmissionSheet(channel="email", destination="cs@agency.gov", deadline="2026-08-15 14:00")
    md = sheet_to_markdown(sheet)
    assert "Delivery playbook — Email" in md
    assert "proof of timely delivery" in md
    # Unknown channel: no playbook section, sheet still renders
    md2 = sheet_to_markdown(SubmissionSheet(channel="unknown", destination="?", deadline="?"))
    assert "Delivery playbook" not in md2


# -- fix-loop guard for human-edited sections --------------------------------


def _draft(section_id: str, human_edited: bool = False) -> SectionDraft:
    return SectionDraft(section_id=section_id, volume="V", title=section_id,
                        markdown="x", human_edited=human_edited)


def _hard(location: str) -> QAFinding:
    return QAFinding(severity=QASeverity.HARD, category="coverage",
                     description="unaddressed", location=location)


def test_fix_loop_never_redrafts_human_edited_sections(tmp_path):
    state = ProposalState(run_id="r", input_url="a" * 32, run_dir=str(tmp_path))
    state.section_drafts = [_draft("TECH-1", human_edited=True), _draft("MGMT-1")]
    findings = [_hard("TECH-1"), _hard("MGMT-1")]
    assert _fixable_sections(state, findings) == ["MGMT-1"]


# -- committed demo corpus item ----------------------------------------------


def test_demo_corpus_scores_full_recall():
    extracted = load_extracted_json(DEMO / "sample_extracted_matrix.json")
    gold = load_gold_csv(DEMO / "gold_matrix.csv")
    score = score_matrix(extracted, gold)
    assert score.gold_total == 8
    assert score.recall == 1.0 and score.passes_g2()
    assert score.extracted_total == 9  # the deliberate extra row is fine
