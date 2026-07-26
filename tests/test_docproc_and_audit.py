from bidpilot.audit import AuditLog, prompt_hash
from bidpilot.docproc import detect_sections, scan_cui


def test_detect_ucf_sections():
    text = (
        "SECTION C - DESCRIPTION/SPECIFICATIONS\nThe contractor shall provide IT services.\n"
        "SECTION L - INSTRUCTIONS TO OFFERORS\nProposals shall be organized in three volumes.\n"
        "SECTION M - EVALUATION FACTORS\nAward will be made on a best value basis.\n"
    )
    sections = detect_sections(text)
    ids = [s.section_id for s in sections]
    assert ids == ["C", "L", "M"]
    assert "three volumes" in sections[1].text


def test_detect_attachment_headers():
    text = "ATTACHMENT 3 - PERFORMANCE WORK STATEMENT\nThe contractor shall..."
    sections = detect_sections(text)
    assert len(sections) == 1
    assert "ATTACHMENT 3" in sections[0].title


def test_scan_cui_hits_and_clean():
    assert "CUI" in scan_cui("This document contains CUI and is export-controlled.")
    marked = scan_cui("CONTROLLED UNCLASSIFIED INFORMATION // ITAR")
    assert len(marked) >= 2
    assert scan_cui("A perfectly ordinary public solicitation.") == []


def test_audit_log_roundtrip(tmp_path):
    log = AuditLog(tmp_path / "audit.jsonl")
    log.record("stage_start", actor="orchestrator", stage="intake")
    log.record(
        "llm_call", actor="model", stage="shred.extract", model="claude-haiku-4-5",
        prompt_sha256=prompt_hash("system", "prompt"), tokens_in=100, tokens_out=50,
    )
    log.human_action("bid_no_bid", approved=True, actor="jrivera")
    entries = log.entries()
    assert len(entries) == 3
    assert entries[1]["model"] == "claude-haiku-4-5"
    assert entries[2]["detail"]["gate"] == "bid_no_bid"


def test_prompt_hash_stable_and_sensitive():
    assert prompt_hash("a", "b") == prompt_hash("a", "b")
    assert prompt_hash("a", "b") != prompt_hash("ab", "")
