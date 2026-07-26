from bidpilot.agents.shredder import assign_ids, dedup_requirements, matrix_to_csv
from bidpilot.models import Citation, ComplianceMatrix, Requirement, RequirementCategory


def _req(text, req_id="TBD", doc="RFP.pdf"):
    return Requirement(
        req_id=req_id,
        verbatim_text=text,
        source=Citation(doc=doc, section="L", page=4),
        category=RequirementCategory.CONTENT,
    )


def test_dedup_exact_duplicates():
    reqs = [_req("The offeror shall submit Volume I."), _req("The offeror shall submit Volume I.")]
    assert len(dedup_requirements(reqs)) == 1


def test_dedup_containment_keeps_longer_quote():
    short = _req("The offeror shall submit Volume I.")
    long = _req("The offeror shall submit Volume I in PDF format not exceeding 20 pages.")
    merged = dedup_requirements([short, long])
    assert len(merged) == 1
    assert "20 pages" in merged[0].verbatim_text


def test_dedup_keeps_distinct_requirements():
    reqs = [
        _req("The offeror shall submit Volume I."),
        _req("Proposals must be received by 2:00 PM Eastern Time."),
    ]
    assert len(dedup_requirements(reqs)) == 2


def test_assign_ids_preserves_native_and_fills_tbd():
    reqs = [_req("A", req_id="L-4.2"), _req("B"), _req("C", req_id="TBD"), _req("D", req_id="L-4.2")]
    assign_ids(reqs)
    ids = [r.req_id for r in reqs]
    assert ids[0] == "L-4.2"
    assert ids[1].startswith("REQ-") and ids[2].startswith("REQ-")
    assert ids[3].startswith("REQ-")  # collision with native id resolved
    assert len(set(ids)) == 4


def test_matrix_csv():
    matrix = ComplianceMatrix(requirements=[_req('Offeror shall use "Times New Roman".', req_id="L-1")])
    csv_text = matrix_to_csv(matrix)
    assert "req_id" in csv_text.splitlines()[0]
    assert "Times New Roman" in csv_text
