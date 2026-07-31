"""Clause scanner (A4 tooling): deterministic detection of eligibility-
relevant clauses and requirements in solicitation text. Code, not LLM —
these patterns feed the eligibility agent as hard evidence."""

from __future__ import annotations

import re

from pydantic import BaseModel


class ClauseHit(BaseModel):
    clause: str
    meaning: str
    snippet: str


_PATTERNS: list[tuple[str, str, str]] = [
    # (regex, clause label, meaning)
    (r"52\.219-3", "FAR 52.219-3", "Notice of HUBZone set-aside"),
    (r"52\.219-4", "FAR 52.219-4", "Notice of price evaluation preference — HUBZone"),
    (r"52\.219-6", "FAR 52.219-6", "Notice of total small business set-aside"),
    (r"52\.219-14", "FAR 52.219-14", "Limitations on subcontracting"),
    (r"52\.219-27", "FAR 52.219-27", "Notice of SDVOSB set-aside"),
    (r"52\.219-29", "FAR 52.219-29", "Notice of EDWOSB set-aside"),
    (r"52\.219-30", "FAR 52.219-30", "Notice of WOSB set-aside"),
    (r"52\.219-18", "FAR 52.219-18", "Notification of competition limited to 8(a)"),
    (r"252\.204-7012", "DFARS 252.204-7012", "Safeguarding covered defense info (NIST 800-171)"),
    (r"252\.204-7021", "DFARS 252.204-7021", "CMMC requirements"),
    (r"\bCMMC\b", "CMMC", "Cybersecurity Maturity Model Certification requirement"),
    (r"52\.225-5", "FAR 52.225-5", "Trade Agreements Act"),
    (r"52\.225-1", "FAR 52.225-1", "Buy American — supplies"),
    (r"52\.204-24", "FAR 52.204-24", "Representation re: covered telecom (Section 889)"),
    (r"52\.204-25", "FAR 52.204-25", "Prohibition on covered telecom equipment"),
    (r"52\.209-5", "FAR 52.209-5", "Certification re: responsibility matters"),
    (r"52\.222-41", "FAR 52.222-41", "Service Contract Act / SCLS — wage determination floors apply"),
    # Pricing-relevant clauses: each one changes how the cost volume must be
    # built, so the rate engine and pricing compliance checks key off them.
    (r"52\.222-43", "FAR 52.222-43",
     "SCA price adjustment (multi-year/option) — base price must carry NO escalation "
     "contingency for SCA wage increases; the clause is the adjustment mechanism"),
    (r"52\.222-44", "FAR 52.222-44",
     "SCA price adjustment (no options) — adjustment on a WD applied by operation of "
     "law or a statutory FLSA amendment"),
    (r"52\.222-46", "FAR 52.222-46",
     "Professional employee compensation — a Total Compensation Plan is required and "
     "an inadequate one can justify rejection"),
    (r"52\.237-10", "FAR 52.237-10",
     "Uncompensated overtime — must disclose UCOT hours/rates by category and apply "
     "the adjusted hourly rate to all proposed hours"),
    (r"52\.215-20", "FAR 52.215-20",
     "Requirement for certified cost or pricing data (or data other than certified)"),
    (r"52\.215-21", "FAR 52.215-21",
     "Requirement for cost or pricing data for modifications"),
    (r"52\.215-17", "FAR 52.215-17",
     "Waiver of facilities capital cost of money — FCCM not proposed is FCCM forfeited"),
    # The Revolutionary FAR Overhaul renumbered and reworded these; a
    # solicitation may use either vocabulary for the same requirement.
    (r"table 15-2|15\.408-2 table 15-1|certified cost or pricing data",
     "Cost or pricing data submission",
     "A structured cost submission is required (FAR 15.408 Table 15-2, renumbered "
     "Table 15-1 under the FAR overhaul)"),
    (r"total compensation plan|compensation plan for professional employees",
     "Total Compensation Plan",
     "Professional compensation plan required — salary ranges plus itemized fringe, "
     "supported by regional survey data"),
    (r"uncompensated overtime", "Uncompensated overtime",
     "Solicitation addresses uncompensated overtime — disclosure and adjusted-rate "
     "pricing required"),
    (r"wage determination", "Wage Determination", "SCA/DBA wage floors attached — rate engine must enforce"),
    (r"davis[- ]bacon", "Davis-Bacon", "Construction wage determination floors apply"),
    (r"52\.228-15", "FAR 52.228-15", "Performance and payment bonds — construction"),
    (r"bid bond|performance bond|payment bond", "Bonding", "Bid/performance/payment bond required"),
    (r"(facility|personnel) (security )?clearance|\bTOP SECRET\b|\bSECRET\b facility", "Security clearance",
     "Facility/personnel clearance requirement — hard stop for most small firms"),
    (r"\bITAR\b|export.{0,20}control", "ITAR/Export control", "Export-controlled — halt-and-notify path"),
    (r"nonmanufacturer rule|52\.219-33", "Nonmanufacturer rule", "Product buys: NMR compliance required"),
    (r"artificial intelligence.{0,120}(disclos|use)|(disclos\w*|use of).{0,60}artificial intelligence|generative AI",
     "AI-use disclosure", "Solicitation addresses AI use in proposal preparation (§14.2)"),
]


def scan_clauses(text: str) -> list[ClauseHit]:
    hits: list[ClauseHit] = []
    seen: set[str] = set()
    for pattern, clause, meaning in _PATTERNS:
        m = re.search(pattern, text, re.IGNORECASE)
        if m and clause not in seen:
            seen.add(clause)
            start = max(0, m.start() - 80)
            snippet = " ".join(text[start : m.end() + 120].split())
            hits.append(ClauseHit(clause=clause, meaning=meaning, snippet=snippet))
    return hits
