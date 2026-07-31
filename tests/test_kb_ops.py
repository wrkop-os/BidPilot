"""Knowledge ops: mining runs for KB gaps, and KB health checks."""

import json
from pathlib import Path

from bidpilot.kb.ops import (
    KBGap,
    gaps_markdown,
    health,
    health_markdown,
    mine_gaps,
    write_reports,
)
from bidpilot.kb.schema import (
    CompanyProfile,
    Governance,
    KnowledgeBaseData,
    PastPerformanceRecord,
    PersonnelRecord,
    ReusableContent,
)
from bidpilot.kb.store import KnowledgeBase, load_kb

EXAMPLE_KB = Path(__file__).resolve().parent.parent / "kb.example"
KB_PRO = Path(__file__).resolve().parent.parent / "kb.pro"


def _run(root: Path, run_id: str, claims: list[dict]) -> None:
    run_dir = root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "state.json").write_text(json.dumps({
        "run_id": run_id,
        "section_drafts": [{"section_id": "TECH-1", "claims": claims}],
    }))


def test_gaps_cluster_across_runs_and_rank_uncited_highest(tmp_path):
    ask = {"text": "Incumbent contract number and current staffing count",
           "needs_input": True}
    near_dup = {"text": "The incumbent contract number plus current staffing count",
                "needs_input": True}
    uncited = {"text": "We hold an active ISO 9001 certification"}
    cited = {"text": "SDVOSB certified", "kb_source_id": "profile"}

    _run(tmp_path, "a" * 32, [ask, cited])
    _run(tmp_path, "b" * 32, [near_dup])
    _run(tmp_path, "c" * 32, [uncited])

    gaps = mine_gaps(tmp_path)
    # Cited claims are not gaps.
    assert all("SDVOSB" not in g.detail for g in gaps)
    # The uncited claim (a hard, export-blocking failure) ranks first.
    assert gaps[0].kind == "uncited" and gaps[0].priority == "HIGH"
    # The two phrasings of one missing fact clustered into a single item.
    needs = [g for g in gaps if g.kind == "needs_input"]
    assert len(needs) == 1
    assert needs[0].count == 2 and len(needs[0].runs) == 2
    assert needs[0].priority == "MEDIUM"


def test_gap_report_is_actionable_and_empty_case_is_honest(tmp_path):
    assert "No gaps found" in gaps_markdown([])
    md = gaps_markdown([KBGap("uncited", "We are CMMC Level 2 certified", 3,
                              ["r1", "r2", "r3"], ["TECH-1"])])
    assert "HIGH" in md and "CMMC Level 2" in md
    assert "blocks export" in md


def test_health_flags_duplicates_staleness_and_thin_records():
    gov_fresh = Governance(owner="o", last_verified="2999-01-01")
    data = KnowledgeBaseData(
        profile=CompanyProfile(name="Co", governance=gov_fresh),
        past_performance=[
            PastPerformanceRecord(
                kb_id="pp-one", customer="GSA", governance=gov_fresh,
                scope_narrative="Data platform modernization migrating fourteen "
                                "legacy reporting systems onto a consolidated "
                                "analytics platform with ingestion pipelines.",
                historical_actuals="11,000 hours",
            ),
            PastPerformanceRecord(  # near-duplicate of pp-one, and no actuals
                kb_id="pp-two", customer="GSA", governance=gov_fresh,
                scope_narrative="Data platform modernization migrating fourteen "
                                "legacy reporting systems onto a consolidated "
                                "analytics platform with ingestion pipelines.",
            ),
            PastPerformanceRecord(  # thin + stale (no last_verified)
                kb_id="pp-thin", customer="VA", scope_narrative="Did work.",
            ),
        ],
        personnel=[PersonnelRecord(kb_id="person-a", name="A", role="PM",
                                   resume_summary="Short.", governance=gov_fresh)],
        reusable_content=[ReusableContent(
            kb_id="content-x", title="Quality", kind="quality_plan",
            text="See pp-missing for the proof point.", governance=gov_fresh)],
    )
    report = health(KnowledgeBase(data))

    assert any(a == "pp-one" and b == "pp-two" for a, b, _ in report.duplicates)
    assert any("pp-thin" in s for s in report.stale)
    assert any("pp-thin" in t for t in report.thin)
    assert any("person-a" in t for t in report.thin)
    assert any("pp-missing" in r for r in report.broken_refs)
    assert any("pp-two" in u for u in report.unusable_for_analogy)
    assert any("indirect rates" in t for t in report.thin)
    assert report.issues >= 6


def test_shipped_kbs_are_healthy():
    """kb.pro is the template contractors copy — it must model good practice."""
    report = health(load_kb(str(KB_PRO)))
    assert report.duplicates == []
    assert report.broken_refs == []
    assert report.unusable_for_analogy == []   # every pp record supports analogy
    assert report.stale == []


def test_health_markdown_explains_why_each_issue_matters():
    report = health(load_kb(str(EXAMPLE_KB)))
    md = health_markdown(report, load_kb(str(EXAMPLE_KB)))
    assert "KB health report" in md
    if report.issues:
        assert "human-owned" in md          # never auto-fixed


def test_write_reports_emits_both_files(tmp_path):
    _run(tmp_path, "d" * 32, [{"text": "missing fact", "needs_input": True}])
    dest = tmp_path / "reports"
    paths = write_reports(tmp_path, load_kb(str(EXAMPLE_KB)), dest)
    assert len(paths) == 2 and all(p.exists() for p in paths)
    assert "missing fact" in (dest.glob("KB_GAPS_*.md").__next__()).read_text()


def test_bare_markers_resolve_to_the_missing_fact_not_the_marker(tmp_path):
    """A gap that reads '[NEEDS INPUT]' is useless; the real description lives
    inside the marker or in the QA finding that reported it."""
    run_dir = tmp_path / ("e" * 32)
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text(json.dumps({
        "run_id": "e-1",
        "section_drafts": [{"section_id": "TECH-1",
                            "claims": [{"text": "[NEEDS INPUT]", "needs_input": True}]}],
        "qa_report": {"findings": [
            {"category": "citation", "severity": "soft", "location": "TECH-1",
             "description": "[NEEDS INPUT] outstanding: Incumbent contract number "
                            "+ staffing count for transition plan"},
            {"category": "citation", "severity": "soft", "location": "TECH-1",
             "description": "Unresolved marker in prose: [NEEDS INPUT: incumbent "
                            "contract number and current staffing count for the "
                            "transition plan]"},
            {"category": "coverage", "severity": "soft", "description": "ignored"},
        ]},
    }))
    gaps = mine_gaps(tmp_path)
    assert len(gaps) == 1, [g.detail for g in gaps]
    gap = gaps[0]
    assert "Incumbent contract number" in gap.detail
    assert "[NEEDS INPUT]" not in gap.detail      # the marker never survives
    assert "Unresolved marker" not in gap.detail  # nor the QA phrasing noise
    assert gap.count == 2                          # both phrasings clustered
