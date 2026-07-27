"""Fillable-PDF form filling (A10 mechanics).

Government PDFs are inconsistent: some have AcroForm fields, many don't.
For fillable ones, code lists the field names, the frontier model proposes a
field->value mapping from the company profile (administrative fields ONLY —
certifications and signatures are never touched), and pypdf applies it to a
copy. Non-fillable forms stay on the human checklist.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from ..kb.store import KnowledgeBase
from ..models import FormsPackage
from ..routing import ModelRouter, Tier

MAPPING_SYSTEM = """You map a company's administrative data into a fillable
government PDF form's fields. You get the form's field names and the company
profile.

Rules:
- Fill ONLY administrative identity fields: company name, UEI, CAGE, address,
  POC name/email/phone, DUNS-style identifiers.
- NEVER fill: certification answers (yes/no representation boxes), signature
  fields, date-signed fields, pricing fields, or anything you are not certain
  about. Wrong prefill on a representation is worse than blank.
- Only map fields whose meaning is unambiguous from the field name.
- Return an empty mapping if the field names are too cryptic to map safely."""


class FieldMapping(BaseModel):
    fields: dict[str, str] = Field(default_factory=dict, description="PDF field name -> value")
    skipped_reason: Optional[str] = None


def list_form_fields(pdf_path: Path) -> Optional[list[str]]:
    """Field names of a fillable PDF, or None if it has no AcroForm fields."""
    from pypdf import PdfReader

    try:
        reader = PdfReader(str(pdf_path))
        fields = reader.get_fields()
    except Exception:
        return None
    if not fields:
        return None
    return sorted(fields.keys())


def fill_pdf(pdf_path: Path, mapping: dict[str, str], dest_path: Path) -> Optional[Path]:
    """Apply a field mapping to a copy of the PDF."""
    from pypdf import PdfReader, PdfWriter

    if not mapping:
        return None
    try:
        reader = PdfReader(str(pdf_path))
        writer = PdfWriter()
        writer.append(reader)
        for page in writer.pages:
            writer.update_page_form_field_values(page, mapping)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        with open(dest_path, "wb") as fh:
            writer.write(fh)
        return dest_path
    except Exception:
        dest_path.unlink(missing_ok=True)
        return None


def prefill_fillable_forms(
    router: ModelRouter,
    forms: FormsPackage,
    attachments_dir: Path,
    out_dir: Path,
    kb: KnowledgeBase,
) -> FormsPackage:
    """For each form with an identified fillable source PDF: propose an
    admin-field mapping and write a prefilled copy. Everything else is left
    for the human checklist."""
    if not attachments_dir.exists():
        return forms
    for form in forms.forms:
        source = _find_source(form.source_file, attachments_dir)
        if source is None:
            continue
        field_names = list_form_fields(source)
        if not field_names:
            form.human_actions.append(
                f"{source.name} has no fillable fields — complete manually"
            )
            continue
        mapping = router.structured(
            Tier.FRONTIER,
            system=MAPPING_SYSTEM,
            prompt=f"""Form: {form.form_name}
PDF field names:
{chr(10).join(f"- {n}" for n in field_names)}

Company profile:
{kb.profile_text()}""",
            output_type=FieldMapping,
            stage="forms.pdf_fill",
        )
        if not mapping.fields:
            continue
        dest = out_dir / f"PREFILLED_{source.name}"
        filled = fill_pdf(source, mapping.fields, dest)
        if filled:
            form.filled_file = str(filled)
            form.prefill.update(mapping.fields)
            form.human_actions.append(
                f"Verify every prefilled field in {filled.name}, then complete "
                "certifications and sign"
            )
    return forms


def _find_source(name: Optional[str], attachments_dir: Path) -> Optional[Path]:
    if not name:
        return None
    candidate = attachments_dir / Path(name).name
    if candidate.exists() and candidate.suffix.lower() == ".pdf":
        return candidate
    return None
