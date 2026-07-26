from bidpilot.agents.compliance import matrix_to_csv
from bidpilot.agents.cost import estimate_to_markdown
from bidpilot.agents.forms import forms_to_markdown
from bidpilot.agents.submission import instructions_to_markdown
from bidpilot.models import (
    ComplianceMatrix,
    ComplianceRow,
    CostEstimate,
    CostLineItem,
    FormItem,
    FormsPackage,
    SubmissionInstructions,
)


def test_matrix_to_csv_roundtrip():
    matrix = ComplianceMatrix(
        rows=[
            ComplianceRow(
                requirement_id="L-1.1",
                source="Section L, p.42",
                requirement_text='Offeror shall submit "Volume I" in PDF.',
                category="submission",
            )
        ]
    )
    csv_text = matrix_to_csv(matrix)
    assert "requirement_id" in csv_text.splitlines()[0]
    assert "L-1.1" in csv_text
    # Embedded quotes must survive CSV quoting
    assert "Volume I" in csv_text


def test_submission_markdown_includes_warning():
    instr = SubmissionInstructions(
        method="email",
        destination="co@agency.gov",
        deadline="2026-08-01 14:00 ET",
        confidence_notes="Amendment 2 changed the deadline — verify.",
    )
    md = instructions_to_markdown(instr)
    assert "co@agency.gov" in md
    assert "never submits" in md
    assert "Verify" in md


def test_cost_markdown_lists_human_actions():
    est = CostEstimate(
        total=100000.0,
        line_items=[CostLineItem(description="PM support", hours=100, rate=165.0, extended_cost=16500.0)],
        basis_of_estimate="Bottoms-up estimate based on scope items.",
        human_pricing_actions=["Set fee percentage"],
    )
    md = estimate_to_markdown(est)
    assert "PM support" in md
    assert "- [ ] Set fee percentage" in md
    assert "NOT a submittable price" in md


def test_forms_markdown():
    pkg = FormsPackage(
        forms=[
            FormItem(
                form_name="SF-1449",
                purpose="Solicitation/Contract/Order for Commercial Products",
                prefill={"Offeror name": "Example LLC"},
                human_actions=["Sign block 30a"],
            )
        ]
    )
    md = forms_to_markdown(pkg)
    assert "SF-1449" in md
    assert "Sign block 30a" in md
