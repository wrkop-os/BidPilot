"""Portal-specific submission playbooks (PRD Phase 5.3).

Static, deterministic guidance packs keyed off the extracted submission
channel. The system still never submits — these are the mechanical steps and
known gotchas a human walks through for each delivery mechanism, appended to
the Submission Instruction Sheet.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class PortalPlaybook(BaseModel):
    channel: str
    display_name: str
    steps: list[str] = Field(default_factory=list)
    gotchas: list[str] = Field(default_factory=list)


_PLAYBOOKS: dict[str, PortalPlaybook] = {
    "email": PortalPlaybook(
        channel="email",
        display_name="Email to the Contracting Officer / Specialist",
        steps=[
            "Send from an address the government can reply to; confirm the destination address character-for-character against the latest amendment",
            "Use the exact subject line format if one is prescribed",
            "Attach files named per Section L; verify total size against the stated attachment limit BEFORE the deadline window",
            "If the package exceeds the size limit, split across sequentially-numbered emails only if the solicitation permits it — otherwise contact the CO ahead of time",
            "Request a read receipt AND send a follow-up asking the CO to confirm receipt",
            "Archive the sent email (with full headers) as proof of timely delivery",
        ],
        gotchas=[
            "Government mail gateways silently drop oversized or macro-bearing attachments — a bounced or dropped email at 13:59 for a 14:00 deadline is a lost bid",
            "'Received by the Government server' is the timeliness standard (FAR 52.212-1(f)/15.208) — send hours early, not minutes",
            "Zip archives are frequently quarantined; prefer individual PDFs unless zips are requested",
        ],
    ),
    "piee": PortalPlaybook(
        channel="piee",
        display_name="PIEE Solicitation Module (DoD)",
        steps=[
            "Verify the company's PIEE account is active and a user holds the 'Proposal Manager' role for the Solicitation Module — registration can take days; start immediately",
            "Locate the solicitation in the module and confirm it matches the latest amendment number",
            "Upload each volume to the correct slot; watch per-file size limits",
            "Submit and download/print the PIEE submission confirmation with timestamp",
        ],
        gotchas=[
            "PIEE role approval is human-in-the-loop on the government side — it is NOT same-day",
            "An uploaded-but-not-submitted proposal is not received; the confirmation screen/receipt is the only proof",
        ],
    ),
    "ebuy": PortalPlaybook(
        channel="ebuy",
        display_name="GSA eBuy",
        steps=[
            "Confirm the company holds the required GSA Schedule/SIN — eBuy is schedule-holders only",
            "Respond to the RFQ within the eBuy interface; upload attachments there",
            "Verify the quote shows as 'submitted' before the closing date/time (eBuy closes hard)",
        ],
        gotchas=[
            "eBuy deadlines are enforced by the system clock — there is no late-is-late argument to make afterward",
        ],
    ),
    "fedconnect": PortalPlaybook(
        channel="fedconnect",
        display_name="FedConnect",
        steps=[
            "Register the company (tied to UEI) and join the opportunity's response team",
            "Compose the response in the message center and attach volumes",
            "Submit and save the delivery receipt",
        ],
        gotchas=[
            "FedConnect registration requires SAM data to sync — allow 24-48h for a first-time account",
        ],
    ),
    "unison": PortalPlaybook(
        channel="unison",
        display_name="Unison Marketplace (reverse auction)",
        steps=[
            "Register/verify the seller account and locate the auction lane",
            "Enter pricing as bids during the auction window; monitor for lead changes",
            "Upload any required technical documentation before the lane closes",
        ],
        gotchas=[
            "This is a REVERSE AUCTION — the human pricing lead must set a walk-away floor before bidding starts, not during",
        ],
    ),
    "sam_gov": PortalPlaybook(
        channel="sam_gov",
        display_name="SAM.gov quote submission",
        steps=[
            "Confirm the notice actually accepts responses through SAM.gov (rare — most notices are posting-only)",
            "Submit through the notice's response interface with the preparer logged into the entity-linked account",
            "Screenshot/save the submission confirmation",
        ],
        gotchas=[
            "SAM.gov is where opportunities are POSTED; verify twice that submission truly happens here before relying on it",
        ],
    ),
    "physical": PortalPlaybook(
        channel="physical",
        display_name="Physical delivery",
        steps=[
            "Confirm copy counts, media (paper/USB), packaging, and labeling per Section L",
            "Use a courier with tracking and signature; deliver at least one business day early",
            "Account for base/building access and security screening time in the delivery plan",
            "Obtain a signed/stamped receipt with date and time",
        ],
        gotchas=[
            "Gate access and visitor processing routinely add hours — a courier stuck at the gate at the deadline is a late proposal",
        ],
    ),
}

_ALIASES = {
    "email": "email", "e-mail": "email", "mail to": "email",
    "piee": "piee", "solicitation module": "piee", "wawf": "piee",
    "ebuy": "ebuy", "e-buy": "ebuy", "gsa ebuy": "ebuy",
    "fedconnect": "fedconnect",
    "unison": "unison", "reverse auction": "unison", "fedbid": "unison",
    "sam.gov": "sam_gov", "sam_gov": "sam_gov", "sam gov": "sam_gov",
    "physical": "physical", "hand deliver": "physical", "hand-carry": "physical",
    "courier": "physical", "hard copy": "physical",
}


def playbook_for(channel: str | None) -> PortalPlaybook | None:
    """Fuzzy-match the extracted channel text to a playbook; None when the
    channel is unknown (the sheet already tells the human to call the CO)."""
    if not channel:
        return None
    needle = channel.strip().lower()
    if needle in _PLAYBOOKS:
        return _PLAYBOOKS[needle]
    for alias, key in _ALIASES.items():
        if alias in needle:
            return _PLAYBOOKS[key]
    return None


def playbook_markdown(playbook: PortalPlaybook) -> str:
    lines = [f"\n## Delivery playbook — {playbook.display_name}"]
    lines += [f"{i}. {step}" for i, step in enumerate(playbook.steps, start=1)]
    if playbook.gotchas:
        lines.append("\n**Known gotchas:**")
        lines += [f"- ⚠️ {g}" for g in playbook.gotchas]
    return "\n".join(lines)
