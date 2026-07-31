"""Regulatory checks on the cost volume.

The load-bearing rule these tests defend: applicability comes from the literal
presence of a clause in the solicitation, never from the contract's dollar
value. Agencies are deleting and renumbering FAR Part 15/22 provisions under
Revolutionary FAR Overhaul class deviations, so a $50M services RFP may
legitimately omit a provision its value would once have implied.
"""

from bidpilot.data.clause_patterns import scan_clauses
from bidpilot.pricing.compliance import (
    CAS_MANDATORY_THRESHOLD_USD,
    TINA_THRESHOLD_USD,
    PricingContext,
    check,
    compliance_markdown,
    context_from_pricing,
    obligations,
)
from bidpilot.pricing.models import (
    ContractType,
    CLINItem,
    ODCItem,
    PricedLine,
    PricingModel,
    PricingStructure,
)


def _ctx(**kw) -> PricingContext:
    return PricingContext(**kw)


def _severities(findings, severity):
    return [f for f in findings if f.severity == severity]


# -- the one hard rule --------------------------------------------------------


def test_escalating_an_sca_category_under_52_222_43_is_a_hard_finding():
    findings = check(_ctx(
        clauses=["FAR 52.222-43", "FAR 52.222-41"],
        sca_categories=["Help Desk Technician", "Custodian"],
        escalated_categories=["Help Desk Technician", "Software Engineer"],
        option_years=4,
        escalation_rate=0.03,
    ))
    hard = _severities(findings, "hard")
    assert len(hard) == 1
    assert hard[0].rule == "FAR 52.222-43(b)"
    assert "Help Desk Technician" in hard[0].detail
    # The exempt category is escalating legitimately and must not be named.
    assert "Software Engineer" not in hard[0].detail
    assert "warranty" in hard[0].detail


def test_no_hard_finding_when_only_exempt_categories_escalate():
    findings = check(_ctx(
        clauses=["FAR 52.222-43"],
        sca_categories=["Custodian"],
        escalated_categories=["Software Engineer", "Program Manager"],
        option_years=4, escalation_rate=0.03, escalation_basis="BLS ECI",
    ))
    assert _severities(findings, "hard") == []


def test_escalating_sca_categories_is_fine_without_the_clause():
    """Absent 52.222-43/-44 there is no adjustment mechanism, so escalation is
    how a bidder covers future wage increases."""
    findings = check(_ctx(
        clauses=["FAR 52.222-41"],
        sca_categories=["Custodian"],
        escalated_categories=["Custodian"],
        option_years=4, escalation_rate=0.03, escalation_basis="BLS ECI",
    ))
    assert _severities(findings, "hard") == []


# -- applicability is clause-driven, never value-driven -----------------------


def test_professional_compensation_absence_is_explained_not_demanded():
    findings = check(_ctx(clauses=["FAR 52.222-41"], total_price=5_000_000))
    prof = [f for f in findings if f.rule == "FAR 52.222-46"]
    assert len(prof) == 1 and prof[0].severity == "info"
    assert "Revolutionary FAR Overhaul" in prof[0].detail
    assert "No plan required" in prof[0].detail


def test_professional_compensation_plan_demanded_only_when_the_clause_is_present():
    findings = check(_ctx(clauses=["FAR 52.222-46"], total_price=200_000))
    prof = [f for f in findings if f.rule == "FAR 52.222-46"]
    assert len(prof) == 1 and prof[0].severity == "soft"
    assert "REGIONAL" in prof[0].remedy       # national averages do not survive review
    assert "below the incumbent" in prof[0].remedy

    satisfied = check(_ctx(clauses=["FAR 52.222-46"], total_price=200_000,
                           has_compensation_plan=True))
    assert [f for f in satisfied if f.rule == "FAR 52.222-46"] == []


