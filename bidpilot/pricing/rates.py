"""Deterministic rate engine (PRD §9.4, FR-13, §14.3).

"LLM writes a number" is unacceptable here. Wrapped rates, escalation, and
wage-determination floors are computed and enforced in code. Underbidding an
SCA/Davis-Bacon wage determination is both illegal and an automatic reviewer
catch, so a violation is a hard error, not a warning.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from .models import LaborEstimate, PricedLine, SensitivityPoint, WDViolation


class IndirectRateStructure(BaseModel):
    """The company's stored indirect rates (KB governance applies)."""

    fringe: float = Field(ge=0, description="e.g. 0.30 for 30%")
    overhead: float = Field(ge=0)
    gna: float = Field(ge=0, description="G&A")
    fee: float = Field(ge=0, description="Target fee/profit")
    escalation_per_year: float = Field(default=0.03, ge=0, description="Option-year escalation")


class WageDeterminationEntry(BaseModel):
    labor_category: str
    minimum_wage: float
    health_welfare: float = Field(default=0.0, description="Required H&W hourly benefit")

    @property
    def floor(self) -> float:
        return self.minimum_wage + self.health_welfare


class WageDetermination(BaseModel):
    """Parsed WD table: legal wage+H&W floors per labor category/locality."""

    wd_number: Optional[str] = None
    locality: Optional[str] = None
    entries: list[WageDeterminationEntry] = Field(default_factory=list)

    def floor_for(self, labor_category: str) -> Optional[WageDeterminationEntry]:
        needle = _norm(labor_category)
        for entry in self.entries:
            if _norm(entry.labor_category) == needle:
                return entry
        # Loose containment match: 'Sr. Systems Administrator' vs 'Systems Administrator'
        for entry in self.entries:
            if _norm(entry.labor_category) in needle or needle in _norm(entry.labor_category):
                return entry
        return None


def wrap_rate(direct: float, rates: IndirectRateStructure) -> float:
    """direct -> +fringe -> +overhead -> +G&A -> +fee, sequentially compounded."""
    loaded = direct * (1 + rates.fringe)
    loaded *= 1 + rates.overhead
    loaded *= 1 + rates.gna
    loaded *= 1 + rates.fee
    return round(loaded, 2)


def escalate(rate: float, rates: IndirectRateStructure, year: int) -> float:
    """Apply option-year escalation. year 0 = base year (no escalation)."""
    return round(rate * (1 + rates.escalation_per_year) ** year, 2)


def price_estimate(
    estimate: LaborEstimate,
    direct_rates: dict[str, float],
    indirects: IndirectRateStructure,
    wage_determination: Optional[WageDetermination] = None,
    option_years: int = 0,
    sca_price_adjustment: bool = False,
) -> tuple[list[PricedLine], list[WDViolation], list[str]]:
    """Deterministically price every labor line across base + option years.

    Returns (priced lines, WD violations, unresolved labor categories).
    WD compliance is checked against the ESCALATED direct rate for each year.

    `sca_price_adjustment` says the solicitation carries FAR 52.222-43 or
    52.222-44. Paragraph (b) of 52.222-43 is an express *warranty* that the
    price contains no contingency covering SCA wage increases — the clause
    itself is the adjustment mechanism, claimed against each new wage
    determination. So when it applies, wage-determination-covered categories
    are held flat at the current WD across option years and only uncovered
    (professional/exempt) categories escalate. Escalating a covered category
    would price in exactly the contingency the offeror warranted away.

    Indirect rates are unchanged either way: the warranty is about SCA wage and
    fringe increases, and the clause's own adjustment is likewise limited to
    wages, fringe, and the accompanying social security, unemployment tax, and
    workers' compensation — no overhead, G&A, or profit.
    """
    priced: list[PricedLine] = []
    violations: list[WDViolation] = []
    unresolved: list[str] = []
    seen_violation_keys: set[tuple[str, int]] = set()

    for line in estimate.lines:
        direct = _lookup_rate(direct_rates, line.labor_category)
        if direct is None:
            if line.labor_category not in unresolved:
                unresolved.append(line.labor_category)
            continue
        wd_entry = wage_determination.floor_for(line.labor_category) if wage_determination else None
        sca_flat = sca_price_adjustment and wd_entry is not None
        for year in range(option_years + 1):
            year_direct = direct if sca_flat else escalate(direct, indirects, year)
            wrapped = wrap_rate(year_direct, indirects)
            wd_floor = wd_entry.floor if wd_entry else None
            wd_ok = None
            if wd_floor is not None:
                wd_ok = year_direct >= wd_floor
                if not wd_ok and (line.labor_category, year) not in seen_violation_keys:
                    seen_violation_keys.add((line.labor_category, year))
                    violations.append(
                        WDViolation(
                            labor_category=line.labor_category,
                            direct_rate=year_direct,
                            wd_floor=wd_floor,
                            detail=(
                                f"Year {year}: direct rate ${year_direct:.2f}/hr is below the "
                                f"wage determination floor ${wd_floor:.2f}/hr "
                                f"(wage ${wd_entry.minimum_wage:.2f} + H&W ${wd_entry.health_welfare:.2f})."
                            ),
                        )
                    )
            priced.append(
                PricedLine(
                    task_id=line.task_id,
                    labor_category=line.labor_category,
                    hours=line.hours,
                    direct_rate=year_direct,
                    wrapped_rate=wrapped,
                    extended=round(line.hours * wrapped, 2),
                    year=year,
                    wd_floor=wd_floor,
                    wd_compliant=wd_ok,
                )
            )
    return priced, violations, unresolved


def total_of(priced: list[PricedLine]) -> float:
    return round(sum(line.extended for line in priced), 2)


def sensitivity(priced: list[PricedLine], deltas: tuple[float, ...] = (-0.10, 0.10)) -> list[SensitivityPoint]:
    """Price at ±X% hours (PRD §6.3 A9 sensitivity summary)."""
    base = total_of(priced)
    points = [SensitivityPoint(label="baseline", total=base)]
    for delta in deltas:
        points.append(
            SensitivityPoint(label=f"hours {delta:+.0%}", total=round(base * (1 + delta), 2))
        )
    return points


def _lookup_rate(direct_rates: dict[str, float], labor_category: str) -> Optional[float]:
    needle = _norm(labor_category)
    for name, rate in direct_rates.items():
        if _norm(name) == needle:
            return rate
    for name, rate in direct_rates.items():
        if _norm(name) in needle or needle in _norm(name):
            return rate
    return None


def _norm(s: str) -> str:
    return " ".join(s.lower().replace(".", "").replace("-", " ").split())
