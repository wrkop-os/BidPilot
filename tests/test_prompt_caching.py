"""Prompt caching: the shared corpus prefix.

Eleven stages read the same solicitation corpus. On a measured run that turned
34K chars of source into 468K chars on the wire. Caching removes the
repetition — but only if the cached block is byte-identical across calls, and
the failure mode is silent: a one-character difference produces a cache miss
that looks exactly like a cache hit from the outside.

These tests exist because nothing else would notice.
"""

import hashlib

import pytest

from bidpilot.prompting import CACHE_HEAD_CHARS, split_for_cache
from bidpilot.routing import MIN_CACHEABLE_CHARS, _user_content


# -- the split ----------------------------------------------------------------


def test_the_head_is_identical_regardless_of_the_agents_limit():
    """Agents cap the corpus differently (400K, 300K, 200K, 150K). If the
    cached block inherited that cap, no two agents would ever share one."""
    corpus = "x" * 500_000
    heads = {split_for_cache(corpus, limit).head
             for limit in (150_000, 200_000, 300_000, 400_000, len(corpus))}
    assert len(heads) == 1
    assert len(next(iter(heads))) == CACHE_HEAD_CHARS


def test_head_and_tail_reconstruct_exactly_what_the_agent_may_see():
    corpus = "".join(str(i % 10) for i in range(400_000))
    for limit in (150_000, 250_000, 400_000):
        piece = split_for_cache(corpus, limit)
        assert piece.head + piece.tail == corpus[:limit]


def test_a_corpus_smaller_than_the_head_has_no_tail():
    piece = split_for_cache("short corpus", 300_000)
    assert piece.head == "short corpus"
    assert piece.tail == ""
    assert piece.truncated is False


def test_truncation_is_reported_rather_than_silent():
    """An agent seeing the first 300K of a 900K solicitation is working from a
    fragment. Before this the pipeline said nothing at all."""
    piece = split_for_cache("y" * 900_000, 300_000)
    assert piece.truncated is True
    assert piece.dropped_chars == 600_000
    notice = piece.notice()
    assert "600,000" in notice and "900,000" in notice
    assert "cannot be found" in notice
    assert split_for_cache("y" * 100, 300_000).notice() is None


# -- the request the router builds -------------------------------------------


def test_a_large_prefix_becomes_its_own_cacheable_block():
    prefix = "c" * (MIN_CACHEABLE_CHARS + 10)
    content = _user_content("the question", prefix)
    assert isinstance(content, list) and len(content) == 2
    assert content[0]["text"] == prefix
    assert content[0]["cache_control"] == {"type": "ephemeral"}
    # The per-call part must NOT be marked cacheable — it differs every time,
    # and marking it would burn a cache breakpoint for nothing.
    assert "cache_control" not in content[1]
    assert content[1]["text"] == "the question"


def test_a_prefix_too_small_to_cache_is_merely_concatenated():
    """Below the provider minimum a cache block buys nothing, and the marker
    would be ignored anyway."""
    prefix = "c" * (MIN_CACHEABLE_CHARS - 1)
    content = _user_content("q", prefix)
    assert isinstance(content, str)
    assert content.startswith(prefix) and content.endswith("q")


def test_no_prefix_leaves_the_request_shape_untouched():
    assert _user_content("just a prompt", None) == "just a prompt"
    assert _user_content("just a prompt", "") == "just a prompt"


# -- the property that actually saves money ----------------------------------


def test_every_stage_sends_a_byte_identical_prefix(tmp_path, monkeypatch):
    """The whole optimization rests on this. Run the real pipeline and assert
    that every cached prefix in it hashes to the same value."""
    from rich.console import Console

    from bidpilot.audit import AuditLog
    from bidpilot.kb.store import load_kb
    from bidpilot.orchestrator import RunContext, new_run, run
    from test_orchestrator_e2e import EXAMPLE_KB, FakeRouter, FakeSam

    seen: list[tuple[str, str]] = []
    original = FakeRouter.structured

    def spy(self, tier, *, system, prompt, output_type, max_tokens=16000,
            stage=None, cache_prefix=None):
        if cache_prefix:
            seen.append((stage or "?",
                         hashlib.sha256(cache_prefix.encode()).hexdigest()))
        return original(self, tier, system=system, prompt=prompt,
                        output_type=output_type, max_tokens=max_tokens,
                        stage=stage, cache_prefix=cache_prefix)

    monkeypatch.setattr(FakeRouter, "structured", spy)

    src = tmp_path / "pkg"
    src.mkdir()
    body = ("The offeror shall submit Volume I Technical. "
            "Technical approach will be evaluated for soundness.\n") * 200
    (src / "solicitation.txt").write_text(
        "Solicitation Number: W9123-26-R-0001\nNAICS Code: 541511\n" + body)

    state, checkpoints = new_run("local:pkg", tmp_path / "runs", local_source=src)
    audit = AuditLog(tmp_path / "audit.jsonl")
    state = run(RunContext(
        state=state, router=FakeRouter(), sam=FakeSam(),
        kb=load_kb(str(EXAMPLE_KB)), checkpoints=checkpoints, audit=audit,
        console=Console(quiet=True), confirm=lambda q: True, actor="test",
        local_source=src,
    ))

    assert state.halted_reason is None, state.halted_reason
    assert len(seen) >= 5, f"only {len(seen)} stages sent a cache prefix"
    distinct = {digest for _, digest in seen}
    assert len(distinct) == 1, (
        "cached prefixes differ between stages — every call after the first is "
        f"a silent cache miss: {sorted({s for s, _ in seen})}"
    )