def test_uncompensated_overtime_asks_for_an_explicit_answer():
    findings = check(_ctx(clauses=["FAR 52.237-10"]))
    ucot = [f for f in findings if f.rule == "FAR 52.237-10"]
    assert len(ucot) == 1
    assert "rate x 40 / proposed" in ucot[0].remedy    # applies to ALL hours
    assert "ALL proposed hours" in ucot[0].remedy

    # "No UCOT proposed" is an answer, and closes the finding.
    answered = check(_ctx(clauses=["FAR 52.237-10"],
                          discloses_uncompensated_overtime=False))
    assert [f for f in answered if f.rule == "FAR 52.237-10"] == []


# -- dated thresholds are advisory, never hard --------------------------------


def test_tina_threshold_is_reported_as_dated_and_never_hard():
    findings = check(_ctx(total_price=TINA_THRESHOLD_USD + 1))
    tina = [f for f in findings if "15.403-4" in f.rule]
    assert len(tina) == 1 and tina[0].severity == "info"
    assert "may lag the statute" in tina[0].detail
    assert "Certificate of Current Cost or Pricing Data" in tina[0].remedy
    assert "at price agreement, not at" in tina[0].remedy   # never at submission

    below = check(_ctx(total_price=TINA_THRESHOLD_USD - 1))
    assert [f for f in below if "15.403-4" in f.rule] == []


def test_cost_or_pricing_data_provision_fires_below_the_threshold_too():
    findings = check(_ctx(clauses=["FAR 52.215-20"], total_price=500_000))
    assert any("15.403-4" in f.rule for f in findings)


def test_small_business_is_cas_exempt_at_any_value():
    over = CAS_MANDATORY_THRESHOLD_USD + 1
    small = check(_ctx(total_price=over, is_small_business=True))
    cas = [f for f in small if "9903.201-1" in f.rule]
    assert len(cas) == 1 and cas[0].severity == "info"
    assert "exempt from" in cas[0].detail

    other = check(_ctx(total_price=over, is_small_business=False))
    cas = [f for f in other if "CFR 9903.201-1" in f.rule or "30.201-4" in f.rule]
    assert len(cas) == 1 and cas[0].severity == "soft"
    assert "DS-1" in cas[0].remedy


# -- cost realism / allowability ---------------------------------------------


def test_unsupported_escalation_factor_is_flagged_until_an_index_is_named():
    findings = check(_ctx(option_years=4, escalation_rate=0.03))
    esc = [f for f in findings if f.rule == "FAR 15.404-1(c)"]
    assert len(esc) == 1 and esc[0].severity == "soft"
    assert "Employment Cost Index" in esc[0].remedy

    supported = check(_ctx(option_years=4, escalation_rate=0.03,
                           escalation_basis="BLS ECI"))
    assert [f for f in supported if f.rule == "FAR 15.404-1(c)"] == []


def test_expressly_unallowable_costs_are_screened_with_their_cites():
    findings = check(_ctx(cost_texts=[
        "Annual customer appreciation dinner and open bar",
        "Trade show booth and promotional materials",
        "Cloud hosting (AWS GovCloud), 12 months",
    ]))
    cites = {f.rule for f in findings if f.rule.startswith("FAR 31.205")}
    assert "FAR 31.205-51" in cites or "FAR 31.205-14" in cites
    assert "FAR 31.205-1" in cites
    unallowable = [f for f in findings if f.rule.startswith("FAR 31.205")]
    assert all(f.severity == "soft" for f in unallowable)     # advisory, not a block
    assert any("42.709" in f.remedy for f in unallowable)     # names the penalty
    # The legitimate ODC produces nothing.
    assert not any("Cloud hosting" in (f.location or "") for f in unallowable)


def test_cost_reimbursement_pricing_warns_about_the_accounting_system():
    findings = check(_ctx(contract_type="cost_reimbursable"))
    acct = [f for f in findings if f.rule == "FAR 16.301-3(a)(1)"]
    assert len(acct) == 1
    assert "SF 1408" in acct[0].remedy
    assert "timekeeping" in acct[0].remedy

    assert [f for f in check(_ctx(contract_type="ffp"))
            if f.rule == "FAR 16.301-3(a)(1)"] == []


