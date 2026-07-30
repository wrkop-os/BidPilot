"""FPDS award collector (price-to-win data path), discovery P(win) ranking,
and the web outcome endpoint."""

from pathlib import Path

import httpx

from bidpilot.discover import prescreen
from bidpilot.intake.fpds import AwardRecord, FpdsClient, parse_atom, price_position
from bidpilot.kb.store import load_kb

EXAMPLE_KB = Path(__file__).resolve().parent.parent / "kb.example"

ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:ns1="https://www.fpds.gov/FPDS">
{entries}
</feed>"""

ENTRY = """<entry><content><ns1:award>
<ns1:awardID><ns1:awardContractID><ns1:PIID>{piid}</ns1:PIID></ns1:awardContractID></ns1:awardID>
<ns1:dollarValues><ns1:obligatedAmount>{obligated}</ns1:obligatedAmount>
<ns1:baseAndAllOptionsValue>{total}</ns1:baseAndAllOptionsValue></ns1:dollarValues>
<ns1:vendor><ns1:vendorHeader><ns1:vendorName>{vendor}</ns1:vendorName></ns1:vendorHeader></ns1:vendor>
<ns1:productOrServiceInformation><ns1:principalNAICSCode ns1:description="CUSTOM PROGRAMMING">541511</ns1:principalNAICSCode></ns1:productOrServiceInformation>
<ns1:relevantContractDates><ns1:signedDate>2025-10-01 00:00:00</ns1:signedDate></ns1:relevantContractDates>
<ns1:purchaserInformation><ns1:contractingOfficeAgencyID ns1:name="DEPT OF THE ARMY">2100</ns1:contractingOfficeAgencyID></ns1:purchaserInformation>
</ns1:award></content></entry>"""


def _feed(values):
    entries = "".join(
        ENTRY.format(piid=f"W91{i:03d}", obligated=v * 0.4, total=v, vendor=f"V{i}")
        for i, v in enumerate(values)
    )
    return ATOM.format(entries=entries)


def test_parse_atom_namespace_tolerant():
    records = parse_atom(_feed([250000.0]))
    assert len(records) == 1
    r = records[0]
    assert r.piid == "W91000" and r.vendor == "V0"
    assert r.naics == "541511"
    assert r.agency == "DEPT OF THE ARMY"
    assert r.value == 250000.0          # base+options preferred over obligated


def test_price_position_percentile_and_small_sample():
    awards = [AwardRecord(base_and_options=v) for v in
              (1e6, 1.5e6, 2e6, 2.5e6, 3e6, 3.5e6, 4e6, 5e6)]
    pos = price_position(2.4e6, awards)
    assert pos["n"] == 8
    assert 0.3 <= pos["percentile"] <= 0.6
    assert "Advisory only" in pos["advisory"]
    thin = price_position(2.4e6, awards[:3])
    assert "no defensible position" in thin["advisory"]


def test_fpds_client_pagination_and_cache(tmp_path):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(dict(request.url.params))
        start = int(request.url.params.get("start", 0))
        # Page 0 has awards; page 1 is empty -> pagination stops.
        return httpx.Response(200, text=_feed([1e6, 2e6] if start == 0 else []))

    client = FpdsClient(cache_dir=tmp_path, transport=httpx.MockTransport(handler))
    awards = client.search_awards("541511", pages=3)
    assert len(awards) == 2
    assert calls[0]["q"] == 'PRINCIPAL_NAICS_CODE:"541511"'
    # Second sweep is served from cache: no new page-0 request.
    n_calls = len(calls)
    client.search_awards("541511", pages=1)
    assert len(calls) == n_calls


def test_discover_ranks_candidates_by_pwin():
    profile = load_kb(str(EXAMPLE_KB)).profile
    registered = prescreen(
        {"noticeId": "a" * 32, "title": "in-NAICS",
         "naicsCode": "541511", "typeOfSetAsideDescription": "SDVOSB Set-Aside"},
        profile,
    )
    unregistered_naics = prescreen(
        {"noticeId": "b" * 32, "title": "out-of-NAICS", "naicsCode": "541990"},
        profile,
    )
    blocked = prescreen(
        {"noticeId": "c" * 32, "title": "blocked",
         "naicsCode": "541511", "typeOfSetAsideDescription": "8(a) Set-Aside"},
        profile,
    )
    assert registered.pwin > (unregistered_naics.pwin or 0)
    assert blocked.screen == "blocked" and blocked.pwin == 0.0


def test_web_outcome_endpoint(tmp_path):
    from fastapi.testclient import TestClient

    from bidpilot.ml.pwin import load_outcomes
    from bidpilot.server import create_app

    from test_server import _fake_ctx_builder, _wait
    from test_orchestrator_e2e import NOTICE

    app = create_app(ctx_builder=_fake_ctx_builder, output_root=tmp_path)
    client = TestClient(app)
    client.post("/api/runs", json={"url": NOTICE})
    # Premature outcome (no eligibility yet) may 409; wait for eligibility.
    _wait(client, NOTICE, lambda s: s["pending_gate"] or not s["running"])
    resp = client.post(f"/api/runs/{NOTICE}/outcome", json={"outcome": "won"})
    assert resp.status_code == 200
    rows = load_outcomes(tmp_path)
    assert rows and rows[-1]["outcome"] == "won"
    assert client.post(
        f"/api/runs/{NOTICE}/outcome", json={"outcome": "maybe"}
    ).status_code == 422
    # Unblock the pipeline thread so the test run finishes cleanly.
    client.post(f"/api/runs/{NOTICE}/gate", json={"approve": False})


def test_parse_atom_rejects_dtd():
    import pytest as _pytest

    bomb = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><feed>&a;</feed>'
    with _pytest.raises(ValueError, match="DTD"):
        parse_atom(bomb)


def test_sensitive_run_files_not_listed_or_served(tmp_path):
    from fastapi.testclient import TestClient

    from bidpilot.server import create_app
    from test_server import _fake_ctx_builder, _wait
    from test_orchestrator_e2e import NOTICE

    app = create_app(ctx_builder=_fake_ctx_builder, output_root=tmp_path)
    client = TestClient(app)
    client.post("/api/runs", json={"url": NOTICE})
    status = _wait(client, NOTICE, lambda s: s["pending_gate"] or not s["running"])
    run_dir = tmp_path / NOTICE
    (run_dir / "training_capture.jsonl").write_text('{"prompt": "secret"}')
    status = client.get(f"/api/runs/{NOTICE}").json()
    assert "training_capture.jsonl" not in status["artifacts"]
    assert "audit.jsonl" not in status["artifacts"]
    assert client.get(
        f"/api/runs/{NOTICE}/files/training_capture.jsonl"
    ).status_code == 403
    client.post(f"/api/runs/{NOTICE}/gate", json={"approve": False})
