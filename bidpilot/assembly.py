"""Assembly & export (stages 7 and 10): render artifacts to the run
directory after every stage (resumable, reviewable), then export the final
versioned package ZIP + audit bundle (FR-15, FR-18, PRD §10)."""

from __future__ import annotations

import datetime as _dt
import json
import shutil
import zipfile
from pathlib import Path

from .agents.forms import forms_to_markdown
from .agents.shredder import matrix_to_csv
from .agents.submission import build_ics, sheet_to_markdown
from .audit import AuditLog
from .pricing.compliance import compliance_markdown


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def write_stage_artifacts(state) -> list[str]:
    """Persist every produced artifact as reviewable files. Called after each
    stage so a human can inspect/edit between checkpoints. Returns warnings
    (e.g. section files preserved because they hold unsynced human edits)."""
    out = Path(state.run_dir)
    warnings: list[str] = []

    if state.notice:
        _write(out / "notice_package.json", state.notice.model_dump_json(indent=2))
    if state.classification:
        _write(out / "classification.json", state.classification.model_dump_json(indent=2))
    if state.eligibility:
        _write(out / "eligibility_report.json", state.eligibility.model_dump_json(indent=2))
        _write(out / "ELIGIBILITY_REPORT.md", eligibility_markdown(state))
    if state.matrix:
        _write(out / "compliance_matrix.json", state.matrix.model_dump_json(indent=2))
        _write(out / "compliance_matrix.csv", matrix_to_csv(state.matrix))
    if state.win_strategy:
        _write(out / "win_strategy.json", state.win_strategy.model_dump_json(indent=2))
    if state.section_drafts:
        volumes: dict[str, list] = {}
        for draft in state.section_drafts:
            volumes.setdefault(draft.volume, []).append(draft)
        for volume, drafts in volumes.items():
            body = "\n\n---\n\n".join(d.markdown for d in drafts)
            _write(out / "volumes" / f"{_slug(volume)}.md",
                   f"> DRAFT — requires human review. Volume: {volume}\n"
                   "> Edit the per-section files in volumes/sections/ (this merged file is regenerated).\n\n"
                   f"{body}")
        # Per-section files: the canonical reviewer edit surface, with
        # clobber protection for unsynced human edits.
        from .drafts import write_section_files

        warnings.extend(write_section_files(state))
        _write(
            out / "volumes" / "claims_source_map.json",
            json.dumps(
                [
                    {"section": d.section_id, "claims": [c.model_dump() for c in d.claims]}
                    for d in state.section_drafts
                ],
                indent=2,
            ),
        )
    if state.past_performance:
        _write(out / "past_performance.json", state.past_performance.model_dump_json(indent=2))
    if state.pricing:
        _write(out / "pricing" / "pricing_model.json", state.pricing.model_dump_json(indent=2))
        _write(out / "pricing" / "priced_lines.csv", priced_lines_csv(state.pricing))
        if state.pricing.boe_narrative:
            _write(out / "pricing" / "basis_of_estimate.md", state.pricing.boe_narrative)
        _write(
            out / "pricing" / "REGULATORY_COMPLIANCE.md",
            compliance_markdown(
                state.pricing.compliance_findings,
                state.pricing.pricing_obligations,
                state.pricing.sca_erosion,
            ),
        )
        template = state.pricing.structure.government_template_file if state.pricing.structure else None
        if template:
            source = _find_attachment(state, template)
            if source and source.exists():
                dest = out / "pricing" / f"GOVERNMENT_TEMPLATE_{source.name}"
                if not dest.exists():
                    shutil.copy(source, dest)  # preserved fillable, never regenerated
    if state.forms:
        _write(out / "forms_package.json", state.forms.model_dump_json(indent=2))
        _write(out / "FORMS_CHECKLIST.md", forms_to_markdown(state.forms))
    if state.submission_sheet:
        _write(out / "submission_sheet.json", state.submission_sheet.model_dump_json(indent=2))
        _write(out / "SUBMISSION_INSTRUCTIONS.md", sheet_to_markdown(state.submission_sheet))
        title = (state.notice.metadata.title if state.notice else None) or "proposal"
        ics = build_ics(state.submission_sheet, title)
        if ics:
            _write(out / "deadlines.ics", ics)
    if state.qa_report:
        _write(out / "qa_report.json", state.qa_report.model_dump_json(indent=2))
        _write(out / "QA_REPORT.md", qa_markdown(state))
    if state.rendered_volumes:
        _write(
            out / "rendered" / "render_manifest.json",
            json.dumps([rv.model_dump() for rv in state.rendered_volumes], indent=2),
        )
    _write(out / "REVIEW_CHECKLIST.md", review_checklist(state))

    # Reviewer dashboard (A13 pragmatic v1) — regenerate on every stage.
    from .dashboard import write_dashboard

    write_dashboard(state)
    return warnings


