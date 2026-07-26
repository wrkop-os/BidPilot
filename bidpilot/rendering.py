"""Rendering & deterministic format verification (FR-15, A12a).

Volumes render Markdown -> DOCX (python-docx) honoring the solicitation's
font/size/margins, then — when LibreOffice (`soffice`) is available — convert
to PDF and count REAL pages with pypdf. Owning the render is what makes
page-limit verification deterministic; without soffice we fall back to a
word-count estimate and say so.

File naming follows Section L's convention where one was extracted; the
convention text itself is surfaced for human verification either way.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from .models import (
    FormatConstraints,
    QAFinding,
    QASeverity,
    RenderedVolumeInfo,
    SectionDraft,
)

WORDS_PER_PAGE_FALLBACK = 450

_DEFAULT_FONT = "Times New Roman"
_DEFAULT_SIZE_PT = 12.0
_DEFAULT_MARGIN_IN = 1.0


# ---------------------------------------------------------------------------
# Markdown -> DOCX
# ---------------------------------------------------------------------------


def render_volume_docx(
    volume: str,
    drafts: list[SectionDraft],
    constraints: FormatConstraints,
    dest: Path,
) -> RenderedVolumeInfo:
    import docx
    from docx.shared import Inches, Pt

    document = docx.Document()

    font_name = _parse_font(constraints.font) or _DEFAULT_FONT
    font_size = _parse_pt(constraints.font_size) or _DEFAULT_SIZE_PT
    margin = _parse_margin_inches(constraints.margins) or _DEFAULT_MARGIN_IN

    style = document.styles["Normal"]
    style.font.name = font_name
    style.font.size = Pt(font_size)
    for section in document.sections:
        section.top_margin = Inches(margin)
        section.bottom_margin = Inches(margin)
        section.left_margin = Inches(margin)
        section.right_margin = Inches(margin)

    document.add_paragraph("DRAFT — requires human review before submission").italic = True

    total_words = 0
    for draft in drafts:
        total_words += draft.word_count or len(draft.markdown.split())
        _markdown_to_docx(document, draft.markdown)

    dest.parent.mkdir(parents=True, exist_ok=True)
    document.save(str(dest))

    pdf_path, page_count = _convert_to_pdf(dest)
    return RenderedVolumeInfo(
        volume=volume,
        docx_path=str(dest),
        pdf_path=str(pdf_path) if pdf_path else None,
        page_count=page_count,
        estimated_pages=total_words / WORDS_PER_PAGE_FALLBACK,
        word_count=total_words,
    )


def render_all_volumes(
    drafts: list[SectionDraft],
    constraints: FormatConstraints,
    out_dir: Path,
    solicitation_number: Optional[str] = None,
    company: Optional[str] = None,
) -> list[RenderedVolumeInfo]:
    volumes: dict[str, list[SectionDraft]] = {}
    for draft in drafts:
        volumes.setdefault(draft.volume, []).append(draft)
    rendered = []
    for volume, volume_drafts in volumes.items():
        filename = volume_filename(volume, solicitation_number, company)
        rendered.append(
            render_volume_docx(volume, volume_drafts, constraints, out_dir / filename)
        )
    return rendered


def _markdown_to_docx(document, markdown: str) -> None:
    """Minimal Markdown subset: headings, bullets, plain paragraphs. HTML
    comments (requirement markers) are stripped from the rendered output but
    remain in the .md sources."""
    text = re.sub(r"<!--.*?-->", "", markdown, flags=re.DOTALL)
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue
        heading = re.match(r"^(#{1,4})\s+(.*)$", line)
        if heading:
            document.add_heading(_strip_inline(heading.group(2)), level=min(len(heading.group(1)), 4))
            continue
        bullet = re.match(r"^\s*[-*]\s+(.*)$", line)
        if bullet:
            document.add_paragraph(_strip_inline(bullet.group(1)), style="List Bullet")
            continue
        if line.lstrip().startswith("|"):
            document.add_paragraph(_strip_inline(line.strip()))
            continue
        if line.lstrip().startswith(">"):
            document.add_paragraph(_strip_inline(line.lstrip("> ")))
            continue
        document.add_paragraph(_strip_inline(line))


def _strip_inline(text: str) -> str:
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    return text.strip()


# ---------------------------------------------------------------------------
# PDF conversion + exact page counting (deterministic when soffice exists)
# ---------------------------------------------------------------------------


def soffice_available() -> bool:
    return shutil.which("soffice") is not None


def soffice_conversion_works() -> bool:
    """Probe an actual DOCX->PDF conversion — the binary can exist while the
    Writer component (DOCX filter) is missing."""
    if not soffice_available():
        return False
    import docx

    try:
        with tempfile.TemporaryDirectory() as tmp:
            probe = Path(tmp) / "probe.docx"
            d = docx.Document()
            d.add_paragraph("probe")
            d.save(str(probe))
            pdf, pages = _convert_to_pdf(probe)
            return pdf is not None and pages == 1
    except Exception:
        return False


def _convert_to_pdf(docx_path: Path) -> tuple[Optional[Path], Optional[int]]:
    if not soffice_available():
        return None, None
    try:
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(
                ["soffice", "--headless", "--convert-to", "pdf", "--outdir", tmp, str(docx_path)],
                check=True, capture_output=True, timeout=120,
            )
            produced = Path(tmp) / (docx_path.stem + ".pdf")
            if not produced.exists():
                return None, None
            final = docx_path.with_suffix(".pdf")
            shutil.copy(produced, final)
            return final, count_pdf_pages(final)
    except Exception:
        return None, None


def count_pdf_pages(pdf_path: Path) -> Optional[int]:
    try:
        from pypdf import PdfReader

        return len(PdfReader(str(pdf_path)).pages)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# File naming (Section L conventions)
# ---------------------------------------------------------------------------


def volume_filename(volume: str, solicitation_number: Optional[str], company: Optional[str]) -> str:
    """Default deterministic convention: <solnum>_<Volume_Name>.docx. The
    solicitation's own naming_convention text (freeform) is surfaced on the
    review checklist for human verification — we don't guess-parse prose."""
    parts = []
    if solicitation_number:
        parts.append(re.sub(r"[^A-Za-z0-9-]+", "", solicitation_number))
    parts.append(re.sub(r"[^A-Za-z0-9]+", "_", volume).strip("_"))
    if company:
        parts.append(re.sub(r"[^A-Za-z0-9]+", "", company)[:24])
    return "_".join(p for p in parts if p) + ".docx"


