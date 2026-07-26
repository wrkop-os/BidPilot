"""Graph orchestrator (PRD §6.1–6.2): a mostly-deterministic DAG with typed
stage contracts, two hard human gates (bid/no-bid, final package) plus the
assumptions checkpoint, checkpointing after every stage, halt paths
(CUI/ITAR, no-bid), and swarm-style parallelism inside the produce node.

LLMs decide and draft; code verifies and computes.
"""

from __future__ import annotations

import datetime as _dt
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from rich.console import Console

from . import amendments, qa_checks, rendering, telemetry
from .agents import (
    architect,
    capability,
    classification,
    eligibility,
    forms,
    past_performance,
    pdf_forms,
    qa,
    shredder,
    submission,
    writers,
)
from .assembly import assemble_and_export, write_stage_artifacts
from .audit import AuditLog
from .docproc import process_attachments
from .docproc import ocr as ocr_mod
from .intake import SamGovClient, parse_notice_id, run_intake
from .kb.store import KnowledgeBase
from .models import HumanApproval, QAReport, ResponseArtifact
from .pricing import boe as boe_mod
from .pricing import estimator as estimator_mod
from .pricing import rates as rates_mod
from .pricing import structure as structure_mod
from .pricing import workbook as workbook_mod
from .pricing.models import PricingModel
from .routing import ModelRouter
from .state import CheckpointStore, ProposalState, Stage

ConfirmFn = Callable[[str], bool]

MAX_QA_FIX_ITERATIONS = 2


@dataclass
class RunContext:
    state: ProposalState
    router: ModelRouter
    sam: SamGovClient
    kb: KnowledgeBase
    checkpoints: CheckpointStore
    audit: AuditLog
    console: Console
    confirm: ConfirmFn
    actor: str = "operator"


@dataclass
class Node:
    stage: Stage
    fn: Callable[[RunContext], None]
    gate_after: Optional[str] = None


def new_run(url_or_id: str, output_root: Path) -> tuple[ProposalState, CheckpointStore]:
    notice_id = parse_notice_id(url_or_id)
    run_id = f"{notice_id[:8]}-{uuid.uuid4().hex[:8]}"
    run_dir = output_root / notice_id
    checkpoints = CheckpointStore(run_dir)
    existing = checkpoints.load()
    if existing:
        return existing, checkpoints  # resumable (FR-21)
    state = ProposalState(run_id=run_id, input_url=url_or_id, run_dir=str(run_dir))
    checkpoints.save(state)
    return state, checkpoints


def run(ctx: RunContext, stop_after: Optional[Stage] = None) -> ProposalState:
    """Execute the graph from the last checkpoint.

    Halt semantics: every halt is re-evaluated, never bypassed, on the next
    invocation — declined gates re-ask (the gate re-check below), and
    stage-internal halts (CUI/ITAR, no-response notices, blocked export)
    re-fire because a halting stage is never marked done. A fresh `run` is
    the operator's signal to reconsider, so any stored halt is cleared here.
    """
    state = ctx.state
    if state.halted_reason:
        ctx.audit.record("halt_cleared_on_resume", actor="orchestrator",
                         detail=state.halted_reason)
        state.halted_reason = None

    for node in build_graph():
        if state.halted_reason:
            break
        if state.is_done(node.stage):
            # Resume path: a completed stage whose gate was never approved
            # (declined last run, or crash between stage and gate) must
            # re-ask — skipping the stage must never skip the gate.
            if node.gate_after and not _gate_approved(state, node.gate_after):
                if not _gate(ctx, node.gate_after):
                    break
            if stop_after and node.stage == stop_after:
                break
            continue
        ctx.console.print(f"[bold]▶ {node.stage.value}[/bold]")
        ctx.audit.record("stage_start", actor="orchestrator", stage=node.stage.value)
        node.fn(ctx)
        if state.halted_reason:
            # The stage itself halted (CUI/ITAR, no-response notice, blocked
            # export): do NOT mark it done — resume must re-run it so the
            # halt condition is re-evaluated.
            write_stage_artifacts(state)
            ctx.checkpoints.save(state)
            ctx.audit.record("stage_halted", actor="orchestrator", stage=node.stage.value,
                             detail=state.halted_reason)
            break
        state.mark_done(node.stage)
        write_stage_artifacts(state)
        ctx.checkpoints.save(state)
        ctx.audit.record("stage_complete", actor="orchestrator", stage=node.stage.value)
        if node.gate_after:
            if not _gate(ctx, node.gate_after):
                break
        if stop_after and node.stage == stop_after:
            break
    ctx.checkpoints.save(ctx.state)
    return ctx.state