def assemble_and_export(state, audit: AuditLog) -> Path:
    """Final export (post final-gate): versioned ZIP of the package + audit log."""
    out = Path(state.run_dir)
    write_stage_artifacts(state)
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    zip_path = out / f"package_{state.run_id}_{stamp}.zip"

    manifest = {
        "run_id": state.run_id,
        "notice_id": state.notice.metadata.notice_id if state.notice else None,
        "title": state.notice.metadata.title if state.notice else None,
        "generated_at": stamp,
        "approvals": [a.model_dump() for a in state.approvals],
        "disclaimer": (
            "DRAFT package generated by BidPilot. A human must review, complete "
            "[NEEDS INPUT] items, sign, and submit. BidPilot never signs or submits."
        ),
    }
    _write(out / "manifest.json", json.dumps(manifest, indent=2))

    # Exportable proof of what was enforced (docs/COMPETITIVE_BENCHMARK.md:
    # rivals let you check citations; this package proves they were required).
    from .trust import write_trust_manifest

    write_trust_manifest(state, out / "audit.jsonl")

    skip = {"attachments", "api_cache"}
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(out.rglob("*")):
            if not path.is_file() or path.suffix == ".zip":
                continue
            rel = path.relative_to(out)
            if rel.parts and rel.parts[0] in skip:
                continue
            zf.write(path, rel)
    audit.record("export", actor="orchestrator", detail={"zip": str(zip_path)})
    return zip_path


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def eligibility_markdown(state) -> str:
    report = state.eligibility
    lines = [
        "# Eligibility Report",
        "",
        f"**Recommendation:** {report.bid_recommendation.value} (confidence {report.confidence:.0%})",
        "",
        report.rationale,
    ]
    if report.pwin_advisory:
        lines += ["", f"_{report.pwin_advisory}_"]
    if report.hard_blockers:
        lines += ["", "## 🛑 Hard blockers"] + [f"- {b}" for b in report.hard_blockers]
    if report.soft_risks:
        lines += ["", "## ⚠️ Soft risks"] + [f"- {r}" for r in report.soft_risks]
    if report.missing_info:
        lines += ["", "## ❓ Missing information"] + [f"- {m}" for m in report.missing_info]
    if report.checks:
        lines += ["", "## Checks", "", "| Dimension | Requirement | Position | Pass |", "|---|---|---|---|"]
        lines += [
            f"| {c.dimension} | {c.requirement} | {c.company_position} | "
            f"{'✔' if c.passes else '✘' if c.passes is False else 'human'} |"
            for c in report.checks
        ]
    return "\n".join(lines)


def priced_lines_csv(pricing) -> str:
    import csv
    import io

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["task_id", "labor_category", "year", "hours", "direct_rate", "wrapped_rate", "extended", "wd_floor", "wd_compliant"]
    )
    for line in pricing.priced_lines:
        writer.writerow(
            [line.task_id, line.labor_category, line.year, line.hours, line.direct_rate,
             line.wrapped_rate, line.extended, line.wd_floor or "", line.wd_compliant if line.wd_compliant is not None else ""]
        )
    writer.writerow([])
    writer.writerow(["TOTAL", "", "", "", "", "", pricing.total or 0, "", ""])
    return buf.getvalue()


def qa_markdown(state) -> str:
    report = state.qa_report
    lines = ["# QA / Red Team Report", ""]
    hard = report.hard_failures()
    lines.append(f"**{len(report.findings)} findings — {len(hard)} open hard failures**")
    lines.append(f"Fix iterations used: {report.fix_iterations_used}")
    for severity in ("hard", "soft", "info"):
        group = [f for f in report.findings if f.severity.value == severity]
        if group:
            lines += ["", f"## {severity.upper()}"]
            lines += [
                f"- [{f.category}] {f.description}" + (f" ({f.location})" if f.location else "")
                for f in group
            ]
    if report.mock_evaluation:
        lines += ["", "## Mock Evaluation (skeptical evaluator vs. Section M)"]
        for factor in report.mock_evaluation.factor_scores:
            lines += ["", f"### {factor.factor}: {factor.adjectival_rating}"]
            lines += [f"- ✔ {s}" for s in factor.strengths]
            lines += [f"- ⚠ {w}" for w in factor.weaknesses]
            lines += [f"- ✘ DEFICIENCY: {d}" for d in factor.deficiencies]
        lines += ["", report.mock_evaluation.overall_assessment]
    return "\n".join(lines)


