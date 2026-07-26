from pathlib import Path

from bidpilot.data import sba_size_standards
from bidpilot.data.clause_patterns import scan_clauses
from bidpilot.state import CheckpointStore, ProposalState, Stage


def _state(tmp_path: Path) -> ProposalState:
    return ProposalState(run_id="r1", input_url="a" * 32, run_dir=str(tmp_path))


def test_checkpoint_roundtrip(tmp_path):
    store = CheckpointStore(tmp_path)
    state = _state(tmp_path)
    state.mark_done(Stage.INTAKE)
    store.save(state)
    loaded = store.load()
    assert loaded is not None
    assert loaded.is_done(Stage.INTAKE)
    assert not loaded.is_done(Stage.DOCPROC)


def test_invalidate_from_amendment(tmp_path):
    state = _state(tmp_path)
    for stage in (Stage.INTAKE, Stage.DOCPROC, Stage.CLASSIFY, Stage.ELIGIBILITY):
        state.mark_done(stage)
    invalidated = state.invalidate_from(Stage.DOCPROC)
    assert Stage.DOCPROC in invalidated and Stage.ELIGIBILITY in invalidated
    assert state.is_done(Stage.INTAKE)
    assert not state.is_done(Stage.DOCPROC)


def test_size_standard_lookup_small():
    assert sba_size_standards.is_small("541511", annual_receipts_avg=6_200_000, employee_count=38) is True
    assert sba_size_standards.is_small("541511", annual_receipts_avg=40_000_000, employee_count=38) is False


def test_size_standard_unknown_naics_returns_none():
    assert sba_size_standards.is_small("999999", 1_000_000, 5) is None
    assert sba_size_standards.lookup("999999") is None


def test_size_standard_employee_based():
    assert sba_size_standards.is_small("541715", None, 800) is True
    assert sba_size_standards.is_small("541715", None, 2000) is False
    assert sba_size_standards.is_small("541715", 1_000_000, None) is None


def test_clause_scanner_detects_set_aside_and_wd():
    text = (
        "This acquisition is set aside per FAR 52.219-27. FAR 52.222-41 Service "
        "Contract Labor Standards applies; see the attached Wage Determination "
        "2015-4281. Contractor must comply with DFARS 252.204-7012."
    )
    hits = {h.clause for h in scan_clauses(text)}
    assert "FAR 52.219-27" in hits
    assert "Wage Determination" in hits
    assert "DFARS 252.204-7012" in hits


def test_clause_scanner_ai_disclosure():
    text = "Offerors shall disclose any use of generative AI in proposal preparation."
    assert any(h.clause == "AI-use disclosure" for h in scan_clauses(text))


def test_clause_scanner_clean_text():
    assert scan_clauses("The quick brown fox.") == []
