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
    # The sweep survives, and the run is recorded as unverified rather than
    # silently counted as up to date.
    assert "RuntimeError" in results[0].error
    assert results[0].unchecked is True and results[0].stale is False


def _seed_local_run(root: Path, notice_id: str, solnum: str) -> Path:
    run_dir = root / notice_id
    store = CheckpointStore(run_dir)
    state = ProposalState(run_id=f"{notice_id[:8]}-x", input_url=f"local:{notice_id}",
                          run_dir=str(run_dir))
    state.notice = NoticePackage(
        metadata=NoticeMetadata(notice_id=notice_id, solicitation_number=solnum,
                                raw_api_record={"source": "local"}),
        amendment_history=[AmendmentRecord(notice_id=notice_id, is_latest=True)],
    )
    store.save(state)
    return run_dir


class _ExplodingChainSam:
    def search_by_solicitation_number(self, solnum):
        raise AssertionError("a local package has no SAM chain to query")


def test_a_local_package_is_never_queried_and_never_reads_as_current(tmp_path):
    """A local run's notice ID is content-derived and matches nothing on
    SAM.gov. Querying it would come back empty and look like 'no new
    amendments' — the exact false all-clear this command exists to prevent."""
    _seed_local_run(tmp_path, "e" * 32, "47QFCA26R0031")

    results = watch_all(_ExplodingChainSam(), tmp_path)

    assert len(results) == 1
    result = results[0]
    assert result.local is True
    assert result.stale is False          # nothing NEW was found...
    assert result.unchecked is True       # ...but nothing was verified either
    assert "check the portal yourself" in result.error


def test_an_unreachable_api_is_unchecked_not_up_to_date(tmp_path):
    """A cron that treats 'could not check' as 'all clear' never fires."""
    class _Blocked:
        def search_by_solicitation_number(self, solnum):
            import httpx
            raise httpx.ProxyError("403 Forbidden")

    _seed_run(tmp_path, "f" * 32, "W9123-26-R-0044")
    result = watch_all(_Blocked(), tmp_path)[0]

    assert result.unchecked is True
    assert result.stale is False
    assert "NOT a bad key" in result.error or "could not" in result.error.lower()


def test_a_verified_current_chain_is_not_marked_unchecked(tmp_path):
    current = "a" * 32
    _seed_run(tmp_path, current, "FA8750-26-Q-0001")
    result = watch_all(ChainSam({"FA8750-26-Q-0001": [current]}), tmp_path)[0]
    assert result.unchecked is False and result.stale is False
