"""Reviewer edit loop: per-section files, clobber protection, sync-drafts,
redo — including the full round trip through the orchestrator."""

from pathlib import Path

from rich.console import Console

from bidpilot import cli
from bidpilot.audit import AuditLog
from bidpilot.drafts import extract_addressed, sync_drafts, write_section_files
from bidpilot.kb.store import load_kb
from bidpilot.models import Claim, SectionDraft
from bidpilot.orchestrator import RunContext, new_run, run
from bidpilot.state import CheckpointStore, ProposalState, Stage

from test_orchestrator_e2e import EXAMPLE_KB, FakeRouter, FakeSam, NOTICE


def _state(tmp_path: Path) -> ProposalState:
    state = ProposalState(run_id="r", input_url=NOTICE, run_dir=str(tmp_path))
    state.section_drafts = [
        SectionDraft(
            section_id="TECH-1", volume="Volume I", title="Approach",
            markdown="## Approach\nOriginal text. <!-- addresses L-1 -->",
            addressed_requirements=["L-1"], word_count=5,
            claims=[Claim(text="Original claim", kb_source_id="pp-gsa-dataplatform")],
        ),
        SectionDraft(
            section_id="MGMT-1", volume="Volume I", title="Management",
            markdown="## Management\nStaffing plan.", word_count=3,
        ),
    ]
    return state


def test_write_section_files_and_sidecar(tmp_path):
    state = _state(tmp_path)
    warnings = write_section_files(state)
    assert warnings == []
    sections = tmp_path / "volumes" / "sections"
    assert (sections / "TECH-1.md").read_text() == state.section_drafts[0].markdown
    assert (sections / ".written.json").exists()


def test_unsynced_human_edit_never_clobbered(tmp_path):
    state = _state(tmp_path)
    write_section_files(state)
    edited_path = tmp_path / "volumes" / "sections" / "TECH-1.md"
    edited_path.write_text("## Approach\nHUMAN EDIT.")
    # Pipeline writes again (e.g. another stage completes) — edit preserved.
    warnings = write_section_files(state)
    assert edited_path.read_text() == "## Approach\nHUMAN EDIT."
    assert len(warnings) == 1 and "unsynced human edits" in warnings[0]


def test_sync_drafts_imports_edits(tmp_path):
    state = _state(tmp_path)
    write_section_files(state)
    (tmp_path / "volumes" / "sections" / "TECH-1.md").write_text(
        "## Approach\nRewritten by reviewer. <!-- addresses L-1, L-9 -->"
    )
    result = sync_drafts(state)
    assert result.updated == ["TECH-1"] and result.unchanged == 1
    draft = state.section_drafts[0]
    assert draft.human_edited and "Rewritten by reviewer" in draft.markdown
    assert draft.addressed_requirements == ["L-1", "L-9"]
    assert draft.word_count == len(draft.markdown.split())
    # After sync, the file counts as synced: pipeline writes don't warn.
    assert write_section_files(state) == []


def test_extract_addressed_parses_lists():
    ids = extract_addressed("x <!-- addresses L-1, L-2 --> y <!-- addresses M-3 -->")
    assert ids == ["L-1", "L-2", "M-3"]


def test_full_reviewer_round_trip(tmp_path):
    """Pipeline run -> human edits a section -> sync-drafts CLI -> re-run:
    the edit lands in the re-rendered volume and re-QA'd export."""
    def make_ctx():
        state, cp = new_run(NOTICE, tmp_path)
        return RunContext(
            state=state, router=FakeRouter(), sam=FakeSam(), kb=load_kb(str(EXAMPLE_KB)),
            checkpoints=cp, audit=AuditLog(Path(state.run_dir) / "audit.jsonl"),
            console=Console(quiet=True), confirm=lambda q: True, actor="t",
        )

    s1 = run(make_ctx())
    assert s1.export_path
    section_file = Path(s1.run_dir) / "volumes" / "sections" / "TECH-1.md"
    original = section_file.read_text()
    section_file.write_text(original + "\n\nREVIEWER-ADDED PARAGRAPH.")

    rc = cli.main(["sync-drafts", NOTICE, "--out", str(tmp_path)])
    assert rc == 0

    s2 = run(make_ctx())
    assert s2.halted_reason is None and s2.export_path
    draft = next(d for d in s2.section_drafts if d.section_id == "TECH-1")
    assert draft.human_edited
    merged_volume = next(Path(s2.run_dir).glob("volumes/volume_i*.md")).read_text()
    assert "REVIEWER-ADDED PARAGRAPH." in merged_volume
    assert any(
        f.category == "citation" and "human-edited" in f.description
        for f in s2.qa_report.findings
    )
    checklist = (Path(s2.run_dir) / "REVIEW_CHECKLIST.md").read_text()
    assert "Human-edited sections" in checklist


def test_redo_cli(tmp_path):
    state = _state(tmp_path / NOTICE)
    for stage in (Stage.INTAKE, Stage.DOCPROC, Stage.QA, Stage.EXPORT):
        state.mark_done(stage)
    CheckpointStore(tmp_path / NOTICE).save(state)
    rc = cli.main(["redo", "qa", NOTICE, "--out", str(tmp_path)])
    assert rc == 0
    reloaded = CheckpointStore(tmp_path / NOTICE).load()
    assert reloaded.is_done(Stage.DOCPROC)
    assert not reloaded.is_done(Stage.QA) and not reloaded.is_done(Stage.EXPORT)


def test_sync_drafts_cli_no_drafts(tmp_path):
    state = ProposalState(run_id="r", input_url=NOTICE, run_dir=str(tmp_path / NOTICE))
    CheckpointStore(tmp_path / NOTICE).save(state)
    assert cli.main(["sync-drafts", NOTICE, "--out", str(tmp_path)]) == 1
