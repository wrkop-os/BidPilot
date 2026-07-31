"""Trust Manifest (exportable proof of enforcement) and multi-source discovery."""

from pathlib import Path

import httpx

from bidpilot.discover import discover, prescreen
from bidpilot.intake.sources import GrantsGovSource, SamGovSource, search_all
from bidpilot.kb.store import load_kb
from bidpilot.trust import build_manifest, manifest_markdown

EXAMPLE_KB = Path(__file__).resolve().parent.parent / "kb.example"


# -- Trust Manifest ---------------------------------------------------------

def test_manifest_states_guarantees_and_traces_every_claim(tmp_path):
    from bidpilot.orchestrator import run
    from test_orchestrator_e2e import _make_ctx

    ctx = _make_ctx(tmp_path)
    state = run(ctx)
    manifest = build_manifest(state, Path(state.run_dir) / "audit.jsonl")

    g = manifest["guarantees"]
    assert g["never_signed"] and g["never_submitted"]
    assert g["arithmetic_computed_by_code_not_model"]
    assert g["uncited_company_claims_block_export"]

    # A package that exported has zero uncited claims, by construction.
    assert manifest["claim_provenance"]["counts"]["uncited"] == 0
    for claim in manifest["claim_provenance"]["cited"]:
        assert claim["kb_source_id"]
        assert claim["section"]

    # Priced numbers name the deterministic path that produced them.
    paths = manifest["deterministic_computation"]["paths"]
    assert "rates.py" in paths["wrapped labor rates"]
    assert "rates.py" in paths["wage-determination floors"]

    # Model calls are accounted for with prompt hashes.
    assert manifest["model_calls"]["audited"]
    assert manifest["model_calls"]["every_call_has_prompt_hash"]

    # Human gates are recorded with actor + decision.
    gates = {gate["gate"]: gate for gate in manifest["human_gates"]}
    assert "final_package" in gates and gates["final_package"]["approved"]


def test_manifest_is_written_into_the_exported_package(tmp_path):
    import zipfile

    from bidpilot.orchestrator import run
    from test_orchestrator_e2e import _make_ctx

    ctx = _make_ctx(tmp_path)
    state = run(ctx)
    run_dir = Path(state.run_dir)
    assert (run_dir / "TRUST_MANIFEST.md").exists()
    assert (run_dir / "trust_manifest.json").exists()
    with zipfile.ZipFile(state.export_path) as zf:
        names = zf.namelist()
    assert "TRUST_MANIFEST.md" in names and "trust_manifest.json" in names


def test_manifest_markdown_is_readable_and_honest():
    manifest = {
        "run_id": "r1", "notice_id": "n1",
        "guarantees": {},
        "claim_provenance": {"counts": {"cited": 3, "needs_input": 1, "uncited": 0},
                             "human_edited_sections": ["TECH-1"]},
        "deterministic_computation": {
            "paths": {"wrapped labor rates": "rates.py:wrap_rate"},
            "pricing": {"priced": True, "total": 1234.5, "line_items": 2,
                        "wd_compliant": True, "wage_determination_violations": []},
        },
        "quality_gates": {"ran": True, "total_findings": 4, "open_hard_findings": 0},
        "human_gates": [{"gate": "final_package", "approved": True,
                         "actor": "op", "timestamp": "t"}],
        "model_calls": {"audited": True, "model_calls": 9, "failed_calls": 0,
                        "models_used": ["m"], "every_call_has_prompt_hash": True},
        "disclaimer": "A human reviews, signs, and submits.",
    }
    md = manifest_markdown(manifest)
    assert "Never signed, never submitted" in md
    assert "must be 0" in md                      # the enforcement, stated
    assert "TECH-1" in md                          # human edits surfaced
    assert "human gate `final_package`: **approved**" in md
    assert "A human reviews, signs, and submits." in md


# -- Multi-source discovery -------------------------------------------------

def _grants_transport(hits):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"oppHits": hits}})

    return httpx.MockTransport(handler)


def test_grants_source_normalizes_into_the_screen_contract():
    source = GrantsGovSource(
        keywords=["cybersecurity"],
        transport=_grants_transport([
            {"id": "358123", "title": "Cyber Workforce Grant",
             "agency": "DHS", "openDate": "2026-07-01", "closeDate": "2026-09-30"},
        ]),
    )
    records = source.search([], days_back=7, limit=10)
    assert len(records) == 1
    rec = records[0]
    assert rec["noticeId"] == "358123" and rec["grant"] is True
    assert rec["source"] == "grants.gov"
    assert rec["naicsCode"] is None          # never invented
    assert "grants.gov" in rec["url"]


def test_grant_records_are_screened_as_review_not_silently_passed():
    profile = load_kb(str(EXAMPLE_KB)).profile
    grant = prescreen(
        {"noticeId": "358123", "title": "Cyber Workforce Grant", "grant": True,
         "source": "grants.gov", "responseDeadLine": "2099-01-01",
         "url": "https://www.grants.gov/x"},
        profile,
    )
    assert grant.screen == "review"
    assert grant.source == "grants.gov"
    assert any("set-aside and size-standard screens do not apply" in r
               for r in grant.reasons)
    assert grant.url == "https://www.grants.gov/x"


def test_a_dead_source_never_sinks_the_sweep():
    class Broken:
        name = "broken"

        def search(self, naics_codes, days_back, limit):
            raise RuntimeError("upstream down")

    class Fine:
        name = "fine"

        def search(self, naics_codes, days_back, limit):
            return [{"noticeId": "a" * 32, "title": "ok"}]

    records, failures = search_all([Broken(), Fine()], ["541511"], 7, 10)
    assert [r["title"] for r in records] == ["ok"]
    # The dead source is named, not swallowed: a caller that only sees an
    # empty-ish record list would report "nothing to bid on" to a contractor.
    assert len(failures) == 1
    assert failures[0][0] and failures[0][1]


def test_discover_merges_sources_and_dedups(tmp_path):
    class FakeSam:
        def search_raw(self, params):
            return {"opportunitiesData": [
                {"noticeId": "A" * 32, "title": "sam one", "naicsCode": "541511",
                 "responseDeadLine": "2099-01-01"},
                {"noticeId": "A" * 32, "title": "duplicate", "naicsCode": "541511"},
            ]}

    profile = load_kb(str(EXAMPLE_KB)).profile
    grants = GrantsGovSource(
        keywords=["software"],
        transport=_grants_transport([
            {"id": "999", "title": "grant one", "closeDate": "2099-01-01"},
        ]),
    )
    results, failures = discover(None, profile, sources=[SamGovSource(FakeSam()), grants])
    assert failures == []
    titles = [o.title for o in results]
    assert "sam one" in titles and "grant one" in titles
    assert "duplicate" not in titles            # deduped by notice id
    assert {o.source for o in results} == {"sam.gov", "grants.gov"}
