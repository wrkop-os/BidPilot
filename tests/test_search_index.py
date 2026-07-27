from bidpilot.docproc.index import SearchIndex
from bidpilot.models import DocTree, ParsedDoc


def _tree() -> DocTree:
    pws = (
        "[page 1]\nPERFORMANCE WORK STATEMENT\n"
        "3.1 Help Desk Services. The contractor shall operate a Tier 1 help desk "
        "handling incident tickets and service requests between 0700 and 1900 ET. "
        + "Filler about administrative matters. " * 60
        + "\n[page 2]\n3.2 Network Operations. The contractor shall monitor the "
        "enterprise network, maintain firewalls, and patch network devices monthly. "
        + "More general contract boilerplate text. " * 60
        + "\n[page 3]\n3.3 Application Development. The contractor shall develop and "
        "sustain web applications using agile sprints with two-week iterations. "
        + "Yet more boilerplate about invoicing. " * 60
    )
    return DocTree(docs=[
        ParsedDoc(name="PWS.pdf", kind="pdf", full_text=pws),
        ParsedDoc(name="wage_determination.pdf", kind="pdf",
                  full_text="[page 1]\nWage determination rates for Computer Operator..."),
    ])


def test_search_ranks_relevant_chunk_first():
    index = SearchIndex.from_doc_tree(_tree())
    results = index.search("help desk incident tickets Tier 1")
    assert results, "expected matches"
    top = results[0][1]
    assert "help desk" in top.text.lower()
    assert top.doc == "PWS.pdf"


def test_excerpts_labeled_and_budgeted():
    index = SearchIndex.from_doc_tree(_tree())
    excerpts = index.excerpts("network firewalls patch devices", budget_chars=3000)
    assert len(excerpts) <= 3000
    assert excerpts.startswith("[PWS.pdf")
    assert "firewalls" in excerpts
    # help-desk chunk should not outrank the network chunk for this query
    assert excerpts.index("Network Operations") < len(excerpts)


def test_excerpts_fallback_when_no_match():
    index = SearchIndex.from_doc_tree(_tree())
    excerpts = index.excerpts("zzz qqq xyzzy", budget_chars=2500)
    assert excerpts  # never empty — falls back to corpus head
    assert len(excerpts) <= 2500


def test_page_metadata_carried():
    index = SearchIndex.from_doc_tree(_tree())
    results = index.search("agile sprints two-week iterations web applications")
    assert results[0][1].page in (2, 3)  # nearest marker at or before the chunk


def test_empty_tree():
    index = SearchIndex.from_doc_tree(DocTree())
    assert index.search("anything") == []
    assert index.excerpts("anything") == ""
