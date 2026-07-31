import pytest

from bidpilot.pricing.models import EstimationMethod, LaborEstimate, LaborLine
from bidpilot.pricing.rates import (
    IndirectRateStructure,
    WageDetermination,
    WageDeterminationEntry,
    escalate,
    price_estimate,
    sensitivity,
    total_of,
    wrap_rate,
)

RATES = IndirectRateStructure(fringe=0.30, overhead=0.20, gna=0.10, fee=0.08, escalation_per_year=0.03)


def _line(category="Software Engineer", hours=100.0):
    return LaborLine(
        task_id="T1", labor_category=category, hours=hours,
        method=EstimationMethod.BOTTOM_UP, rationale="test",
    )


def test_wrap_rate_compounds_sequentially():
    # 100 * 1.30 * 1.20 * 1.10 * 1.08 = 185.328 -> 185.33
    assert wrap_rate(100.0, RATES) == pytest.approx(185.33)


def test_escalation():
    assert escalate(100.0, RATES, 0) == 100.0
    assert escalate(100.0, RATES, 2) == pytest.approx(106.09)


def test_price_estimate_base_year_only():
    estimate = LaborEstimate(lines=[_line(hours=100)])
    priced, violations, unresolved = price_estimate(estimate, {"Software Engineer": 58.0}, RATES)
    assert len(priced) == 1
    assert violations == [] and unresolved == []
    line = priced[0]
    assert line.wrapped_rate == pytest.approx(wrap_rate(58.0, RATES))
    assert line.extended == pytest.approx(100 * line.wrapped_rate)
    assert total_of(priced) == line.extended


def test_price_estimate_option_years():
    estimate = LaborEstimate(lines=[_line(hours=10)])
    priced, _, _ = price_estimate(estimate, {"Software Engineer": 58.0}, RATES, option_years=2)
    assert len(priced) == 3  # base + 2 option years
    assert priced[1].direct_rate > priced[0].direct_rate  # escalated


def test_wd_violation_is_hard_error():
    wd = WageDetermination(entries=[WageDeterminationEntry(
        labor_category="Help Desk Technician", minimum_wage=22.50, health_welfare=4.98,
    )])
    estimate = LaborEstimate(lines=[_line(category="Help Desk Technician", hours=50)])
    priced, violations, _ = price_estimate(
        estimate, {"Help Desk Technician": 24.00}, RATES, wage_determination=wd,
    )
    # floor = 27.48 > 24.00 direct -> violation
    assert len(violations) == 1
    assert "27.48" in violations[0].detail
    assert priced[0].wd_compliant is False


def test_wd_compliant_when_above_floor():
    wd = WageDetermination(entries=[WageDeterminationEntry(
        labor_category="Software Engineer", minimum_wage=40.0, health_welfare=5.0,
    )])
    estimate = LaborEstimate(lines=[_line(hours=10)])
    _, violations, _ = price_estimate(estimate, {"Software Engineer": 58.0}, RATES, wage_determination=wd)
    assert violations == []


def test_wd_fuzzy_category_match():
    wd = WageDetermination(entries=[WageDeterminationEntry(
        labor_category="Systems Administrator", minimum_wage=30.0,
    )])
    assert wd.floor_for("Sr. Systems Administrator") is not None
    assert wd.floor_for("Janitor") is None


def test_unresolved_labor_category_flagged():
    estimate = LaborEstimate(lines=[_line(category="Quantum Consultant")])
    priced, _, unresolved = price_estimate(estimate, {"Software Engineer": 58.0}, RATES)
    assert priced == []
    assert unresolved == ["Quantum Consultant"]


def _mixed_estimate():
    """One SCA-covered category and one exempt professional category."""
    return LaborEstimate(lines=[
        _line(category="Help Desk Technician", hours=100),
        _line(category="Software Engineer", hours=100),
    ])


_MIXED_WD = WageDetermination(entries=[WageDeterminationEntry(
    labor_category="Help Desk Technician", minimum_wage=22.50, health_welfare=4.98,
)])
_MIXED_RATES = {"Help Desk Technician": 30.00, "Software Engineer": 58.00}


def test_sca_covered_categories_hold_flat_under_52_222_43():
    """FAR 52.222-43(b) warrants the price carries no contingency for SCA wage
    increases — the clause itself is the adjustment mechanism, so escalating a
    WD-covered category breaches the warranty the offeror just signed."""
    priced, violations, _ = price_estimate(
        _mixed_estimate(), _MIXED_RATES, RATES,
        wage_determination=_MIXED_WD, option_years=2,
        sca_price_adjustment=True,
    )
    sca = [p for p in priced if p.labor_category == "Help Desk Technician"]
    exempt = [p for p in priced if p.labor_category == "Software Engineer"]

    assert {p.direct_rate for p in sca} == {30.00}          # flat across all years
    assert len({p.direct_rate for p in exempt}) == 3        # professional staff escalate
    assert exempt[2].direct_rate > exempt[0].direct_rate
    assert violations == []                                  # still above the floor


def test_sca_categories_escalate_when_the_clause_is_absent():
    """No 52.222-43/-44 means no adjustment mechanism, so escalation is the
    only way to cover future wage increases — and is expected."""
    priced, _, _ = price_estimate(
        _mixed_estimate(), _MIXED_RATES, RATES,
        wage_determination=_MIXED_WD, option_years=2,
    )
    sca = [p for p in priced if p.labor_category == "Help Desk Technician"]
    assert len({p.direct_rate for p in sca}) == 3


def test_sca_flat_pricing_still_enforces_the_wd_floor():
    """Holding flat must never become a way to sneak under the floor."""
    priced, violations, _ = price_estimate(
        LaborEstimate(lines=[_line(category="Help Desk Technician", hours=10)]),
        {"Help Desk Technician": 24.00}, RATES,
        wage_determination=_MIXED_WD, option_years=1,
        sca_price_adjustment=True,
    )
    assert len(violations) == 2                     # one per year, both below 27.48
    assert all(p.wd_compliant is False for p in priced)


def test_sensitivity_points():
    estimate = LaborEstimate(lines=[_line(hours=100)])
    priced, _, _ = price_estimate(estimate, {"Software Engineer": 58.0}, RATES)
    points = sensitivity(priced)
    assert [p.label for p in points] == ["baseline", "hours -10%", "hours +10%"]
    assert points[1].total < points[0].total < points[2].total
