"""Vision-LLM OCR fallback (A2 parsing ladder, final rung).

Scanned solicitation PDFs typically embed one full-page raster image per
page. We extract those embedded images with pypdf and transcribe them
page-by-page with a vision-capable fast-tier model. Pages that yield no
extractable image stay flagged for manual review — we never emit garbage.
"""

from __future__ import annotations

from pathlib import Path

from ..models import ParsedDoc
from .parsers import MAX_CHARS_PER_DOC, detect_sections, scan_cui

# Bound OCR cost per document; beyond this, route to manual review.
MAX_OCR_PAGES = 60

_MEDIA_TYPES = {
    "/DCTDecode": "image/jpeg",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
}


def ocr_pdf(router, path: str | Path, *, stage: str = "docproc.ocr") -> ParsedDoc | None:
    """Re-parse an image-only PDF via embedded-image transcription.

    Returns an updated ParsedDoc, or None when OCR wasn't possible
    (no embedded images / too many pages) — caller keeps the manual flag.
    """
    from pypdf import PdfReader

    path = Path(path)
    try:
        reader = PdfReader(str(path))
    except Exception:
        return None
    if len(reader.pages) > MAX_OCR_PAGES:
        return None

    page_texts: list[str] = []
    transcribed_any = False
    for page_num, page in enumerate(reader.pages, start=1):
        native = ""
        try:
            native = page.extract_text() or ""
        except Exception:
            pass
        if native.strip():
            page_texts.append(f"[page {page_num}]\n{native}")
            continue
        image = _largest_image(page)
        if image is None:
            page_texts.append(f"[page {page_num}]\n[UNREADABLE — no text and no extractable image]")
            continue
        data, media_type = image
        try:
            text = router.transcribe_image(data, media_type, stage=stage)
        except Exception:
            page_texts.append(f"[page {page_num}]\n[OCR FAILED — manual review]")
            continue
        transcribed_any = True
        page_texts.append(f"[page {page_num}]\n{text}")

    if not transcribed_any:
        return None

    full_text = "\n\n".join(page_texts)[:MAX_CHARS_PER_DOC]
    return ParsedDoc(
        name=path.name,
        kind="pdf",
        page_count=len(reader.pages),
        full_text=full_text,
        sections=detect_sections(full_text),
        ocr_used=True,
        cui_markings=scan_cui(full_text),
    )


def _largest_image(page) -> tuple[bytes, str] | None:
    """Pick the largest embedded image on a page (scans embed one per page)."""
    best: tuple[bytes, str] | None = None
    try:
        images = page.images
    except Exception:
        return None
    for img in images:
        try:
            data = img.data
        except Exception:
            continue
        if not data:
            continue
        name = (getattr(img, "name", "") or "").lower()
        media_type = "image/png"
        for key, mt in _MEDIA_TYPES.items():
            if name.endswith(key.lower()):
                media_type = mt
                break
        if data[:3] == b"\xff\xd8\xff":
            media_type = "image/jpeg"
        elif data[:8] == b"\x89PNG\r\n\x1a\n":
            media_type = "image/png"
        if best is None or len(data) > len(best[0]):
            best = (data, media_type)
    return best