def _gate_approved(state: ProposalState, gate: str) -> bool:
    approval = state.approval_for(gate)
    return approval is not None and approval.approved


def invalidate_for_amendment(state: ProposalState) -> list[Stage]:
    """FR-4: on amendment, invalidate downstream from document processing."""
    return state.invalidate_from(Stage.DOCPROC)


def build_graph() -> list[Node]:
    return [
        Node(Stage.INTAKE, _intake),
        Node(Stage.DOCPROC, _docproc),
        Node(Stage.CLASSIFY, _classify),
        Node(Stage.ELIGIBILITY, _eligibility, gate_after="bid_no_bid"),
        Node(Stage.SHRED, _shred),
        Node(Stage.STRATEGY, _strategy, gate_after="assumptions"),
        Node(Stage.PRODUCE, _produce),
        Node(Stage.ASSEMBLE, _assemble),
        Node(Stage.QA, _qa),
        Node(Stage.EXPORT, _export),
    ]


# ---------------------------------------------------------------------------
# Gates (FR-19)
# ---------------------------------------------------------------------------


def _gate(ctx: RunContext, gate: str) -> bool:
    state = ctx.state
    if gate == "bid_no_bid" and state.eligibility:
        report = state.eligibility
        ctx.console.print(f"  recommendation: [bold]{report.bid_recommendation.value}[/bold] "
                          f"(confidence {report.confidence:.0%})")
        for blocker in report.hard_blockers:
            ctx.console.print(f"  [red]HARD BLOCKER:[/red] {blocker}")
        for risk in report.soft_risks:
            ctx.console.print(f"  [yellow]risk:[/yellow] {risk}")
        for info in report.missing_info:
            ctx.console.print(f"  [dim]missing:[/dim] {info}")
        question = "GATE — proceed to bid (drafting stages)?"
    elif gate == "assumptions" and state.win_strategy:
        ctx.console.print("  assumptions the solution rests on:")
        for assumption in state.win_strategy.assumptions:
            ctx.console.print(f"   • {assumption}")
        question = "CHECKPOINT — assumptions acceptable? (edit win_strategy.json and resume to change)"
    elif gate == "final_package":
        hard = state.qa_report.hard_failures() if state.qa_report else []
        if hard:
            ctx.console.print(f"  [red]{len(hard)} open hard QA failure(s) — export is blocked (FR-16).[/red]")
            for finding in hard:
                ctx.console.print(f"   ✗ {finding.description}")
            _record_gate(ctx, gate, approved=False, notes="blocked: open hard QA failures")
            ctx.state.halted_reason = "qa_hard_failures"
            return False
        question = "FINAL GATE — approve package for export? (a human still signs and submits)"
    else:
        question = f"GATE {gate} — continue?"

    approved = ctx.confirm(question)
    _record_gate(ctx, gate, approved)
    if not approved:
        ctx.state.halted_reason = f"gate_declined:{gate}"
        ctx.console.print(f"[yellow]Stopped at gate '{gate}'. Re-run to resume from here.[/yellow]")
    return approved


def _record_gate(ctx: RunContext, gate: str, approved: bool, notes: Optional[str] = None) -> None:
    ctx.state.approvals.append(
        HumanApproval(
            gate=gate,
            approved=approved,
            actor=ctx.actor,
            timestamp=_dt.datetime.now(_dt.timezone.utc).isoformat(),
            notes=notes,
        )
    )
    ctx.audit.human_action(gate, approved, ctx.actor, notes)
    ctx.checkpoints.save(ctx.state)


# ---------------------------------------------------------------------------
# Stage implementations
# ---------------------------------------------------------------------------


