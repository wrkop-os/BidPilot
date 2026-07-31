"""Margin erosion on SCA labor under the 52.222-43 price adjustment.

Holding SCA-covered categories flat across option years is legally correct
(see `rates.price_estimate`), but it is not free, and the cost is invisible
unless someone computes it.

The mechanics, from FAR 52.222-43(d): when a new wage determination raises a
covered employee's wages or fringe, the contract price is adjusted for that
increase **and the accompanying increases in social security and unemployment
taxes and workers' compensation insurance** — and then, in the clause's own
words, it "shall not otherwise include any amount for general and
administrative costs, overhead, or profit."

So the contractor recovers the wage delta plus statutory burden, and eats the
overhead, G&A, and fee that the same delta would have carried in the base
year. Over a five-year period of performance on a labor-heavy SCA contract
that is real money, and it compounds.

This module quantifies it, because "plan for it in fee" is not actionable
until someone says how much. Deterministic arithmetic (invariant 3) — the
numbers here are computed, never drafted.

Every projection rests on an assumption about future wage determinations that
nobody can know. The assumption is stated in the output, defaults to the
company's own documented escalation factor, and is overridable.
"""

from __future__ import annotations

from typing import Optional

from .models import PricedLine, SCAErosion, SCAErosionYear
from .rates import IndirectRateStructure, wrap_rate

# Employer-side statutory payroll burden that FAR 52.222-43(d) DOES let you
# recover along with the wage delta. Defaults to the two components that are
# uniform nationally:
#
#   FICA        7.65%  (6.2% OASDI + 1.45% Medicare, 26 U.S.C. 3111)
#   FUTA        0.60%  (effective rate after the standard state credit)
#
# Workers' compensation and state unemployment are also recoverable but vary
# by state and class code (WC alone spans well under 1% for clerical work to
# double digits for hazardous trades), so they are excluded from the default.
#
# The bias direction matters and runs one way: excluding them UNDERSTATES what
# you recover, which OVERSTATES the projected erosion. This default is the
# conservative end of the range. Supplying your real SUTA and WC rates can
# only shrink the number.
STATUTORY_BURDEN_RATE = 0.0825
STATUTORY_BURDEN_CITE = "26 U.S.C. 3111 (FICA) + FUTA effective rate"


def project_erosion(
    priced_lines: list[PricedLine],
    indirects: IndirectRateStructure,
    total_price: Optional[float] = None,
    wd_growth: Optional[float] = None,
    statutory_burden_rate: float = STATUTORY_BURDEN_RATE,
) -> Optional[SCAErosion]:
    """Project unrecovered indirect + fee on SCA labor across option years.

    Returns None when the projection would be meaningless: no WD-covered
    lines, no option years, or no assumed wage growth.

    `wd_growth` is the assumed annual wage-determination increase. It defaults
    to the company's own escalation factor — the rate they already believe
    wages will move at, which keeps the projection internally consistent with
    the rest of the cost volume rather than importing a second opinion.
    """
    covered = [line for line in priced_lines if line.wd_floor is not None]
    if not covered:
        return None
    max_year = max((line.year for line in covered), default=0)
    if max_year < 1:
        return None
    growth = indirects.escalation_per_year if wd_growth is None else wd_growth
    if growth <= 0:
        return None

    # Base-year direct rate per category anchors the projected increase.
    base_rate: dict[str, float] = {
        line.labor_category: line.direct_rate for line in covered if line.year == 0
    }

    years: list[SCAErosionYear] = []
    for year in range(1, max_year + 1):
        year_lines = [line for line in covered if line.year == year]
        if not year_lines:
            continue
        recoverable = fully_loaded = hours = 0.0
        weighted_delta = 0.0
        for line in year_lines:
            base = base_rate.get(line.labor_category, line.direct_rate)
            # What the new WD is assumed to add to this category's hourly wage.
            delta = base * ((1 + growth) ** year - 1)
            # 52.222-43(d): wage delta + statutory burden, and nothing else.
            recoverable += delta * (1 + statutory_burden_rate) * line.hours
            # What that same delta costs once it flows through the company's
            # own fringe, overhead, G&A and fee — which it does, because those
            # pools are allocated on direct labor.
            fully_loaded += wrap_rate(delta, indirects) * line.hours
            weighted_delta += delta * line.hours
            hours += line.hours
        unrecovered = fully_loaded - recoverable
        years.append(SCAErosionYear(
            year=year,
            hours=round(hours, 2),
            wage_delta_per_hour=round(weighted_delta / hours, 4) if hours else 0.0,
            recoverable=round(recoverable, 2),
            fully_loaded=round(fully_loaded, 2),
            unrecovered=round(unrecovered, 2),
        ))

    if not years:
        return None
    total_unrecovered = round(sum(y.unrecovered for y in years), 2)

    # Two ways to size it, because they answer different questions:
    #   margin_points  — how much of the contract you give back
    #   fee_uplift     — how many points of fee on the whole job offset it
    margin_points = None
    fee_uplift = None
    if total_price:
        margin_points = round(total_unrecovered / total_price, 6)
        pre_fee = total_price / (1 + indirects.fee) if indirects.fee > -1 else total_price
        if pre_fee:
            fee_uplift = round(total_unrecovered / pre_fee, 6)

    return SCAErosion(
        assumed_wd_growth=growth,
        statutory_burden_rate=statutory_burden_rate,
        statutory_burden_basis=STATUTORY_BURDEN_CITE,
        covered_categories=sorted(base_rate),
        years=years,
        total_unrecovered=total_unrecovered,
        total_price=total_price,
        margin_points=margin_points,
        fee_uplift_needed=fee_uplift,
    )


