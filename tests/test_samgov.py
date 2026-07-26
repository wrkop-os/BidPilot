import pytest

from bidpilot.samgov import parse_notice_id, _strip_html


NOTICE = "a1b2c3d4e5f60718293a4b5c6d7e8f90"


def test_parse_bare_id():
    assert parse_notice_id(NOTICE) == NOTICE


def test_parse_bare_id_uppercase():
    assert parse_notice_id(NOTICE.upper()) == NOTICE


def test_parse_public_url():
    assert parse_notice_id(f"https://sam.gov/opp/{NOTICE}/view") == NOTICE


def test_parse_workspace_url():
    assert parse_notice_id(f"https://sam.gov/workspace/contract/opp/{NOTICE}/view") == NOTICE


def test_parse_invalid_raises():
    with pytest.raises(ValueError):
        parse_notice_id("https://sam.gov/search?keywords=janitorial")


def test_strip_html():
    html = "<p>Hello<br/>world</p><p>Second &amp; para</p>"
    text = _strip_html(html)
    assert "Hello" in text and "world" in text
    assert "<p>" not in text
