"""Phase-0 corpus collection (PRD Appendix A, Week 1).

Snapshot a SAM.gov notice into the frozen eval corpus: API JSON, all
attachments across the amendment chain, and a gold-matrix CSV template ready
for the Week-2 manual gold build.

Usage:  python -m evals.collect <sam.gov URL or notice id> <slug>
Creates: evals/corpus/<slug>/{api_snapshot.json, attachments/, gold_matrix.csv}
"""

from __future__ import annotations

import sys
from pathlib import Path

CORPUS_ROOT = Path(__file__).resolve().parent / "corpus"

GOLD_HEADER = "req_id,category,source,verbatim_text,owner_section,notes\n"


def collect(url_or_id: str, slug: str) -> Path:
    from bidpilot.intake import SamGovClient, run_intake

    dest = CORPUS_ROOT / slug
    if dest.exists():
        raise SystemExit(f"{dest} already exists — corpus items are frozen; pick a new slug.")
    dest.mkdir(parents=True)

    sam = SamGovClient(cache_dir=dest / "api_cache")
    package = run_intake(sam, url_or_id, dest / "attachments")

    (dest / "api_snapshot.json").write_text(package.model_dump_json(indent=2), encoding="utf-8")
    (dest / "gold_matrix.csv").write_text(GOLD_HEADER, encoding="utf-8")
    (dest / "NOTES.md").write_text(
        f"# {package.metadata.title or slug}\n\n"
        f"- notice: {package.metadata.notice_id}\n"
        f"- solicitation #: {package.metadata.solicitation_number}\n"
        f"- NAICS: {package.metadata.naics_code} | set-aside: {package.metadata.set_aside}\n"
        f"- attachments: {len(package.files)} | amendments: {len(package.amendment_history)}\n\n"
        "Gold build (Week 2): fill gold_matrix.csv by hand — one row per binding\n"
        "requirement, verbatim text, source page. Record your build time here for\n"
        "the ROI baseline:\n\n- human matrix build time: ___ hours\n",
        encoding="utf-8",
    )
    return dest


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    dest = collect(argv[0], argv[1])
    print(f"Corpus item frozen at {dest}")
    print("Next: hand-build gold_matrix.csv (Week-2 gold build), then score runs with evals.harness.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
