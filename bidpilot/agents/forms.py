"""Forms & Certifications agent (A10): identify every required form, prefill
administrative fields from the profile, and produce a "signature required
here" checklist. Never signs anything; never pre-answers a certification."""

from __future__ import annotations

from ..kb.store import KnowledgeBase
from ..models import DocTree, FormsPackage, NoticePackage
from ..routing import ModelRouter, Tier

SYSTEM = """You are a contracts administrator preparing the forms package for a
federal proposal.
- Identify every required form: SF-33/SF-1449/SF-18 first page, SF-30 amendment
  acknowledgments (one per amendment in the chain provided), solicitation-
  specific representations (52.204-24/25/26 covered telecom, 52.209-5,
  Section K / 52.212-3 reps & certs), subcontracting plan if applicable.
- prefill: ONLY administrative fields mapping directly from the company profile
  (name, UEI, CAGE, address, POC). NEVER pre-answer a certification or
  representation — those are human-only legal acts.
- signature_required + human_actions: every signature block, certification
  answer, and amendment acknowledgment a human must execute.
- If reps & certs are maintained in SAM.gov, note the human must verify the SAM
  record is current."""


def prepare_forms(
    router: ModelRouter, notice: NoticePackage, doc_tree: DocTree, kb: KnowledgeBase
) -> FormsPackage:
    amendments = "\n".join(
        f"- {a.notice_id} posted {a.posted_date or '?'}" for a in notice.amendment_history
    )
    prompt = f"""Prepare the forms package.

=== AMENDMENT CHAIN (each needs acknowledgment) ===
{amendments or "(no amendments)"}

=== COMPANY PROFILE ===
{kb.profile_text()}

=== SOLICITATION CORPUS ===
{doc_tree.corpus()[:300_000]}"""
    return router.structured(
        Tier.FRONTIER, system=SYSTEM, prompt=prompt, output_type=FormsPackage, stage="forms",
    )


def forms_to_markdown(pkg: FormsPackage) -> str:
    lines = ["# Forms & Certifications Checklist", ""]
    for form in pkg.forms:
        lines.append(f"## {form.form_name}")
        lines.append(f"*{form.purpose}*")
        if form.prefill:
            lines.append("\n**Pre-filled (administrative fields only):**")
            lines += [f"- {k}: {v}" for k, v in form.prefill.items()]
        if form.signature_required:
            lines.append("\n**✍️ SIGNATURE REQUIRED**")
        if form.human_actions:
            lines.append("\n**⚠️ Human must:**")
            lines += [f"- [ ] {a}" for a in form.human_actions]
        lines.append("")
    if pkg.amendment_acknowledgments:
        lines.append("## Amendment acknowledgments")
        lines += [f"- [ ] {a}" for a in pkg.amendment_acknowledgments]
    if pkg.notes:
        lines.append("\n## Notes")
        lines += [f"- {n}" for n in pkg.notes]
    lines.append("\n---\n*BidPilot never signs. Certifications are human-only legal acts (§14.1).*")
    return "\n".join(lines)
