import json

from bidpilot.amendments import archive_doc_tree, diff_doc_trees, load_archived_doc_tree
from bidpilot.discover import prescreen
from bidpilot.kb.schema import CompanyProfile
from bidpilot.models import DocTree, ParsedDoc
from bidpilot.telemetry import compute_costs, report_markdown


# -- amendment diff ----------------------------------------------------------


def _tree(text_by_name: dict[str, str]) -> DocTree:
    return DocTree(docs=[ParsedDoc(name=n, kind="pdf", full_text=t) for n, t in text_by_name.items()])


def test_diff_detects_change_new_and_removed():
    old = _tree({"rfp.pdf": "Proposals due August 1.\nPage limit 20.", "old.pdf": "x"})
    new = _tree({"rfp.pdf": "Proposals due August 15.\nPage limit 20.", "amend1.pdf": "New Q&A"})
    diffs = diff_doc_trees(old, new)
    assert set(diffs) == {"rfp.pdf", "old.pdf", "amend1.pdf"}
    assert "-Proposals due August 1." in diffs["rfp.pdf"]
    assert "+Proposals due August 15." in diffs["rfp.pdf"]
    assert diffs["amend1.pdf"].startswith("[NEW DOCUMENT]")
    assert diffs["old.pdf"].startswith("[REMOVED]")


def test_diff_identical_trees_empty():
    tree = _tree({"rfp.pdf": "Same text."})
    assert diff_doc_trees(tree, tree) == {}


def test_archive_roundtrip(tmp_path):
    tree = _tree({"rfp.pdf": "text"})
    archive_doc_tree(tmp_path, tree)
    loaded = load_archived_doc_tree(tmp_path)
    assert loaded is not None and loaded.docs[0].name == "rfp.pdf"
    assert load_archived_doc_tree(tmp_path / "nowhere") is None


# -- telemetry ---------------------------------------------------------------


def test_compute_costs_by_stage_and_model(tmp_path):
    audit = tmp_path / "audit.jsonl"
    entries = [
        {"event": "llm_call", "stage": "shred.extract", "model": "claude-haiku-4-5",
         "tokens_in": 1_000_000, "tokens_out": 100_000, "duration_s": 5},
        {"event": "llm_call", "stage": "shred.adversarial", "model": "claude-opus-5",
         "tokens_in": 200_000, "tokens_out": 20_000, "duration_s": 30},
        {"event": "llm_call", "stage": "write.TECH-1", "model": "claude-opus-5",
         "tokens_in": 100_000, "tokens_out": 30_000, "duration_s": 60},
        {"event": "stage_start", "stage": "qa"},
    ]
    audit.write_text("\n".join(json.dumps(e) for e in entries), encoding="utf-8")
    costs = compute_costs(audit)
    # haiku: 1M in @ $1 + 0.1M out @ $5 = 1.50 ; opus: (0.2*5+0.02*25)+(0.1*5+0.03*25)=1.5+1.25=2.75
    assert round(costs.total.cost_usd, 2) == 4.25
    assert set(costs.by_stage) == {"shred", "write"}
    assert costs.by_stage["shred"].calls == 2
    assert costs.within_budget()
    md = report_markdown(costs)
    assert "WITHIN" in md and "claude-haiku-4-5" in md


def test_costs_over_budget(tmp_path):
    audit = tmp_path / "audit.jsonl"
    audit.write_text(json.dumps({
        "event": "llm_call", "stage": "write.x", "model": "claude-opus-5",
        "tokens_in": 10_000_000, "tokens_out": 1_000_000,
    }), encoding="utf-8")
    costs = compute_costs(audit)
    assert not costs.within_budget()
    assert "OVER" in report_markdown(costs)


# -- discovery pre-screen ----------------------------------------------------


PROFILE = CompanyProfile(
    name="X", naics_codes=["541511"], socioeconomic_certifications=["SDVOSB"],
    annual_receipts_avg=6_000_000, employee_count=40,
)


def _record(**kw):
    base = {
        "noticeId": "A" * 32, "title": "IT Services", "naicsCode": "541511",
        "postedDate": "2026-07-20", "responseDeadLine": "2026-08-20", "type": "Solicitation",
    }
    base.update(kw)
    return base


def test_prescreen_candidate():
    opp = prescreen(_record(typeOfSetAsideDescription="Total Small Business Set-Aside"), PROFILE)
    assert opp.screen == "candidate"
    assert opp.url.startswith("https://sam.gov/opp/")


def test_prescreen_blocked_missing_certification():
    opp = prescreen(_record(typeOfSetAsideDescription="8(a) Set-Aside"), PROFILE)
    assert opp.screen == "blocked"
    assert "8(a)" in opp.reasons[0]


def test_prescreen_matching_certification():
    opp = prescreen(
        _record(typeOfSetAsideDescription="Service-Disabled Veteran-Owned Small Business Set-Aside"),
        PROFILE,
    )
    assert opp.screen == "candidate"


def test_prescreen_blocked_deadline_passed():
    opp = prescreen(_record(responseDeadLine="2020-01-01"), PROFILE)
    assert opp.screen == "blocked"


def test_prescreen_review_unknown_naics():
    opp = prescreen(_record(naicsCode="999999"), PROFILE)
    assert opp.screen == "review"


def test_prescreen_blocked_other_than_small_on_set_aside():
    big = PROFILE.model_copy(update={"annual_receipts_avg": 200_000_000})
    opp = prescreen(_record(typeOfSetAsideDescription="Total Small Business Set-Aside"), big)
    assert opp.screen == "blocked"
