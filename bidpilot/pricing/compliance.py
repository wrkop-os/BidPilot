"""Regulatory compliance checks on the cost volume (docs/PRICING_COMPLIANCE.md).

Deterministic, code-only (invariant 3): these are legal tests, never model
judgment. Two rules of construction, both learned the hard way:

1. THE SOLICITATION GOVERNS. Applicability is decided by the literal presence
   of a clause in the solicitation, never inferred from contract value. The
   Revolutionary FAR Overhaul (EO 14275) has agencies deleting and renumbering
   Part 15/22 provisions via class deviations, so a large services RFP may
   legitimately omit a provision its dollar value would once have implied.
2. THRESHOLDS ARE DATED ADVISORIES. Statutory dollar figures move (TINA went
   $2M -> $2.5M -> $10M inside twelve months). Every constant here carries its
   cite and as-of date, is reported as advisory, and never hard-fails a build.

Only two things hard-fail, because only two are unambiguous legal violations
computable from the numbers: paying an SCA-covered category below its wage
determination floor, and pricing an SCA escalation contingency into a
contract whose 52.222-43 warranty says you did not.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from .models import ComplianceFinding

# --- dated regulatory constants ---------------------------------------------
# Sources were verified by search against primary pages (acquisition.gov,
# eCFR); direct fetch was blocked, so treat each as advisory and re-confirm
# against eCFR before relying on it externally. See docs/PRICING_COMPLIANCE.md.

AS_OF = "2026-07-31"

TINA_THRESHOLD_USD = 10_000_000          # FY2026 NDAA s.1804, awards after 2026-06-30
TINA_PRIOR_THRESHOLD_USD = 2_500_000     # FAC 2025-06, awards 2025-10-01..2026-06-30
TINA_CITE = "FAR 15.403-4 / FY2026 NDAA s.1804"

SIMPLIFIED_ACQUISITION_THRESHOLD_USD = 350_000   # FAR 2.101, eff. 2025-10-01
MICRO_PURCHASE_THRESHOLD_USD = 15_000            # FAR 2.101, eff. 2025-10-01

# FAR 22.1103 prescription threshold for 52.222-46. [unverified] — searches
# returned $900,000 with conflicting FAC attribution. Applicability is decided
# by clause presence, so this is used only to explain an unexpected absence.
PROFESSIONAL_COMP_THRESHOLD_USD = 900_000

CAS_MANDATORY_THRESHOLD_USD = 35_000_000  # FY2026 NDAA s.1806, eff. 2026-06-30

# Clause -> what its presence obliges the offeror to do.
PRICING_CLAUSES = {
    "FAR 52.222-43": "SCA price adjustment (multi-year/options): base price must carry NO escalation contingency for SCA wage increases — the clause is the mechanism.",
    "FAR 52.222-44": "SCA price adjustment (no options): triggered by a WD applied by operation of law or a statutory FLSA amendment.",
    "FAR 52.222-46": "Professional employee compensation: a Total Compensation Plan must be submitted; an inadequate plan can justify rejecting the proposal.",
    "FAR 52.237-10": "Uncompensated overtime: disclose UCOT hours/rates by category and apply the ADJUSTED hourly rate to all proposed hours.",
    "FAR 52.215-20": "Certified cost or pricing data (or data other than certified) will be required — plan the cost volume accordingly.",
    "FAR 52.215-21": "Cost or pricing data for modifications.",
    "FAR 52.222-41": "Service Contract Labor Standards: wage determination floors bind every covered category.",
}


# Expressly unallowable costs (FAR 31.205) most likely to appear in a small
# IT-services proposal or indirect pool. Including one in a proposal exposes
# the contractor to a FAR 42.709 penalty equal to the disallowed amount (2x if
# previously determined unallowable), so this screen is worth its false
# positives — every hit is advisory and names its cite.
UNALLOWABLE_PATTERNS: list[tuple[str, str, str]] = [
    (r"\bentertainment\b|\bcountry club\b|\bsocial club\b|\bgolf outing\b",
     "FAR 31.205-14", "entertainment, amusement, and social/dining/country club memberships"),
    (r"\balcohol|\bbeer\b|\bwine\b|\bliquor\b|\bopen bar\b",
     "FAR 31.205-51", "alcoholic beverages (no exception)"),
    (r"\blobby|lobbying\b|\bpolitical contribution|\bpac\b|\bcampaign contribution",
     "FAR 31.205-22", "lobbying and political activity"),
    (r"\bdonation|\bcharitable\b|\bcontribution to\b",
     "FAR 31.205-8", "contributions and donations, regardless of recipient"),
    (r"\bfine\b|\bfines\b|\bpenalt(y|ies)\b|\blate fee",
     "FAR 31.205-15", "fines and penalties from violations of law"),
    (r"\binterest\b on|\bloan interest|\bline of credit\b|\bfinancing cost",
     "FAR 31.205-20", "interest on borrowings, however represented"),
    (r"\bbad debt|\buncollectible\b",
     "FAR 31.205-3", "bad debts and associated collection/legal costs"),
    (r"\bgoodwill\b",
     "FAR 31.205-49", "amortization or write-off of goodwill"),
    (r"\bfirst[- ]class (?:air|flight|fare|travel)|\bbusiness[- ]class (?:air|flight|fare)",
     "FAR 31.205-46(b)", "airfare above the lowest available fare, absent a documented justification"),
    (r"\badvertis(?:ing|ement)|\btrade show\b|\bpromotional\b",
     "FAR 31.205-1", "public relations and advertising not tied to a specific recruitment or contract requirement"),
]


@dataclass
class PricingContext:
    """Everything the checks need, gathered deterministically upstream."""

    clauses: list[str] = field(default_factory=list)      # clause ids found in the solicitation
    total_price: Optional[float] = None
    option_years: int = 0
    sca_categories: list[str] = field(default_factory=list)   # categories with a WD floor
    escalated_categories: list[str] = field(default_factory=list)  # priced above base in an option year
    escalation_rate: float = 0.0
    escalation_basis: Optional[str] = None    # citable index, e.g. "BLS ECI"
    has_compensation_plan: bool = False
    discloses_uncompensated_overtime: Optional[bool] = None
    cost_texts: list[str] = field(default_factory=list)   # ODC/indirect descriptions to screen
    is_small_business: bool = True
    contract_type: Optional[str] = None                   # ffp | tm | cpff | ...

    def has(self, clause: str) -> bool:
        return any(clause in c for c in self.clauses)


def check(ctx: PricingContext) -> list[ComplianceFinding]:
    findings: list[ComplianceFinding] = []
    findings += _sca_escalation_warranty(ctx)
    findings += _professional_compensation(ctx)
    findings += _uncompensated_overtime(ctx)
    findings += _certified_cost_or_pricing_data(ctx)
    findings += _escalation_basis(ctx)
    findings += _unallowable_costs(ctx)
    findings += _accounting_system(ctx)
    findings += _cost_accounting_standards(ctx)
    return findings


def _cost_accounting_standards(ctx: PricingContext) -> list[ComplianceFinding]:
    """CAS applicability. A small business is exempt from all CAS at any dollar
    value (48 CFR 9903.201-1(b)(3)) — worth saying out loud, because the
    threshold headline reads as if it applies to everyone."""
    if not ctx.total_price or ctx.total_price <= CAS_MANDATORY_THRESHOLD_USD:
        return []
    if ctx.is_small_business:
        return [ComplianceFinding(
            severity="info",
            rule="48 CFR 9903.201-1(b)(3)",
            detail=(
                f"Proposed total ${ctx.total_price:,.0f} exceeds the CAS "
                f"full-coverage threshold (${CAS_MANDATORY_THRESHOLD_USD:,.0f}, "
                f"as of {AS_OF}), but small business concerns are exempt from "
                "all Cost Accounting Standards regardless of contract value."
            ),
            remedy=(
                "No CASB Disclosure Statement is required. Keep the small "
                "business representation current in SAM: the exemption follows "
                "the representation, and losing it mid-performance would pull "
                "the contract into coverage."
            ),
        )]
    return [ComplianceFinding(
        severity="soft",
        rule="FAR 30.201-4 / 48 CFR 9903.201-1",
        detail=(
            f"Proposed total ${ctx.total_price:,.0f} exceeds the CAS full-coverage "
            f"threshold (${CAS_MANDATORY_THRESHOLD_USD:,.0f}, as of {AS_OF}) and the "
            "offeror is not represented as a small business."
        ),
        remedy=(
            "Determine whether full or modified CAS coverage applies, submit a "
            "CASB Disclosure Statement (CASB DS-1) if required, and confirm the "
            "proposal's cost accounting practices match the disclosed ones — a "
            "mismatch is a price adjustment waiting to happen."
        ),
    )]


def _unallowable_costs(ctx: PricingContext) -> list[ComplianceFinding]:
    """FAR 31.201-6: unallowable costs must be identified and excluded from any
    proposal. FAR 42.709 penalizes including an expressly unallowable cost."""
    findings: list[ComplianceFinding] = []
    for text in ctx.cost_texts:
        for pattern, cite, what in UNALLOWABLE_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                findings.append(ComplianceFinding(
                    severity="soft",
                    rule=cite,
                    detail=(
                        f"Proposed cost mentions {what}: {text[:120]!r}. If this "
                        "cost is what it appears to be, it is expressly "
                        "unallowable and must be excluded from the proposal."
                    ),
                    remedy=(
                        "Remove the cost, or confirm it falls in a documented "
                        "exception and re-word the description. Expressly "
                        "unallowable costs must also be segregated in the "
                        "accounting records (FAR 31.201-6 / 48 CFR 9904.405); "
                        "including one in a proposal carries a FAR 42.709 "
                        "penalty of the disallowed amount, doubled if the cost "
                        "was previously determined unallowable."
                    ),
                    location=text[:60],
                ))
                break  # one finding per cost line is enough to prompt review
    return findings


def _accounting_system(ctx: PricingContext) -> list[ComplianceFinding]:
    """Cost-reimbursement work requires an accounting system determined
    adequate (FAR 16.301-3(a)(1); SF 1408 criteria)."""
    ctype = (ctx.contract_type or "").lower()
    if not ctype or "cost" not in ctype:
        return []
    return [ComplianceFinding(
        severity="info",
        rule="FAR 16.301-3(a)(1)",
        detail=(
            "Cost-reimbursement pricing: the award requires an accounting "
            "system adequate for determining costs applicable to the contract."
        ),
        remedy=(
            "Expect an SF 1408 preaward accounting-system survey. The system "
            "must segregate direct from indirect costs, accumulate direct costs "
            "by contract, allocate indirect costs on a consistent logical base, "
            "run under general-ledger control, identify labor by cost objective "
            "through timekeeping and labor distribution, determine costs at "
            "least monthly, and exclude costs unallowable under FAR Part 31."
        ),
    )]


def _sca_escalation_warranty(ctx: PricingContext) -> list[ComplianceFinding]:
    """FAR 52.222-43(b) is an express WARRANTY that the price contains no
    contingency for SCA wage increases — stronger than a prohibition. Escalating
    an SCA-floored category into an option year breaches it."""
    if not (ctx.has("52.222-43") or ctx.has("52.222-44")):
        return []
    offenders = sorted(set(ctx.sca_categories) & set(ctx.escalated_categories))
    if not offenders:
        return []
    return [ComplianceFinding(
        severity="hard",
        rule="FAR 52.222-43(b)",
        detail=(
            "Option-year prices escalate SCA-covered categories "
            f"({', '.join(offenders)}) while the contract carries the SCA price-"
            "adjustment clause. Paragraph (b) is a warranty that the price "
            "includes NO allowance for a contingency covering increases the "
            "clause already adjusts for."
        ),
        remedy=(
            "Hold SCA-covered categories flat at the current wage determination "
            "across option years and recover actual increases under 52.222-43 "
            "(wage/fringe delta plus social security, unemployment tax and "
            "workers' compensation only — no OH, G&A, or profit), claiming "
            "within 30 days of each new WD. Escalate professional/exempt "
            "categories normally: they get no SCA protection."
        ),
        location="option-year pricing",
    )]


def _professional_compensation(ctx: PricingContext) -> list[ComplianceFinding]:
    if not ctx.has("52.222-46"):
        # Absence is expected under RFO deviations even above the threshold.
        if ctx.total_price and ctx.total_price > PROFESSIONAL_COMP_THRESHOLD_USD:
            return [ComplianceFinding(
                severity="info",
                rule="FAR 52.222-46",
                detail=(
                    f"Value exceeds ~${PROFESSIONAL_COMP_THRESHOLD_USD:,.0f} but the "
                    "professional-compensation provision is not in the solicitation. "
                    "Expected: agencies are deleting FAR Subpart 22.11 under "
                    "Revolutionary FAR Overhaul class deviations. No plan required."
                ),
            )]
        return []
    if ctx.has_compensation_plan:
        return []
    return [ComplianceFinding(
        severity="soft",
        rule="FAR 52.222-46",
        detail=(
            "The solicitation carries the professional-employee compensation "
            "provision but no Total Compensation Plan was produced."
        ),
        remedy=(
            "Submit salary ranges by labor category and itemized fringe, "
            "supported by dated REGIONAL compensation surveys (BLS OES plus a "
            "recognized commercial survey). On a successor procurement, justify "
            "in writing every category priced below the incumbent against the "
            "paragraph (b) criteria — program continuity, uninterrupted quality, "
            "availability of qualified staff. Paragraph (d) permits rejecting a "
            "proposal for an inadequate plan."
        ),
    )]


def _uncompensated_overtime(ctx: PricingContext) -> list[ComplianceFinding]:
    if not ctx.has("52.237-10"):
        return []
    if ctx.discloses_uncompensated_overtime is not None:
        return []
    return [ComplianceFinding(
        severity="soft",
        rule="FAR 52.237-10",
        detail=(
            "Hours-based professional/technical services over the simplified "
            f"acquisition threshold (${SIMPLIFIED_ACQUISITION_THRESHOLD_USD:,.0f}): "
            "the proposal must state whether uncompensated overtime is proposed."
        ),
        remedy=(
            "If no UCOT is proposed, say so explicitly. If it is, disclose UCOT "
            "hours and rates by labor category at the same detail as compensated "
            "hours, and apply the adjusted hourly rate (rate x 40 / proposed "
            "hours per week) to ALL proposed hours — not just the overtime."
        ),
    )]


def _certified_cost_or_pricing_data(ctx: PricingContext) -> list[ComplianceFinding]:
    if not ctx.total_price:
        return []
    over = ctx.total_price > TINA_THRESHOLD_USD
    signalled = ctx.has("52.215-20") or ctx.has("52.215-21")
    if not (over or signalled):
        return []
    detail = (
        f"Proposed total ${ctx.total_price:,.0f} exceeds the certified cost or "
        f"pricing data threshold (${TINA_THRESHOLD_USD:,.0f}, {TINA_CITE}, as of "
        f"{AS_OF})." if over else
        "The solicitation includes the cost-or-pricing-data provision."
    )
    return [ComplianceFinding(
        severity="info",
        rule=TINA_CITE,
        detail=detail + (
            " Threshold figures move often and the codified FAR text may lag the "
            "statute — the solicitation's stated requirement governs."
        ),
        remedy=(
            "Confirm whether an exception applies (adequate price competition, "
            "commercial product/service, prices set by law, HCA waiver — FAR "
            "15.403-1(b)). If none does, prepare a FAR 15.408 Table 15-2 "
            "submission (time-phased labor by category, indirect pool/base "
            "build-ups, ODC bases, an affirmative statement if facilities capital "
            "cost of money is not proposed) and expect to execute the Certificate "
            "of Current Cost or Pricing Data at price agreement, not at "
            "submission. Otherwise expect to supply data other than certified "
            "cost or pricing data — prior sales of the same or similar items."
        ),
    )]


def _escalation_basis(ctx: PricingContext) -> list[ComplianceFinding]:
    if ctx.option_years <= 0 or ctx.escalation_rate <= 0:
        return []
    if ctx.escalation_basis:
        return []
    return [ComplianceFinding(
        severity="soft",
        rule="FAR 15.404-1(c)",
        detail=(
            f"Option-year pricing applies {ctx.escalation_rate:.1%} escalation with "
            "no index cited. Cost analysis evaluates the reasonableness of "
            "projected cost trends; an unsupported factor is the easiest line for "
            "an analyst to question."
        ),
        remedy=(
            "Tie the factor to a published index and name it in the basis of "
            "estimate (e.g. BLS Employment Cost Index for wages and salaries, or "
            "a trailing multi-year average of it), or to your own documented "
            "historical merit-increase actuals."
        ),
    )]


# Indices an escalation factor may legitimately rest on. Naming one in the BOE
# is what turns "3%" from an assertion into a supported projection.
_ESCALATION_INDEX_RE = re.compile(
    r"employment cost index|\bECI\b|\bCPI[- ]?U?\b|consumer price index|"
    r"bureau of labor statistics|\bBLS\b|global insight|\bIHS\b|"
    r"historical merit increase|merit[- ]increase actuals",
    re.IGNORECASE,
)


def context_from_pricing(
    pricing,
    clauses: list[str],
    escalation_rate: float = 0.0,
    is_small_business: bool = True,
    has_compensation_plan: bool = False,
    discloses_uncompensated_overtime: Optional[bool] = None,
) -> PricingContext:
    """Derive the check inputs from the priced model — no judgment, just facts
    already computed by the rate engine."""
    lines = list(pricing.priced_lines or [])
    by_category: dict[str, set[float]] = {}
    sca: list[str] = []
    for line in lines:
        by_category.setdefault(line.labor_category, set()).add(line.direct_rate)
        if line.wd_floor is not None and line.labor_category not in sca:
            sca.append(line.labor_category)
    escalated = sorted(cat for cat, seen in by_category.items() if len(seen) > 1)

    cost_texts = [o.description for o in (pricing.odcs or [])]
    cost_texts += [o.source for o in (pricing.odcs or []) if o.source]

    contract_type = None
    clins = getattr(pricing.structure, "clins", []) if pricing.structure else []
    for clin in clins:
        value = getattr(clin.contract_type, "value", clin.contract_type)
        if value and value != "unknown":
            contract_type = str(value)
            break

    # Prefer the most specific name in the BOE: "BLS Employment Cost Index"
    # reads as a citation, a bare "BLS" reads as a hand-wave.
    named = _ESCALATION_INDEX_RE.findall(pricing.boe_narrative or "")
    basis = max(named, key=len) if named else None

    return PricingContext(
        clauses=list(clauses),
        total_price=pricing.total,
        option_years=max((line.year for line in lines), default=0),
        sca_categories=sca,
        escalated_categories=escalated,
        escalation_rate=escalation_rate,
        escalation_basis=basis,
        has_compensation_plan=has_compensation_plan,
        discloses_uncompensated_overtime=discloses_uncompensated_overtime,
        cost_texts=cost_texts,
        is_small_business=is_small_business,
        contract_type=contract_type,
    )


def obligations(clauses: list[str]) -> list[str]:
    """Plain-language duties triggered by the clauses actually present."""
    return [
        f"{clause}: {duty}"
        for clause, duty in PRICING_CLAUSES.items()
        if any(clause in c for c in clauses)
    ]


_SEVERITY_ORDER = {"hard": 0, "soft": 1, "info": 2}
_SEVERITY_LABEL = {
    "hard": "BLOCKS EXPORT",
    "soft": "Reviewer action",
    "info": "For awareness",
}


def compliance_markdown(findings: list[ComplianceFinding],
                        duties: list[str]) -> str:
    """The regulatory memo that ships with the cost volume."""
    lines = [
        "# Cost volume — regulatory review",
        "",
        "Deterministic checks against the pricing clauses this solicitation "
        "actually carries. Applicability is decided by clause presence, never "
        "inferred from contract value: agencies are deleting and renumbering "
        "FAR Part 15 and 22 provisions under Revolutionary FAR Overhaul class "
        "deviations, so the solicitation governs.",
        "",
        f"Dollar thresholds cited below are as of {AS_OF} and are advisory — "
        "statutory figures move faster than the codified FAR text. Confirm "
        "against the solicitation and eCFR before relying on one.",
        "",
    ]

    if duties:
        lines += ["## Obligations triggered by the clauses present", ""]
        lines += [f"- {duty}" for duty in duties]
        lines.append("")
    else:
        lines += [
            "## Obligations triggered by the clauses present",
            "",
            "None detected. The clause scanner found no pricing-specific "
            "provisions (52.222-41/-43/-44/-46, 52.237-10, 52.215-20/-21) in "
            "the solicitation corpus. If you know one applies, the corpus may "
            "be incomplete — check the amendment chain.",
            "",
        ]

    if not findings:
        lines += [
            "## Findings",
            "",
            "No findings: the cost volume is consistent with every pricing "
            "clause detected in the solicitation.",
        ]
        return "\n".join(lines)

    lines += ["## Findings", ""]
    for finding in sorted(findings, key=lambda f: _SEVERITY_ORDER.get(f.severity, 3)):
        label = _SEVERITY_LABEL.get(finding.severity, finding.severity)
        lines += [f"### {finding.rule} — {label}", ""]
        if finding.location:
            lines.append(f"_Where: {finding.location}_")
            lines.append("")
        lines += [finding.detail, ""]
        if finding.remedy:
            lines += [f"**What to do:** {finding.remedy}", ""]

    lines += [
        "---",
        "",
        "This is a compliance screen, not legal advice, and not a substitute "
        "for contracts review. It reports what the numbers and the clause text "
        "say; a human owns the price.",
    ]
    return "\n".join(lines)