# -- deriving the context from a real priced model ----------------------------


def test_context_is_derived_from_the_priced_model_not_asserted():
    pricing = PricingModel(
        structure=PricingStructure(clins=[
            CLINItem(clin="0001", description="Base", contract_type=ContractType.UNKNOWN),
            CLINItem(clin="0002", description="OY1", contract_type=ContractType.COST),
        ]),
        priced_lines=[
            # SCA category, held flat across two years
            PricedLine(task_id="T1", labor_category="Custodian", hours=10,
                       direct_rate=20.0, wrapped_rate=30.0, extended=300.0,
                       year=0, wd_floor=19.0, wd_compliant=True),
            PricedLine(task_id="T1", labor_category="Custodian", hours=10,
                       direct_rate=20.0, wrapped_rate=30.0, extended=300.0,
                       year=1, wd_floor=19.0, wd_compliant=True),
            # exempt category, escalating
            PricedLine(task_id="T2", labor_category="Software Engineer", hours=10,
                       direct_rate=58.0, wrapped_rate=90.0, extended=900.0, year=0),
            PricedLine(task_id="T2", labor_category="Software Engineer", hours=10,
                       direct_rate=59.74, wrapped_rate=93.0, extended=930.0, year=1),
        ],
        odcs=[ODCItem(description="Travel to Huntsville, coach airfare")],
        total=2_430.0,
        boe_narrative="Escalation of 3.0% follows the BLS Employment Cost Index.",
    )
    ctx = context_from_pricing(pricing, ["FAR 52.222-43"], escalation_rate=0.03)

    assert ctx.sca_categories == ["Custodian"]
    assert ctx.escalated_categories == ["Software Engineer"]
    assert ctx.option_years == 1
    assert ctx.contract_type == "cost_reimbursable"
    assert ctx.escalation_basis and "Employment Cost Index" in ctx.escalation_basis
    # Correctly priced: no warranty breach, no unsupported-escalation finding.
    findings = check(ctx)
    assert _severities(findings, "hard") == []
    assert [f for f in findings if f.rule == "FAR 15.404-1(c)"] == []


# -- clause detection ---------------------------------------------------------


def test_scanner_detects_the_pricing_clauses_and_their_rfo_aliases():
    text = (
        "The following clauses apply: 52.222-41, 52.222-43, 52.222-46, "
        "52.237-10, and 52.215-20. Offerors shall submit a Total Compensation "
        "Plan and state whether uncompensated overtime is proposed. Cost "
        "elements shall follow Table 15-2."
    )
    hits = {h.clause for h in scan_clauses(text)}
    for clause in ("FAR 52.222-41", "FAR 52.222-43", "FAR 52.222-46",
                   "FAR 52.237-10", "FAR 52.215-20"):
        assert clause in hits
    assert "Total Compensation Plan" in hits
    assert "Uncompensated overtime" in hits
    assert "Cost or pricing data submission" in hits


def test_obligations_only_lists_clauses_actually_present():
    duties = obligations(["FAR 52.222-43", "FAR 52.219-6"])
    assert len(duties) == 1
    assert duties[0].startswith("FAR 52.222-43")
    assert obligations([]) == []


# -- the shipped memo ---------------------------------------------------------


def test_memo_is_honest_when_nothing_applies_and_ranks_hard_findings_first():
    empty = compliance_markdown([], [])
    assert "No findings" in empty
    assert "amendment chain" in empty      # tells you where to look if that is wrong

    findings = check(_ctx(
        clauses=["FAR 52.222-43", "FAR 52.237-10"],
        sca_categories=["Custodian"], escalated_categories=["Custodian"],
        option_years=2, escalation_rate=0.03,
    ))
    md = compliance_markdown(findings, obligations(["FAR 52.222-43"]))
    assert md.index("BLOCKS EXPORT") < md.index("Reviewer action")
    assert "not legal advice" in md
    assert "advisory" in md                # thresholds never presented as settled