def _intake(ctx: RunContext) -> None:
    dest = Path(ctx.state.run_dir) / "attachments"
    ctx.state.notice = run_intake(ctx.sam, ctx.state.input_url, dest)
    notice = ctx.state.notice
    ctx.console.print(f"  {notice.metadata.title or '(no metadata — set SAM_GOV_API_KEY)'}")
    ctx.console.print(
        f"  attachments: {len(notice.files)} | amendments in chain: {len(notice.amendment_history)}"
    )
    if notice.restricted_files_flagged:
        ctx.console.print(
            "  [yellow]Some attachments are login-restricted — download manually into "
            f"{dest} and re-run (they'll be picked up on resume).[/yellow]"
        )


def _docproc(ctx: RunContext) -> None:
    notice = ctx.state.notice
    ctx.state.doc_tree = process_attachments(notice.files, notice.metadata.description_text)
    tree = ctx.state.doc_tree

    # Parsing-ladder final rung: vision-OCR image-only PDFs page-by-page.
    paths_by_name = {f.name: f.local_path for f in notice.files}
    for i, doc in enumerate(tree.docs):
        if doc.kind == "pdf" and doc.ocr_used and doc.name in paths_by_name:
            ctx.console.print(f"  OCR (vision transcription): {doc.name}")
            replaced = ocr_mod.ocr_pdf(ctx.router, paths_by_name[doc.name])
            if replaced is not None:
                tree.docs[i] = replaced
    still_unreadable = [d.name for d in tree.docs if d.ocr_used and not d.full_text.strip()]
    if still_unreadable:
        ctx.console.print(
            f"  [yellow]Unreadable (no text, no extractable images): "
            f"{', '.join(still_unreadable)} — route to manual review[/yellow]"
        )

    # Amendment lifecycle: if `bidpilot amend` archived the previous doc tree,
    # diff it against the freshly parsed one and write the what-changed report.
    if amendments.load_archived_doc_tree(Path(ctx.state.run_dir)) is not None:
        ctx.console.print("  amendment diff: comparing against pre-amendment documents…")
        report = amendments.run_amendment_diff(ctx.router, Path(ctx.state.run_dir), tree)
        if report and report.changes:
            for change in report.changes:
                marker = "[red]material[/red]" if change.severity == "material" else "admin"
                ctx.console.print(f"   • ({marker}) {change.description}")
            ctx.console.print("  see AMENDMENT_REPORT.md for the re-review checklist")
    # CUI/ITAR halt path (NG2, §14.5): halt-and-notify, do not process further.
    flags = tree.cui_flags()
    if flags:
        ctx.console.print("[red]  CUI/ITAR markings detected — halting (NG2):[/red]")
        for flag in flags:
            ctx.console.print(f"   • {flag}")
        if not ctx.confirm("Markings may be boilerplate false positives. Continue anyway?"):
            ctx.state.halted_reason = "cui_itar_detected"
            return
        ctx.audit.record("cui_override", actor=ctx.actor, detail=flags)


def _classify(ctx: RunContext) -> None:
    state = ctx.state
    state.classification = classification.classify(ctx.router, state.notice.metadata, state.doc_tree)
    c = state.classification
    ctx.console.print(
        f"  {c.notice_type.value} | {c.far_regime.value} | respond with: {c.response_artifact.value}"
        + (" [escalated]" if c.escalated else "")
    )
    if c.ai_disclosure_clause_detected:
        ctx.console.print("  [yellow]AI-use disclosure clause detected — surface to the customer (§14.2).[/yellow]")
    if c.response_artifact == ResponseArtifact.NONE:
        state.halted_reason = "no_response_required"
        ctx.console.print("  Nothing to produce (presolicitation/award) — monitoring only.")
    elif c.response_artifact == ResponseArtifact.CAPABILITY_STATEMENT:
        ctx.console.print(
            "  [cyan]Sources sought: the response is a 2-5 page capability statement — "
            "the produce stage drafts that instead of proposal volumes (no pricing/forms).[/cyan]"
        )


def _eligibility(ctx: RunContext) -> None:
    state = ctx.state
    state.eligibility = eligibility.check_eligibility(
        ctx.router, state.notice.metadata, state.classification, state.doc_tree, ctx.kb, ctx.sam
    )


def _shred(ctx: RunContext) -> None:
    state = ctx.state
    state.matrix = shredder.shred(ctx.router, state.doc_tree)
    ctx.console.print(f"  {len(state.matrix.requirements)} requirements extracted")


