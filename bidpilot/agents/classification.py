"""Classification agent (A3): notice type, FAR regime, required response
artifact, key dates. Cheap model + rules; low confidence escalates to a
frontier model (PRD §6.3). Deadline timezone bugs have lost real bids, so
dates are extracted timezone-explicit and cross-checked against metadata."""

from __future__ import annotations

from ..models import Classification, DocTree, NoticeMetadata, NoticeType
from ..prompting import sections_for, split_for_cache
from ..routing import ModelRouter, Tier

SYSTEM = """You classify federal contract opportunity notices. Determine:
- notice_type: sources_sought | presolicitation | solicitation |
  combined_synopsis_solicitation | amendment | award | other
- far_regime: far_15_negotiated (UCF A-M sections, Section L/M) |
  far_12_commercial (SF-1449, 52.212-1/-2) | far_13_simplified |
  far_14_sealed_bid (IFB, SF-33/SF-1447 price-only) | unknown
- response_artifact: what must be produced — full_proposal | quote |
  capability_statement (sources sought) | sealed_bid | none_monitor
- key_dates: every deadline (questions due, proposal due) with the timezone
  EXACTLY as stated; if no timezone is stated, note that in the date string.
- ai_disclosure_clause_detected: true if the solicitation asks offerors to
  disclose AI use in proposal preparation.
- submission_channel_hint: email / PIEE / eBuy / FedConnect / Unison / etc. if visible.
Set confidence 0-1 honestly. Explain your rationale briefly."""

# Rules first: SAM.gov's own type field maps directly for the easy cases.
_TYPE_MAP = {
    "sources sought": NoticeType.SOURCES_SOUGHT,
    "presolicitation": NoticeType.PRESOLICITATION,
    "solicitation": NoticeType.SOLICITATION,
    "combined synopsis/solicitation": NoticeType.COMBINED_SYNOPSIS,
    "combined synopsis": NoticeType.COMBINED_SYNOPSIS,
    "award notice": NoticeType.AWARD,
}

CONFIDENCE_ESCALATION_THRESHOLD = 0.75


def classify(router: ModelRouter, metadata: NoticeMetadata, doc_tree: DocTree) -> Classification:
    _corpus = split_for_cache(doc_tree.corpus(), 150000, doc_tree,
                              sections_for("classify"))
    prompt = f"""Classify this opportunity.

SAM.gov metadata:
- type field: {metadata.notice_type_raw or "unknown"}
- title: {metadata.title or "unknown"}
- response deadline per SAM.gov: {metadata.response_deadline or "unknown"}
- set-aside: {metadata.set_aside or "none"}

Corpus (first 150k chars):
{_corpus.tail}"""

    result = router.structured(
        Tier.FAST, system=SYSTEM, prompt=prompt, output_type=Classification,
        stage="classify", cache_prefix=_corpus.head,
    )

    # Rules override: trust SAM.gov's own type field when it maps cleanly and
    # the model disagreed.
    mapped = _TYPE_MAP.get((metadata.notice_type_raw or "").strip().lower())
    if mapped and result.notice_type != mapped and mapped != NoticeType.SOLICITATION:
        result.notice_type = mapped
        result.rationale = (result.rationale or "") + " [overridden by SAM.gov type field]"

    # Escalation: low confidence goes to the frontier model.
    if result.confidence < CONFIDENCE_ESCALATION_THRESHOLD:
        result = router.structured(
            Tier.FRONTIER, system=SYSTEM, prompt=prompt, output_type=Classification,
            cache_prefix=_corpus.head,
            stage="classify.escalated",
        )
        result.escalated = True
    return result
