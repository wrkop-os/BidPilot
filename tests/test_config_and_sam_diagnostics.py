"""Local secret loading, and telling apart the ways a SAM.gov call can fail."""

import os

import httpx
import pytest

from bidpilot.config import find_dotenv, load_dotenv
from bidpilot.intake.samgov import diagnose_api_failure


# -- .env loading -------------------------------------------------------------


def test_dotenv_loads_keys_and_reports_names_never_values(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(
        "# a comment\n"
        "\n"
        "SAM_GOV_API_KEY=abc123\n"
        "export QUOTED_KEY='shh'\n"
        'DOUBLE="also-shh"\n'
        "NOT_A_PAIR\n"
    )
    monkeypatch.delenv("SAM_GOV_API_KEY", raising=False)
    monkeypatch.delenv("QUOTED_KEY", raising=False)
    monkeypatch.delenv("DOUBLE", raising=False)

    applied = load_dotenv(tmp_path)

    assert set(applied) == {"SAM_GOV_API_KEY", "QUOTED_KEY", "DOUBLE"}
    assert os.environ["SAM_GOV_API_KEY"] == "abc123"
    assert os.environ["QUOTED_KEY"] == "shh"       # quotes stripped
    assert os.environ["DOUBLE"] == "also-shh"
    # The return value is safe to print — it must never carry secrets.
    assert not any("abc123" in name or "shh" in name for name in applied)


def test_a_real_environment_variable_always_beats_the_file(tmp_path, monkeypatch):
    """CI and containers set real env vars; a stale file on disk must never
    silently override them."""
    (tmp_path / ".env").write_text("SAM_GOV_API_KEY=from-file\n")
    monkeypatch.setenv("SAM_GOV_API_KEY", "from-environment")

    applied = load_dotenv(tmp_path)

    assert os.environ["SAM_GOV_API_KEY"] == "from-environment"
    assert "SAM_GOV_API_KEY" not in applied


def test_dotenv_search_walks_up_to_the_project_root(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("FOUND_FROM_PARENT=1\n")
    nested = tmp_path / "a" / "b" / "c"
    nested.mkdir(parents=True)
    monkeypatch.delenv("FOUND_FROM_PARENT", raising=False)

    assert find_dotenv(nested) == tmp_path / ".env"
    assert load_dotenv(nested) == ["FOUND_FROM_PARENT"]


def test_missing_or_unreadable_dotenv_is_not_an_error(tmp_path):
    assert find_dotenv(tmp_path) is None
    assert load_dotenv(tmp_path) == []


# -- failure diagnosis --------------------------------------------------------


def _status_error(code: int, body: dict | None = None) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://api.sam.gov/opportunities/v2/search?api_key=SEKRIT")
    response = httpx.Response(code, json=body or {}, request=request)
    return httpx.HTTPStatusError(f"{code}", request=request, response=response)


@pytest.mark.parametrize("code", [401, 403])
def test_a_rejected_key_says_the_key_was_reached_and_rejected(code):
    msg = diagnose_api_failure(_status_error(code, {"error": {"message": "API_KEY_INVALID"}}))
    assert "REJECTED" in msg
    assert "API_KEY_INVALID" in msg
    assert "system-account key is a different" in msg   # the common mix-up


def test_rate_limit_is_not_mistaken_for_a_bad_key():
    msg = diagnose_api_failure(_status_error(429))
    assert "rate limit" in msg
    assert "the key works" in msg
    assert "REJECTED" not in msg


def test_upstream_outage_absolves_the_key():
    msg = diagnose_api_failure(_status_error(503))
    assert "the key is fine" in msg


def test_a_blocked_egress_path_is_never_reported_as_an_auth_problem(monkeypatch):
    """The failure that actually cost time: a proxy denying CONNECT looks like
    a 403, but the key was never presented."""
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:41083")
    msg = diagnose_api_failure(httpx.ProxyError("403 Forbidden"))
    assert "NOT a bad key" in msg
    assert "network policy" in msg
    assert "REJECTED" not in msg


def test_transport_failures_say_the_key_was_never_presented(monkeypatch):
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.delenv("http_proxy", raising=False)
    msg = diagnose_api_failure(httpx.ConnectError("dns failure"))
    assert "never presented" in msg
    assert diagnose_api_failure(httpx.ReadTimeout("slow")).startswith("Timed out")


def test_diagnosis_never_echoes_a_url_that_carries_the_api_key():
    """httpx puts the full request URL in its own message; repeating it would
    leak the key into logs and the web UI."""
    for exc in (_status_error(403), _status_error(429), _status_error(500),
                httpx.ProxyError("403 Forbidden"), httpx.ConnectError("boom")):
        assert "SEKRIT" not in diagnose_api_failure(exc)
        assert "api_key=" not in diagnose_api_failure(exc)