def _strategy(ctx: RunContext) -> None:
    state = ctx.state
    state.win_strategy = architect.build_strategy(ctx.router, state.doc_tree, state.matrix, ctx.kb)


def _produce(ctx: RunContext) -> None:
    """Parallel production swarm inside a fixed graph node (§6.4)."""
    state = ctx.state

    # Sources-sought branch: the deliverable is a capability statement —
    # no proposal volumes, no pricing, no forms (PRD §2.2 table).
    if state.classification.response_artifact == ResponseArtifact.CAPABILITY_STATEMENT:
        ctx.console.print("  drafting capability statement…")
        state.section_drafts = [
            capability.write_capability_statement(
                ctx.router, state.notice.metadata, state.doc_tree, ctx.kb
            )
        ]
        ctx.console.print("  submission instructions…")
        state.submission_sheet = submission.extract_submission(
            ctx.router, state.notice.metadata, state.doc_tree
        )
        return

    ctx.console.print("  drafting sections (parallel writers)…")
    state.section_drafts = writers.write_all_sections(
        ctx.router, state.matrix, state.win_strategy, ctx.kb, state.doc_tree
    )

    ctx.console.print("  past performance…")
    state.past_performance = past_performance.select_past_performance(
        ctx.router, state.matrix, ctx.kb, state.doc_tree
    )

    ctx.console.print("  pricing…")
    state.pricing = _price(ctx)

    ctx.console.print("  forms & certifications…")
    state.forms = forms.prepare_forms(ctx.router, state.notice, state.doc_tree, ctx.kb)
    state.forms = pdf_forms.prefill_fillable_forms(
        ctx.router,
        state.forms,
        Path(ctx.state.run_dir) / "attachments",
        Path(ctx.state.run_dir) / "forms",
        ctx.kb,
    )

    ctx.console.print("  submission instructions…")
    state.submission_sheet = submission.extract_submission(
        ctx.router, state.notice.metadata, state.doc_tree
    )


def _price(ctx: RunContext) -> PricingModel:
    state = ctx.state
    structure = structure_mod.parse_structure(ctx.router, state.doc_tree)
    estimate = estimator_mod.estimate_labor(
        ctx.router, state.win_strategy, state.matrix, structure, ctx.kb
    )
    odcs = estimator_mod.estimate_odcs(ctx.router, state.win_strategy, ctx.kb)
    wd = structure_mod.extract_wage_determination(ctx.router, state.doc_tree)

    indirects = ctx.kb.profile.indirect_rates or rates_mod.IndirectRateStructure(
        fringe=0.0, overhead=0.0, gna=0.0, fee=0.0
    )
    option_years = _count_option_years(structure)
    priced, violations, unresolved = rates_mod.price_estimate(
        estimate, ctx.kb.direct_rates(), indirects, wd, option_years=option_years
    )
    pricing = PricingModel(
        structure=structure,
        estimate=estimate,
        odcs=odcs,
        priced_lines=priced,
        total=rates_mod.total_of(priced),
        wd_violations=violations,
        sensitivity=rates_mod.sensitivity(priced),
        quote_needed=[o.description for o in odcs if o.quote_needed],
        human_pricing_actions=(
            ["Validate hours, rates, fee, and escalation; competitive price-to-win review"]
            + [f"No direct rate on file for labor category '{c}'" for c in unresolved]
            + (["Company indirect rate structure missing from KB — totals are UNBURDENED"]
               if ctx.kb.profile.indirect_rates is None else [])
        ),
    )
    if ctx.kb.profile.indirect_rates is None:
        ctx.console.print("  [yellow]No indirect rates in KB — totals are unburdened direct cost.[/yellow]")
    for violation in pricing.wd_violations:
        ctx.console.print(f"  [red]WD VIOLATION:[/red] {violation.detail}")

    # Government pricing template: propose a fill, apply to a COPY (FR-12).
    if structure.government_template_file:
        template_path = _attachment_path(ctx.state, structure.government_template_file)
        if template_path and template_path.exists():
            ctx.console.print(f"  filling government template {template_path.name} (copy)…")
            pricing.template_fill = workbook_mod.propose_fill(ctx.router, template_path, pricing)
            filled, skipped = workbook_mod.apply_fill(
                template_path,
                pricing.template_fill,
                Path(ctx.state.run_dir) / "pricing" / f"FILLED_{template_path.name}",
            )
            if filled is None:
                reason = pricing.template_fill.unfillable_reason or "; ".join(skipped)
                pricing.human_pricing_actions.append(
                    f"Fill the government template {template_path.name} manually "
                    f"(machine fill declined: {reason}); system-computed numbers are in priced_lines.csv"
                )
            elif skipped:
                pricing.human_pricing_actions.append(
                    f"Template fill skipped some cells ({'; '.join(skipped)}) — verify FILLED_{template_path.name}"
                )
            else:
                pricing.human_pricing_actions.append(
                    f"Verify every machine-filled cell in FILLED_{template_path.name} against priced_lines.csv"
                )

    pricing.boe_narrative = boe_mod.write_boe(ctx.router, pricing)
    return pricing


