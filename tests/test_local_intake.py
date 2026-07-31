"""Local intake: a folder of documents instead of a SAM.gov URL.

The point of this path is that the product stays usable when the API is not:
restricted networks, spent rate limits, login-gated attachments, packages that
were never on SAM.gov at all.
"""

from pathlib import Path

import pytest

from bidpilot.intake.local import (
    LocalIntakeError,
    load_local_package,
    local_run_key,
    missing_metadata,
)

SOLICITATION = """\
COMBINED SYNOPSIS/SOLICITATION
Solicitation Number: 47QFCA26R0031
NAICS Code: 541519
PSC: D399
This requirement is a Total Small Business Set-Aside.
Offers due: March 14, 2027 at 2:00 PM ET

The contractor shall provide Tier 1 and Tier 2 service desk support.
52.222-41 Service Contract Labor Standards apply.
"""


def _package_dir(tmp_path: Path, meta: str | None = None, body: str = SOLICITATION) -> Path:
    src = tmp_path / "RFQ_Service_Desk"
    src.mkdir()
    (src / "solicitation.txt").write_text(body)
    if meta is not None:
        (src / "notice.yaml").write_text(meta)
    return src


# -- extraction ---------------------------------------------------------------


def test_metadata_is_extracted_deterministically_from_the_documents(tmp_path):
    pkg = load_local_package(_package_dir(tmp_path), tmp_path / "att")
    m = pkg.metadata
    assert m.solicitation_number == "47QFCA26R0031"
    assert m.naics_code == "541519"
    assert m.psc_code == "D399"
    assert m.set_aside == "Total Small Business Set-Aside"
    assert m.response_deadline == "March 14, 2027 at 2:00 PM ET"
    assert m.title == "RFQ Service Desk"          # from the folder name
    assert m.raw_api_record["source"] == "local"


def test_the_solicitation_label_is_never_mistaken_for_its_value(tmp_path):
    """The header word 'SOLICITATION' sits directly above 'Solicitation
    Number:'. Case-insensitive matching once captured the label itself."""
    m = load_local_package(_package_dir(tmp_path), tmp_path / "att").metadata
    assert m.solicitation_number != "Solicitation"
    assert any(ch.isdigit() for ch in m.solicitation_number)


def test_the_deadline_keeps_its_time_and_zone(tmp_path):
    """A federal deadline is a timestamp. Dropping '2:00 PM ET' would silently
    hand the bidder an extra day that does not exist."""
    m = load_local_package(_package_dir(tmp_path), tmp_path / "att").metadata
    assert "2:00" in m.response_deadline and "ET" in m.response_deadline


@pytest.mark.parametrize("body,expected", [
    ("Solicitation No: W912DY-27-R-0042\n", "W912DY-27-R-0042"),
    ("RFP #: 70Z03826QAB123456\n", "70Z03826QAB123456"),
    ("Due date: 2027-03-14\n", None),                  # no solicitation number present
])
def test_extraction_finds_real_identifiers_and_invents_nothing(tmp_path, body, expected):
    m = load_local_package(_package_dir(tmp_path, body=body), tmp_path / "att").metadata
    assert m.solicitation_number == expected


def test_declared_metadata_beats_extraction(tmp_path):
    src = _package_dir(tmp_path, meta="naics_code: '541512'\nagency: GSA FAS\n")
    m = load_local_package(src, tmp_path / "att").metadata
    assert m.naics_code == "541512"                    # human wins over the regex
    assert m.agency == "GSA FAS"
    assert m.solicitation_number == "47QFCA26R0031"    # extraction still fills the rest


def test_unresolved_fields_are_reported_not_guessed(tmp_path):
    src = _package_dir(tmp_path, body="Some scope text with no identifiers at all.\n")
    m = load_local_package(src, tmp_path / "att").metadata
    assert m.response_deadline is None and m.naics_code is None
    gaps = missing_metadata(m)
    # Ordered by consequence: deadline first, then NAICS.
    assert "response_deadline" in gaps[0] and "NAICS" in gaps[1]
    assert any("size standard" in g for g in gaps)


# -- run identity -------------------------------------------------------------


def test_run_key_is_content_derived_so_the_same_folder_resumes(tmp_path):
    src = _package_dir(tmp_path)
    key = local_run_key(src)
    assert len(key) == 32 and all(c in "0123456789abcdef" for c in key)
    assert local_run_key(src) == key            # stable across calls


def test_adding_an_amendment_document_starts_a_new_run(tmp_path):
    """Local intake has no amendment chain to consult, so a changed document
    set must not silently resume the old run's checkpoint."""
    src = _package_dir(tmp_path)
    before = local_run_key(src)
    (src / "amendment_0001.txt").write_text("Amendment 1: deadline extended.\n")
    assert local_run_key(src) != before


def test_metadata_file_is_not_ingested_as_a_solicitation_document(tmp_path):
    pkg = load_local_package(_package_dir(tmp_path, meta="agency: GSA\n"), tmp_path / "att")
    assert [f.name for f in pkg.files] == ["solicitation.txt"]


# -- failure modes ------------------------------------------------------------


def test_an_empty_or_missing_folder_says_what_is_wrong(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(LocalIntakeError, match="No readable solicitation documents"):
        load_local_package(empty, tmp_path / "att")
    with pytest.raises(LocalIntakeError, match="not a directory"):
        load_local_package(tmp_path / "nope", tmp_path / "att")


def test_a_typo_in_notice_yaml_fails_loudly_instead_of_being_ignored(tmp_path):
    """Silently dropping 'naics' because the field is 'naics_code' would ship a
    proposal screened against the wrong size standard."""
    src = _package_dir(tmp_path, meta="naics: '541512'\n")
    with pytest.raises(LocalIntakeError, match="unrecognized field"):
        load_local_package(src, tmp_path / "att")


def test_malformed_notice_yaml_names_the_file(tmp_path):
    src = _package_dir(tmp_path, meta="agency: [unclosed\n")
    with pytest.raises(LocalIntakeError, match="notice.yaml"):
        load_local_package(src, tmp_path / "att")


def test_documents_are_copied_into_the_run_and_hashed(tmp_path):
    dest = tmp_path / "att"
    pkg = load_local_package(_package_dir(tmp_path), dest)
    record = pkg.files[0]
    assert Path(record.local_path).parent == dest
    assert Path(record.local_path).exists()
    assert len(record.sha256) == 64
    assert record.restricted is False          # nothing is login-gated locally
    assert pkg.restricted_files_flagged is False
