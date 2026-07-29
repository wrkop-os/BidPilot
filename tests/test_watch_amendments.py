"""Amendment watch: stale-chain detection across run directories (FR-4)."""

from pathlib import Path

from bidpilot.models import AmendmentRecord, NoticeMetadata, NoticePackage
from bidpilot.state import CheckpointStore, ProposalState
from bidpilot.watch import report_lines, watch_all


class ChainSam:
    def __init__(self, live_by_solnum):
        self.live = live_by_solnum

    def search_by_solicitation_number(self, solnum):
        return [{"noticeId": nid} for nid in self.live.get(solnum, [])]


def _seed_run(root: Path, notice_id: str, solnum, amendments=()):
    run_dir = root / notice_id
    store = CheckpointStore(run_dir)
    state = ProposalState(run_id=f"{notice_id[:8]}-x", input_url=notice_id, run_dir=str(run_dir))
    state.notice = NoticePackage(
        metadata=NoticeMetadata(notice_id=notice_id, solicitation_number=solnum),
        amendment_history=[AmendmentRecord(notice_id=a) for a in amendments],
    )
    store.save(state)
    return run_dir


def test_watch_flags_only_stale_chains(tmp_path):
    base, amend1, amend2 = "a" * 32, "b" * 32, "c" * 32
    current = "d" * 32
    _seed_run(tmp_path, base, "W9123-26-R-0044", amendments=[amend1])
    _seed_run(tmp_path, current, "FA8750-26-Q-0001", amendments=[])
    sam = ChainSam({
        "W9123-26-R-0044": [base, amend1, amend2],   # amend2 is NEW
        "FA8750-26-Q-0001": [current],               # chain current
    })
    results = watch_all(sam, tmp_path)
    by_id = {r.notice_id: r for r in results}
    assert by_id[base].stale and by_id[base].new_notice_ids == [amend2]
    assert not by_id[current].stale
    lines = report_lines(results)
    assert any("NEW" in ln and "bidpilot amend" in ln for ln in lines)
    assert any("chain current" in ln for ln in lines)


def test_watch_reports_missing_solnum_instead_of_guessing(tmp_path):
    _seed_run(tmp_path, "e" * 32, None)
    results = watch_all(ChainSam({}), tmp_path)
    assert results[0].error and "solicitation number" in results[0].error
    assert not results[0].stale


def test_watch_survives_sam_failure(tmp_path):
    class BrokenSam:
        def search_by_solicitation_number(self, solnum):
            raise RuntimeError("boom")

    _seed_run(tmp_path, "f" * 32, "X-1")
    results = watch_all(BrokenSam(), tmp_path)
    assert results[0].error == "SAM query failed: RuntimeError"