# ---------------------------------------------------------------------------
# Deterministic verification (feeds QA)
# ---------------------------------------------------------------------------


def verify_rendered(
    rendered: list[RenderedVolumeInfo], constraints: FormatConstraints
) -> list[QAFinding]:
    findings: list[QAFinding] = []
    for rv in rendered:
        limit = constraints.page_limits.get(rv.volume)
        if limit is None:
            continue
        if rv.page_count is not None:  # exact — we own the render
            if rv.page_count > limit:
                findings.append(
                    QAFinding(
                        severity=QASeverity.HARD,
                        category="format",
                        description=(
                            f"{rv.volume}: rendered PDF is {rv.page_count} pages — exceeds "
                            f"the {limit}-page limit (exact count via LibreOffice render)."
                        ),
                        location=rv.volume,
                    )
                )
        elif rv.estimated_pages > limit:
            findings.append(
                QAFinding(
                    severity=QASeverity.HARD,
                    category="format",
                    description=(
                        f"{rv.volume}: ~{rv.estimated_pages:.0f} estimated pages exceeds the "
                        f"{limit}-page limit ({rv.word_count} words; install LibreOffice for "
                        "exact page verification)."
                    ),
                    location=rv.volume,
                )
            )
        if constraints.file_formats:
            allowed = {f.lower().lstrip(".") for f in constraints.file_formats}
            if allowed and "pdf" in allowed and rv.pdf_path is None and not soffice_available():
                findings.append(
                    QAFinding(
                        severity=QASeverity.SOFT,
                        category="format",
                        description=(
                            f"{rv.volume}: solicitation requires PDF; LibreOffice not available — "
                            "human must convert the DOCX and re-verify page count."
                        ),
                        location=rv.volume,
                    )
                )
    return findings


# ---------------------------------------------------------------------------
# Constraint parsing (deterministic, conservative)
# ---------------------------------------------------------------------------


def _parse_font(font: Optional[str]) -> Optional[str]:
    if not font:
        return None
    for known in ("Times New Roman", "Arial", "Calibri", "Garamond", "Georgia", "Helvetica"):
        if known.lower() in font.lower():
            return known
    return None


def _parse_pt(size: Optional[str]) -> Optional[float]:
    if not size:
        return None
    m = re.search(r"(\d{1,2}(?:\.\d)?)\s*(?:pt|point)?", size)
    return float(m.group(1)) if m else None


def _parse_margin_inches(margins: Optional[str]) -> Optional[float]:
    if not margins:
        return None
    m = re.search(r"(\d(?:\.\d{1,2})?)\s*(?:\"|in|inch)", margins, re.IGNORECASE)
    return float(m.group(1)) if m else None
