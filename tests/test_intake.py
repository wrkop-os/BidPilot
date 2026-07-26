import pytest

from bidpilot.intake import order_amendment_chain, parse_notice_id

NOTICE = "a1b2c3d4e5f60718293a4b5c6d7e8f90"


def test_parse_bare_and_urls():
    assert parse_notice_id(NOTICE) == NOTICE
    assert parse_notice_id(NOTICE.upper()) == NOTICE
    assert parse_notice_id(f"https://sam.gov/opp/{NOTICE}/view") == NOTICE
    assert parse_notice_id(f"https://sam.gov/workspace/contract/opp/{NOTICE}/view") == NOTICE


def test_parse_invalid_raises():
    with pytest.raises(ValueError):
        parse_notice_id("https://sam.gov/search?keywords=janitorial")


def test_amendment_chain_ordering_marks_latest():
    records = [
        {"noticeId": "B" * 32, "postedDate": "2026-03-10", "title": "Amendment 1"},
        {"noticeId": "A" * 32, "postedDate": "2026-02-01", "title": "Base RFP"},
        {"noticeId": "C" * 32, "postedDate": "2026-04-02", "title": "Amendment 2"},
    ]
    chain = order_amendment_chain(records)
    assert [c.title for c in chain] == ["Base RFP", "Amendment 1", "Amendment 2"]
    assert [c.is_latest for c in chain] == [False, False, True]
    assert chain[2].notice_id == "c" * 32


def test_amendment_chain_empty():
    assert order_amendment_chain([]) == []
