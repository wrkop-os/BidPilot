"""SBA size standards lookup (PRD §2.3.3, FR-7): NAICS -> revenue or
employee ceiling. Static data + code lookup — never an LLM guess.

This table is a working subset of the SBA's size standards; the full table
is published by SBA and should be refreshed periodically. Unknown NAICS
codes return None and are surfaced as missing_info in the eligibility
report, not silently passed.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class SizeStandard(BaseModel):
    naics: str
    description: str
    receipts_millions: Optional[float] = None  # average annual receipts ceiling ($M)
    employees: Optional[int] = None            # employee ceiling


# Subset covering common services/IT/professional NAICS. Source: SBA size
# standards table (13 CFR 121.201). Refresh with the published table.
_TABLE: dict[str, SizeStandard] = {
    s.naics: s
    for s in [
        SizeStandard(naics="541511", description="Custom Computer Programming Services", receipts_millions=34.0),
        SizeStandard(naics="541512", description="Computer Systems Design Services", receipts_millions=34.0),
        SizeStandard(naics="541513", description="Computer Facilities Management Services", receipts_millions=34.0),
        SizeStandard(naics="541519", description="Other Computer Related Services", receipts_millions=34.0),
        SizeStandard(naics="541611", description="Admin & General Management Consulting", receipts_millions=24.5),
        SizeStandard(naics="541330", description="Engineering Services", receipts_millions=25.5),
        SizeStandard(naics="541612", description="Human Resources Consulting", receipts_millions=29.0),
        SizeStandard(naics="541690", description="Other Scientific & Technical Consulting", receipts_millions=19.0),
        SizeStandard(naics="541990", description="All Other Professional/Scientific/Technical", receipts_millions=19.5),
        SizeStandard(naics="561210", description="Facilities Support Services", receipts_millions=47.0),
        SizeStandard(naics="561612", description="Security Guards & Patrol Services", receipts_millions=29.0),
        SizeStandard(naics="561720", description="Janitorial Services", receipts_millions=22.0),
        SizeStandard(naics="518210", description="Computing Infrastructure/Data Processing", receipts_millions=40.0),
        SizeStandard(naics="517110", description="Wired Telecommunications Carriers", employees=1500),
        SizeStandard(naics="334111", description="Electronic Computer Manufacturing", employees=1250),
        SizeStandard(naics="236220", description="Commercial Building Construction", receipts_millions=45.0),
        SizeStandard(naics="238210", description="Electrical Contractors", receipts_millions=19.0),
        SizeStandard(naics="541714", description="R&D in Biotechnology", employees=1000),
        SizeStandard(naics="541715", description="R&D Physical/Engineering/Life Sciences", employees=1000),
        SizeStandard(naics="561110", description="Office Administrative Services", receipts_millions=12.5),
    ]
}


def lookup(naics: Optional[str]) -> Optional[SizeStandard]:
    if not naics:
        return None
    return _TABLE.get(naics.strip())


def is_small(
    naics: str,
    annual_receipts_avg: Optional[float],
    employee_count: Optional[int],
) -> Optional[bool]:
    """True/False when determinable against THIS NAICS's standard; None when
    the standard is unknown or the company datum is missing."""
    standard = lookup(naics)
    if standard is None:
        return None
    if standard.receipts_millions is not None:
        if annual_receipts_avg is None:
            return None
        return annual_receipts_avg <= standard.receipts_millions * 1_000_000
    if standard.employees is not None:
        if employee_count is None:
            return None
        return employee_count <= standard.employees
    return None
