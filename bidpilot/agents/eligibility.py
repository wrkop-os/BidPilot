"""Eligibility agent (A4): the §2.3 checklist against the company profile.

Tools feeding the LLM as hard evidence (code, not model guesses):
- SBA size standard lookup by NAICS (data/sba_size_standards.py)
- Clause scanner hits (data/clause_patterns.py)
- Entity Management API self-check (registration active, not excluded)

Output is never a bare yes/no (FR-5): hard blockers, soft risks, missing
info, and a bid/no-bid recommendation with confidence and reasoning.
"""

from __future__ import annotations

from typing import Optional

from ..data import sba_size_standards
from ..data.clause_patterns import scan_clauses
from ..intake.samgov import SamGovClient
from ..kb.store import KnowledgeBase
from ..models import Classification, DocTree, EligibilityReport, NoticeMetadata
from ..routing import ModelRouter, Tier

SYSTEM = """You are a federal contracting eligibility analyst. Evaluate ALL of:
1. Registration — active SAM registration, UEI/CAGE, exclusions (use the
   Entity-API evidence provided; if absent, list as missing_info).
2. Set-aside qualification — match the notice's set-aside against the company's
   certifications. Missing a required certification is a HARD BLOCKER.
3. Size standard — use the SBA lookup evidence provided for THIS NAICS. If the
   company is other-than-small under this NAICS on a set-aside notice: hard blocker.
   If size data is stale or the standard is unknown: missing_info, not a guess.
4. Special regimes — from the clause-scan evidence: limitations on subcontracting,
   nonmanufacturer rule, Buy American/TAA, ITAR, facility/personnel clearances
   (clearance the company lacks = hard blocker), bonding, CMMC/DFARS 7012.
5. Practical capability screens (soft) — place of performance, past performance
   depth vs. stated requirements, key personnel, incumbent signals.

Be conservative: anything not determinable from the evidence goes to
missing_info. Hard blockers must be unambiguous. bid_recommendation:
'no_bid' only on hard blockers; 'conditional' when soft risks/missing info
dominate; 'bid' when clean."""


def check_eligibility(
    router: ModelRouter,
    metadata: NoticeMetadata,
    classification: Classification,
    doc_tree: DocTree,
    kb: KnowledgeBase,
    sam: Optional[SamGovClient] = None,
) -> EligibilityReport:
    profile = kb.profile
    corpus = doc_tree.corpus()

    # -- deterministic evidence gathering (code, not LLM) ----------------------
    clause_hits = scan_clauses(corpus)
    naics = metadata.naics_code
    standard = sba_size_standards.lookup(naics)
    small = sba_size_standards.is_small(
        naics or "", profile.annual_receipts_avg, profile.employee_count
    )
    size_evidence = (
        f"NAICS {naics}: standard = "
        + (
            f"${standard.receipts_millions}M receipts" if standard and standard.receipts_millions
            else f"{standard.employees} employees" if standard and standard.employees
            else "UNKNOWN (not in size-standard table — flag for human lookup)"
        )
        + f"; company small under this NAICS: {small if small is not None else 'UNDETERMINED'}"
        + (f"; size data last verified {profile.size_data_verified_date or 'NEVER — stale per FR-7'}")
    )

    entity_evidence = "Entity API not checked (no API key or no UEI on file)."
    if sam and profile.uei:
        status = sam.entity_status(profile.uei)
        if status:
            entity_evidence = f"Entity API result for UEI {profile.uei}: {status}"
        else:
            # NOT verified is different from verified-and-clean. Say which,
            # so the reviewer knows this check still has to happen by hand.
            reason = getattr(sam, "last_entity_error", None)
            entity_evidence = (
                f"Entity API NOT verified for UEI {profile.uei}"
                + (f" — {reason}" if reason else "")
                + ". Registration status and exclusions are UNCONFIRMED; "
                "check SAM.gov manually before submitting."
            )

    clause_evidence = "\n".join(
        f"- {h.clause}: {h.meaning} :: …{h.snippet}…" for h in clause_hits
    ) or "(no eligibility-relevant clauses detected by the scanner)"

    prompt = f"""Evaluate eligibility.

=== NOTICE ===
Set-aside: {metadata.set_aside or "none stated"}
NAICS: {naics or "unknown"} | PSC: {metadata.psc_code or "unknown"}
Place of performance: {metadata.place_of_performance or "unknown"}
Classification: {classification.model_dump_json()}

=== DETERMINISTIC EVIDENCE (trust this over the corpus) ===
[SIZE STANDARD] {size_evidence}
[ENTITY API] {entity_evidence}
[CLAUSE SCAN]
{clause_evidence}

=== COMPANY PROFILE ===
{kb.profile_text()}

=== PAST PERFORMANCE SUMMARY ===
{kb.past_performance_text()}

=== SOLICITATION CORPUS (for requirements the scanner can't see) ===
{corpus[:300_000]}"""

    return router.structured(
        Tier.FRONTIER,
        system=SYSTEM,
        prompt=prompt,
        output_type=EligibilityReport,
        max_tokens=16000,
        stage="eligibility",
    )
