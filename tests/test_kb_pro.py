"""kb.pro must load through the real store, resolve citations, satisfy
governance freshness, and keep its pricing structure inside the researched
competitive bands (see kb.pro/MARKET_RESEARCH.md)."""

from pathlib import Path

import pytest

from bidpilot.kb.store import load_kb
from bidpilot.pricing.rates import wrap_rate

KB_PRO = Path(__file__).resolve().parent.parent / "kb.pro"


@pytest.fixture(scope="module")
def kb():
    return load_kb(str(KB_PRO))


def test_loads_and_is_populated(kb):
    assert kb.profile.name == "Meridian Federal Systems LLC"
    assert len(kb.profile.labor_categories) >= 14
    assert len(kb.data.past_performance) >= 4
    assert len(kb.data.personnel) >= 4
    assert len(kb.data.reusable_content) >= 8


def test_wrap_rate_in_competitive_band(kb):
    # Contractor-site competitive band for a DC-metro small business is
    # 2.00-2.25x; the guardrails treat <1.9 and >2.3 as out of policy.
    multiplier = wrap_rate(1.0, kb.profile.indirect_rates)
    assert 2.00 <= multiplier <= 2.25


def test_sca_categories_carry_compliance_notes(kb):
    sca = [lc for lc in kb.profile.labor_categories if "Help Desk" in lc.title]
    assert len(sca) == 2
    for lc in sca:
        assert "SCA" in (lc.description or "")
        assert "WD" in lc.description


def test_governance_is_fresh(kb):
    assert kb.stale_entries() == []


def test_all_ids_resolve_and_are_unique(kb):
    # KnowledgeBase.__init__ raises on duplicates; resolving every known id
    # proves the citation gate has a valid target for each.
    for kb_id in kb.known_ids():
        assert kb.resolve(kb_id) is not None


def test_estimating_doctrine_is_citable(kb):
    corpus = kb.citable_corpus()
    for kb_id in (
        "content-estimating-benchmarks",
        "content-pricing-policy",
        "content-sca-compliance",
        "content-boe-methodology",
    ):
        assert kb_id in corpus
    # Load-bearing researched figures the estimator/pricer cite.
    assert "113 tickets/tech/month" in corpus
    assert "$5.55" in corpus
    assert "1,880" in corpus
    assert "52.222-43" in corpus


def test_past_performance_actuals_support_analogy_estimating(kb):
    for pp in kb.data.past_performance:
        assert pp.historical_actuals, f"{pp.kb_id} lacks actuals for analogy estimating"
        assert pp.cpars_rating
        assert pp.governance.last_verified