def _attachment_path(state: ProposalState, name: str) -> Optional[Path]:
    for f in state.notice.files if state.notice else []:
        if f.name == name:
            return Path(f.local_path)
    return None


def _count_option_years(structure) -> int:
    periods = {c.period for c in (structure.clins if structure else []) if c.period}
    import re

    years = 0
    for period in periods:
        m = re.search(r"option\s*(?:year)?\s*(\d+)", (period or "").lower())
        if m:
            years = max(years, int(m.group(1)))
    return years


def _assemble(ctx: RunContext) -> None:
    """Render volumes to DOCX (+ exact-page PDF when LibreOffice exists),
    named per convention (FR-15). Markdown sources stay alongside."""
    state = ctx.state
    if state.section_drafts:
        constraints = state.matrix.constraints if state.matrix else None
        from .models import FormatConstraints

        state.rendered_volumes = rendering.render_all_volumes(
            state.section_drafts,
            constraints or FormatConstraints(),
            Path(state.run_dir) / "rendered",
            solicitation_number=state.notice.metadata.solicitation_number if state.notice else None,
            company=ctx.kb.profile.name,
        )
        for rv in state.rendered_volumes:
            pages = f"{rv.page_count} pages (exact)" if rv.page_count else f"~{rv.estimated_pages:.0f} pages (estimate)"
            ctx.console.print(f"  rendered {Path(rv.docx_path).name}: {pages}")
        if not rendering.soffice_available():
            ctx.console.print(
                "  [dim]LibreOffice (soffice) not found — page counts are estimates; "
                "install it for exact page verification.[/dim]"
            )
    write_stage_artifacts(state)


def _qa(ctx: RunContext) -> None:
    state = ctx.state
    report = QAReport()

    capability_path = (
        state.classification is not None
        and state.classification.response_artifact == ResponseArtifact.CAPABILITY_STATEMENT
    )
    for iteration in range(MAX_QA_FIX_ITERATIONS + 1):
        findings = []
        findings += qa_checks.citation_check(state.section_drafts, ctx.kb)
        if state.matrix:
            coverage = qa_checks.coverage_check(state.matrix, state.section_drafts)
            if capability_path:
                # A capability statement doesn't carry proposal volumes; the
                # L-outline coverage contract doesn't apply — informational only.
                for finding in coverage:
                    if finding.severity == qa_checks.QASeverity.HARD:
                        finding.severity = qa_checks.QASeverity.SOFT
            findings += coverage
            if state.rendered_volumes:
                # Renderer-owned page counts (exact when soffice converted).
                findings += rendering.verify_rendered(state.rendered_volumes, state.matrix.constraints)
            else:
                findings += qa_checks.format_check(state.matrix, state.section_drafts)
        if state.pricing:
            findings += qa_checks.pricing_check(state.pricing)
            if state.pricing.estimate:
                drafts_text = "\n".join(d.markdown for d in state.section_drafts)
                for issue in boe_mod.staffing_consistency(state.pricing.estimate, drafts_text):
                    findings.append(
                        qa_checks.QAFinding(
                            severity=qa_checks.QASeverity.SOFT,
                            category="consistency", description=issue,
                        )
                    )
        report.findings = findings
        report.fix_iterations_used = iteration

        fixable = _fixable_sections(state, findings)
        if not fixable or iteration >= MAX_QA_FIX_ITERATIONS:
            break
        ctx.console.print(
            f"  fix loop {iteration + 1}/{MAX_QA_FIX_ITERATIONS}: re-drafting "
            f"{len(fixable)} section(s) with QA feedback"
        )
        _redraft(ctx, fixable, findings)
        if state.rendered_volumes:  # keep rendered files in sync with redrafts
            from .models import FormatConstraints

            state.rendered_volumes = rendering.render_all_volumes(
                state.section_drafts,
                state.matrix.constraints if state.matrix else FormatConstraints(),
                Path(state.run_dir) / "rendered",
                solicitation_number=state.notice.metadata.solicitation_number if state.notice else None,
                company=ctx.kb.profile.name,
            )

    ctx.console.print("  LLM audits: consistency, citation sampling, mock evaluation…")
    report.findings += qa.consistency_audit(ctx.router, state.section_drafts, state.pricing)
    report.findings += qa.citation_sample_audit(ctx.router, state.section_drafts, ctx.kb)
    report.mock_evaluation = qa.mock_evaluate(ctx.router, state.matrix, state.section_drafts)

    state.qa_report = report
    hard = report.hard_failures()
    ctx.console.print(
        f"  findings: {len(report.findings)} total, [bold]{len(hard)} hard[/bold]"
        + (" — export will be blocked" if hard else "")
    )


