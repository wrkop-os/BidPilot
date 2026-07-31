"""Margin erosion on SCA labor under FAR 52.222-43(d).

Holding covered categories flat is legally required. These tests pin down what
that costs, because "plan for it in fee" is only actionable with a number.
"""

import pytest

from bidpilot.pricing.compliance import PricingContext, check
from bidpilot.pricing.models import PricedLine
from bidpilot.pricing.rates import IndirectRateStructure, wrap_rate
from bidpilot.pricing.sca_erosion import (
    STATUTORY_BURDEN_RATE,
    erosion_markdown,
    project_erosion,
)

RATES = IndirectRateStructure(fringe=0.30, overhead=0.20, gna=0.10, fee=0.08,
                              escalation_per_year=0.03)


def _sca_lines(years=4, rate=25.0, hours=2000.0, category="Custodian"):
    """A WD-covered category held flat, as price_estimate now produces."""
    return [
        PricedLine(task_id="T1", labor_category=category, hours=hours,
                   direct_rate=rate, wrapped_rate=wrap_rate(rate, RATES),
                   extended=hours * wrap_rate(rate, RATES), year=y,
                   wd_floor=20.0, wd_compliant=True)
        for y in range(years + 1)
    ]


def _exempt_lines(years=4, rate=58.0, hours=2000.0):
    """A professional category escalating normally — no WD floor."""
    out = []
    for y in range(years + 1):
        r = round(rate * (1 + RATES.escalation_per_year) ** y, 2)
        out.append(PricedLine(task_id="T2", labor_category="Software Engineer",
                              hours=hours, direct_rate=r,
                              wrapped_rate=wrap_rate(r, RATES),
                              extended=hours * wrap_rate(r, RATES), year=y))
    return out


def test_erosion_is_exactly_the_oh_gna_and_fee_on_the_wage_delta():
    """52.222-43(d) restores the wage delta plus statutory burden and nothing
    else. The gap must equal wrapped(delta) - delta*(1+burden), to the cent."""
    erosion = project_erosion(_sca_lines(years=1, rate=25.0, hours=1000.0), RATES)
    assert erosion is not None and len(erosion.years) == 1

    year1 = erosion.years[0]
    delta = 25.0 * ((1 + 0.03) ** 1 - 1)              # 0.75/hr
    assert year1.wage_delta_per_hour == pytest.approx(delta, abs=1e-4)
    assert year1.recoverable == pytest.approx(delta * (1 + STATUTORY_BURDEN_RATE) * 1000, abs=0.01)
    assert year1.fully_loaded == pytest.approx(wrap_rate(delta, RATES) * 1000, abs=0.01)
    assert year1.unrecovered == pytest.approx(year1.fully_loaded - year1.recoverable, abs=0.01)
    assert year1.unrecovered > 0


def test_erosion_compounds_across_option_years():
    """Each year's WD is further above the frozen base rate, so the annual gap
    grows — the reason a five-year PoP hurts more than the first year suggests."""
    erosion = project_erosion(_sca_lines(years=4), RATES)
    assert erosion is not None
    gaps = [y.unrecovered for y in erosion.years]
    assert len(gaps) == 4
    assert gaps == sorted(gaps)              # monotonically increasing
    assert gaps[-1] > gaps[0] * 3            # year 4 delta is ~4x year 1's
    assert erosion.total_unrecovered == pytest.approx(sum(gaps), abs=0.01)


def test_only_wd_covered_categories_erode():
    """Exempt categories escalate normally and carry no gap — including them
    would inflate the number and misdirect the fee decision."""
    erosion = project_erosion(_sca_lines(years=2) + _exempt_lines(years=2), RATES)
    assert erosion is not None
    assert erosion.covered_categories == ["Custodian"]
    sca_only = project_erosion(_sca_lines(years=2), RATES)
    assert erosion.total_unrecovered == pytest.approx(sca_only.total_unrecovered, abs=0.01)


def test_sizing_is_expressed_both_ways():
    lines = _sca_lines(years=4)
    total = sum(line.extended for line in lines)
    erosion = project_erosion(lines, RATES, total_price=total)
    assert erosion.margin_points == pytest.approx(erosion.total_unrecovered / total, abs=1e-6)
    # Fee uplift is measured on the pre-fee base, so it exceeds margin points.
    assert erosion.fee_uplift_needed > erosion.margin_points
    assert 0 < erosion.margin_points < 0.25          # sane magnitude


def test_the_default_burden_assumption_is_conservative_in_a_stated_direction():
    """Excluding SUTA/WC understates recovery, so it overstates erosion.
    Supplying real rates must only ever shrink the number."""
    lines = _sca_lines(years=3)
    default = project_erosion(lines, RATES)
    with_wc = project_erosion(lines, RATES, statutory_burden_rate=STATUTORY_BURDEN_RATE + 0.04)
    assert with_wc.total_unrecovered < default.total_unrecovered


def test_growth_assumption_defaults_to_the_companys_own_factor_and_is_overridable():
    lines = _sca_lines(years=2)
    assert project_erosion(lines, RATES).assumed_wd_growth == RATES.escalation_per_year
    faster = project_erosion(lines, RATES, wd_growth=0.06)
    assert faster.assumed_wd_growth == 0.06
    assert faster.total_unrecovered > project_erosion(lines, RATES).total_unrecovered


def test_no_projection_when_it_would_be_meaningless():
    assert project_erosion([], RATES) is None
    assert project_erosion(_exempt_lines(years=3), RATES) is None       # no WD floors
    assert project_erosion(_sca_lines(years=0), RATES) is None          # base year only
    flat = IndirectRateStructure(fringe=0.3, overhead=0.2, gna=0.1, fee=0.08,
                                 escalation_per_year=0.0)
    assert project_erosion(_sca_lines(years=3), flat) is None           # no assumed growth


def test_memo_states_its_assumptions_and_the_choices_they_force():
    lines = _sca_lines(years=4)
    total = sum(line.extended for line in lines)
    md = erosion_markdown(project_erosion(lines, RATES, total_price=total))
    assert "Assumed annual wage-determination increase" in md
    assert "conservative" in md                     # bias direction is stated
    assert "26 U.S.C. 3111" in md                   # burden basis is cited
    assert "no overhead, G&A, or profit" in md.replace("**", "")
    # All three postures offered, none chosen for the user.
    assert "Absorb it" in md and "base-year fee" in md and "labor mix" in md
    assert "30 days" in md                          # the adjustment must be claimed
    assert erosion_markdown(None) == ""


def test_erosion_reaches_the_compliance_memo_as_a_sized_info_finding():
    lines = _sca_lines(years=4)
    total = sum(line.extended for line in lines)
    erosion = project_erosion(lines, RATES, total_price=total)
    findings = check(PricingContext(clauses=["FAR 52.222-43"], erosion=erosion))
    sized = [f for f in findings if f.rule == "FAR 52.222-43(d)"]
    assert len(sized) == 1
    finding = sized[0]
    assert finding.severity == "info"          # not a defect: the clause working
    assert f"{erosion.total_unrecovered:,.0f}" in finding.detail
    assert "%" in finding.detail               # sized against the total price
    assert "fee posture" in finding.remedy

    # No erosion projected -> no finding invented.
    assert [f for f in check(PricingContext(clauses=["FAR 52.222-43"]))
            if f.rule == "FAR 52.222-43(d)"] == []