def erosion_markdown(erosion: Optional[SCAErosion]) -> str:
    """The section that goes in the regulatory memo."""
    if erosion is None:
        return ""
    lines = [
        "## SCA margin erosion across option years",
        "",
        "Holding SCA-covered categories flat is required by the 52.222-43(b) "
        "warranty, and the clause's adjustment recovers the wage delta plus "
        "social security, unemployment tax and workers' compensation — but "
        "**no overhead, G&A, or profit**. That gap is projected below.",
        "",
        f"Assumed annual wage-determination increase: **{erosion.assumed_wd_growth:.2%}** "
        "(defaults to the company's own escalation factor — change the "
        "assumption and this whole table moves).",
        "",
        f"Statutory burden treated as recoverable: **{erosion.statutory_burden_rate:.2%}** "
        f"({erosion.statutory_burden_basis}). State unemployment and workers' "
        "compensation are also recoverable but vary by state and class code; "
        "excluding them makes this projection conservative — supplying your "
        "real rates can only reduce the erosion.",
        "",
        f"Categories: {', '.join(erosion.covered_categories)}",
        "",
        "| Option year | Hours | Assumed wage delta/hr | Loaded cost of the delta | Recoverable under 52.222-43 | Unrecovered |",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    for y in erosion.years:
        lines.append(
            f"| {y.year} | {y.hours:,.0f} | ${y.wage_delta_per_hour:,.2f} | "
            f"${y.fully_loaded:,.2f} | ${y.recoverable:,.2f} | **${y.unrecovered:,.2f}** |"
        )
    lines += ["", f"**Total unrecovered over the period of performance: "
                  f"${erosion.total_unrecovered:,.2f}**", ""]
    if erosion.margin_points is not None:
        lines.append(
            f"That is {erosion.margin_points:.2%} of the proposed total price"
            + (f", equivalent to about {erosion.fee_uplift_needed:.2%} of additional "
               "fee across the whole job." if erosion.fee_uplift_needed is not None else ".")
        )
        lines.append("")
    lines += [
        "This is the clause working as designed, not a pricing error. The "
        "decision it forces is a fee-posture decision, and it belongs to a "
        "human:",
        "",
        "- **Absorb it** — accept the declining margin to stay price-competitive.",
        "- **Price it into base-year fee** — legitimate, since the warranty in "
        "52.222-43(b) covers a contingency for *SCA wage increases*, not your "
        "target profit rate. Document the reasoning in the BOE so it reads as "
        "a fee decision rather than a hidden escalation contingency.",
        "- **Reshape the labor mix** — shift hours toward exempt/professional "
        "categories, which escalate normally and carry no such gap.",
        "",
        "Whichever you choose, claim every adjustment: the 52.222-43 "
        "adjustment is bilateral and must be requested within 30 days of each "
        "new wage determination. An unclaimed adjustment turns the recoverable "
        "column above into more unrecovered cost.",
    ]
    return "\n".join(lines)
