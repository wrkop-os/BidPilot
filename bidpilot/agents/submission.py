"""Submission agent: extract the who/where/how/when of proposal delivery.

Critical premise: SAM.gov is where opportunities are posted, but for the vast
majority of contracts it is NOT where proposals are submitted. Proposals go
by email to the Contracting Officer, or through PIEE, GSA eBuy, FedConnect,
Unison Marketplace, etc., exactly as the solicitation instructs. BidPilot's
output is a submission package plus these machine-extracted instructions —
never an automated upload.
"""

from __future__ import annotations

from ..llm import LLM
from ..models import SolicitationAnalysis, SubmissionInstructions

SYSTEM = """You extract proposal submission instructions from federal solicitations \
with zero tolerance for guessing. The output tells a human exactly who to send the \
proposal to, where, how, by when, and in what format.

Rules:
- Quote email addresses, portal names/URLs, deadlines, and file-format rules \
exactly as written.
- If the solicitation and the SAM.gov notice disagree (e.g. on the deadline), \
report the solicitation's version and flag the conflict in confidence_notes.
- If the delivery method is ambiguous or split across documents/amendments, say \
so explicitly in confidence_notes — a human must verify before submitting.
- Never invent a destination. If none is stated, set method='unknown' and \
destination='NOT FOUND — human must contact the Contracting Officer'."""


def extract_submission_instructions(
    llm: LLM, analysis: SolicitationAnalysis, corpus: str
) -> SubmissionInstructions:
    prompt = f"""Extract the submission instructions.

=== STRUCTURED ANALYSIS (JSON) ===
{analysis.model_dump_json(indent=2)}

=== FULL SOLICITATION CORPUS ===
{corpus}"""
    return llm.structured(
        system=SYSTEM,
        prompt=prompt,
        output_type=SubmissionInstructions,
    )


def instructions_to_markdown(instr: SubmissionInstructions) -> str:
    lines = [
        "# Submission Instructions (machine-extracted — VERIFY BEFORE SUBMITTING)",
        "",
        f"- **Method:** {instr.method}",
        f"- **Destination:** {instr.destination}",
        f"- **Deadline:** {instr.deadline}",
    ]
    if instr.contacts:
        lines.append("\n## Contacts")
        lines += [f"- {c}" for c in instr.contacts]
    if instr.format_rules:
        lines.append("\n## Format rules")
        lines += [f"- {r}" for r in instr.format_rules]
    if instr.special_instructions:
        lines.append("\n## Special instructions")
        lines += [f"- {s}" for s in instr.special_instructions]
    if instr.confidence_notes:
        lines.append("\n## ⚠️ Verify")
        lines.append(instr.confidence_notes)
    lines.append(
        "\n---\n*BidPilot never submits proposals. A human must verify these "
        "instructions against the solicitation and deliver the package.*"
    )
    return "\n".join(lines)