def review_checklist(state) -> str:
    lines = [
        "# Human Review Checklist",
        "",
        "> Everything here is DRAFT. Federal offers carry legal certifications",
        "> (18 U.S.C. §1001 / False Claims Act) — a human reviews, resolves every",
        "> item below, signs, and submits. **BidPilot never signs or submits.**",
        "",
    ]
    if state.eligibility:
        lines.append(f"- [ ] Confirm bid decision: **{state.eligibility.bid_recommendation.value}**")
        lines += [f"- [ ] Resolve: {m}" for m in state.eligibility.missing_info]
    if state.win_strategy:
        lines += [f"- [ ] Confirm assumption: {a}" for a in state.win_strategy.assumptions]
    edited = [d.section_id for d in state.section_drafts if d.human_edited]
    if edited:
        lines.append("")
        lines.append("## Human-edited sections (claim maps may be stale)")
        lines += [
            f"- [ ] Re-verify facts you added/changed in {sid} — your edits, your accuracy"
            for sid in edited
        ]
    needs_input = [
        c for d in state.section_drafts for c in d.claims if c.needs_input
    ]
    if needs_input:
        lines.append("")
        lines.append("## [NEEDS INPUT] items (facts the KB lacks)")
        lines += [f"- [ ] {c.input_note or c.text[:120]}" for c in needs_input]
    if getattr(state, "corpus_truncation_notice", None):
        lines.append("")
        lines.append("## \U0001f6d1 Corpus truncation")
        lines.append("")
        lines.append(f"- [ ] {state.corpus_truncation_notice}")
        lines.append("  Split the package into smaller runs, or review the "
                     "dropped tail by hand, before relying on completeness.")
    if state.matrix and state.matrix.model_flagged_gaps:
        lines.append("")
        lines.append("## Possible missed requirements (trained domain model)")
        lines.append("")
        lines.append("_The local requirement model found these in the corpus but "
                     "could not match them to a matrix entry. Confirm or dismiss "
                     "each — none were added automatically._")
        lines += [f"- [ ] {gap}" for gap in state.matrix.model_flagged_gaps]
    if state.pricing:
        lines.append("")
        lines.append("## Pricing")
        lines += [f"- [ ] {a}" for a in state.pricing.human_pricing_actions]
        lines += [f"- [ ] Obtain quote: {q}" for q in state.pricing.quote_needed]
        lines += [f"- [ ] 🛑 RESOLVE WD VIOLATION: {v.detail}" for v in state.pricing.wd_violations]
        lines += [
            f"- [ ] 🛑 RESOLVE {f.rule}: {f.detail}"
            for f in state.pricing.compliance_findings if f.severity == "hard"
        ]
    if state.forms:
        lines.append("")
        lines.append("## Forms & certifications (human-only)")
        for form in state.forms.forms:
            if form.filled_file:
                lines.append(
                    f"- [ ] Verify machine-prefilled admin fields in {Path(form.filled_file).name}"
                )
            if form.signature_required:
                lines.append(f"- [ ] Sign: {form.form_name}")
            lines += [f"- [ ] {form.form_name}: {a}" for a in form.human_actions]
    if state.matrix and state.matrix.constraints.naming_convention:
        lines.append("")
        lines.append(
            f"- [ ] Verify rendered file names against Section L's convention: "
            f"“{state.matrix.constraints.naming_convention}”"
        )
    if state.submission_sheet:
        sheet = state.submission_sheet
        lines += [
            "",
            "## Submission",
            f"- [ ] Verify channel/destination: {sheet.channel} → {sheet.destination}",
            f"- [ ] Verify deadline: {sheet.deadline}"
            + (f" ({sheet.deadline_timezone})" if sheet.deadline_timezone else " ⚠️ NO TIMEZONE STATED"),
            "- [ ] Confirm all amendments acknowledged",
            "- [ ] Submit per SUBMISSION_INSTRUCTIONS.md; keep proof of timely delivery",
        ]
    if state.qa_report and state.qa_report.hard_failures():
        lines.append("")
        lines.append("## 🛑 OPEN HARD QA FAILURES (export blocked until resolved)")
        lines += [f"- [ ] {f.description}" for f in state.qa_report.hard_failures()]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _slug(name: str) -> str:
    import re

    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return slug or "volume"


def _find_attachment(state, name: str):
    if not state.notice:
        return None
    for f in state.notice.files:
        if f.name == name:
            return Path(f.local_path)
    return None