# -- telemetry ----------------------------------------------------------------


def test_cached_reads_are_priced_and_the_saving_is_reported(tmp_path):
    """A saving nobody can see is a saving nobody will defend when a future
    refactor breaks the prefix."""
    from bidpilot.audit import AuditLog
    from bidpilot.telemetry import compute_costs, report_markdown

    audit = AuditLog(tmp_path / "audit.jsonl")
    audit.record("llm_call", actor="model", stage="shred.adversarial",
                 model="claude-opus-5", tokens_in=1_000, tokens_out=500,
                 cache_write_tokens=40_000, cache_read_tokens=0, duration_s=1)
    for _ in range(7):
        audit.record("llm_call", actor="model", stage="produce.write",
                     model="claude-opus-5", tokens_in=1_000, tokens_out=500,
                     cache_write_tokens=0, cache_read_tokens=40_000, duration_s=1)

    cost = compute_costs(tmp_path / "audit.jsonl")
    assert cost.total.cache_read_tokens == 280_000
    assert cost.total.cache_write_tokens == 40_000
    # 280K tokens re-read at 10% of $5/M instead of 100%: $1.40 -> $0.14.
    assert cost.total.cache_saving_usd == pytest.approx(1.26, abs=0.01)

    report = report_markdown(cost)
    assert "280,000 tokens re-read" in report
    assert "saved" in report


def test_a_run_without_caching_says_so_rather_than_claiming_zero_saving(tmp_path):
    from bidpilot.audit import AuditLog
    from bidpilot.telemetry import compute_costs, report_markdown

    audit = AuditLog(tmp_path / "audit.jsonl")
    audit.record("llm_call", actor="model", stage="classify",
                 model="claude-haiku-4-5", tokens_in=500, tokens_out=100, duration_s=1)
    report = report_markdown(compute_costs(tmp_path / "audit.jsonl"))
    assert "no cached reads" in report


def test_an_oversized_corpus_is_reported_to_the_human(tmp_path):
    """Silence about truncation is the dangerous case: an eligibility answer
    drawn from the first 150K of a 900K solicitation reads exactly like one
    drawn from all of it."""
    from rich.console import Console

    from bidpilot.assembly import review_checklist
    from bidpilot.audit import AuditLog
    from bidpilot.kb.store import load_kb
    from bidpilot.orchestrator import RunContext, new_run, run
    from bidpilot.prompting import SMALLEST_STAGE_LIMIT
    from test_orchestrator_e2e import EXAMPLE_KB, FakeRouter, FakeSam

    src = tmp_path / "pkg"
    src.mkdir()
    line = "The offeror shall submit Volume I Technical for evaluation.\n"
    (src / "solicitation.txt").write_text(
        "Solicitation Number: W9123-26-R-0001\nNAICS Code: 541511\n"
        + line * (SMALLEST_STAGE_LIMIT // len(line) + 5_000))

    state, checkpoints = new_run("local:pkg", tmp_path / "runs", local_source=src)
    state = run(RunContext(
        state=state, router=FakeRouter(), sam=FakeSam(),
        kb=load_kb(str(EXAMPLE_KB)), checkpoints=checkpoints,
        audit=AuditLog(tmp_path / "audit.jsonl"), console=Console(quiet=True),
        confirm=lambda q: True, actor="test", local_source=src,
    ))

    assert state.corpus_truncation_notice
    assert "were not shown" in state.corpus_truncation_notice
    # And it reaches the checklist a human actually reads.
    assert "Corpus truncation" in review_checklist(state)


def test_a_normal_sized_corpus_reports_nothing(tmp_path):
    from rich.console import Console

    from bidpilot.assembly import review_checklist
    from bidpilot.audit import AuditLog
    from bidpilot.kb.store import load_kb
    from bidpilot.orchestrator import RunContext, new_run, run
    from test_orchestrator_e2e import EXAMPLE_KB, FakeRouter, FakeSam

    src = tmp_path / "pkg"
    src.mkdir()
    (src / "solicitation.txt").write_text(
        "Solicitation Number: W9123-26-R-0001\nNAICS Code: 541511\n"
        "The offeror shall submit Volume I Technical.\n")

    state, checkpoints = new_run("local:pkg", tmp_path / "runs", local_source=src)
    state = run(RunContext(
        state=state, router=FakeRouter(), sam=FakeSam(),
        kb=load_kb(str(EXAMPLE_KB)), checkpoints=checkpoints,
        audit=AuditLog(tmp_path / "audit.jsonl"), console=Console(quiet=True),
        confirm=lambda q: True, actor="test", local_source=src,
    ))
    assert state.corpus_truncation_notice is None
    assert "Corpus truncation" not in review_checklist(state)