def _fixable_sections(state: ProposalState, findings) -> list[str]:
    section_ids = {d.section_id for d in state.section_drafts}
    return sorted(
        {
            f.location
            for f in findings
            if f.severity.value == "hard" and f.location in section_ids and not f.resolved
        }
    )


def _redraft(ctx: RunContext, section_ids: list[str], findings) -> None:
    state = ctx.state
    outline_sections = {s.section_id: s for s in (state.matrix.outline.sections if state.matrix.outline else [])}
    for i, draft in enumerate(state.section_drafts):
        if draft.section_id not in section_ids:
            continue
        section = outline_sections.get(draft.section_id)
        if section is None:
            continue
        feedback = "\n".join(
            f"- {f.description}" for f in findings
            if f.location == draft.section_id and f.severity.value == "hard"
        )
        section = section.model_copy()
        section.guidance = (section.guidance or "") + (
            f"\n\nQA FOUND THESE HARD FAILURES IN THE PREVIOUS DRAFT — fix every one:\n{feedback}"
        )
        state.section_drafts[i] = writers.write_section(
            ctx.router, section, state.matrix, state.win_strategy, ctx.kb, state.doc_tree
        )


def _export(ctx: RunContext) -> None:
    if not _gate(ctx, "final_package"):
        return
    # Cost telemetry (FR-22 / NFR-2) rolls up before the ZIP so it ships inside it.
    run_cost = telemetry.compute_costs(ctx.audit.path)
    (Path(ctx.state.run_dir) / "COST_TELEMETRY.md").write_text(
        telemetry.report_markdown(run_cost), encoding="utf-8"
    )
    if not run_cost.within_budget():
        ctx.console.print(
            f"  [yellow]model spend ${run_cost.total.cost_usd:.2f} exceeds the "
            f"NFR-2 ${telemetry.NFR2_BUDGET_USD:.0f} budget — review COST_TELEMETRY.md[/yellow]"
        )
    ctx.state.export_path = str(assemble_and_export(ctx.state, ctx.audit))
    ctx.console.print(f"[green]  package exported: {ctx.state.export_path}[/green]")


# ---------------------------------------------------------------------------
# Entrypoint helper
# ---------------------------------------------------------------------------


def make_context(
    url_or_id: str,
    kb: KnowledgeBase,
    output_root: Path,
    *,
    effort: str = "high",
    confirm: Optional[ConfirmFn] = None,
    console: Optional[Console] = None,
    actor: str = "operator",
) -> RunContext:
    state, checkpoints = new_run(url_or_id, output_root)
    run_dir = Path(state.run_dir)
    audit = AuditLog(run_dir / "audit.jsonl")
    return RunContext(
        state=state,
        router=ModelRouter(audit=audit, effort=effort),
        sam=SamGovClient(cache_dir=run_dir / "api_cache"),
        kb=kb,
        checkpoints=checkpoints,
        audit=audit,
        console=console or Console(),
        confirm=confirm or (lambda q: sys.stdin.isatty()),
        actor=actor,
    )
