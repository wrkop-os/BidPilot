"""Compliance agent: build the requirement-by-requirement compliance matrix.

A large fraction of federal proposals are rejected without evaluation for
compliance failures (format, missing form, late, page limits) — this matrix
is the backbone of the whole package.
"""

from __future__ import annotations

from ..llm import LLM
from ..models import ComplianceMatrix, SolicitationAnalysis

SYSTEM = """You are a proposal compliance manager. From the solicitation corpus and \
its structured analysis, build an exhaustive compliance matrix: every "shall", \
"must", "will provide", instruction-to-offerors item, format rule, page limit, \
submission rule, required form, and evaluation criterion the proposal must satisfy.

Rules:
- One row per discrete requirement; split compound sentences into separate rows.
- requirement_id: use the solicitation's own numbering where it exists (e.g. \
'L-4.2', 'PWS 3.1.5'); otherwise assign sequential IDs like 'REQ-001'.
- source: cite the document and section/page so a human can verify.
- category must be one of: submission, format, content, evaluation, contractual, forms.
- status: 'human_action_required' for anything only a human can do (signatures, \
certifications, pricing decisions); otherwise 'open'.
- Do not paraphrase away binding language — keep 'shall'/'must' phrasing intact."""


def build_compliance_matrix(
    llm: LLM, analysis: SolicitationAnalysis, corpus: str
) -> ComplianceMatrix:
    prompt = f"""Build the complete compliance matrix.

=== STRUCTURED ANALYSIS (JSON) ===
{analysis.model_dump_json(indent=2)}

=== FULL SOLICITATION CORPUS ===
{corpus}"""
    return llm.structured(
        system=SYSTEM,
        prompt=prompt,
        output_type=ComplianceMatrix,
        max_tokens=64000,
    )


def matrix_to_csv(matrix: ComplianceMatrix) -> str:
    import csv
    import io

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["requirement_id", "source", "category", "requirement_text", "proposal_location", "status", "notes"]
    )
    for row in matrix.rows:
        writer.writerow(
            [
                row.requirement_id,
                row.source,
                row.category,
                row.requirement_text,
                row.proposal_location or "",
                row.status,
                row.notes or "",
            ]
        )
    return buf.getvalue()
