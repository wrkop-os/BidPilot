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

from . import qa_checks
from .agents import architect, classification, eligibility, forms, past_performance, qa, shredder, submission, writers
from .assembly import assemble_and_export, write_stage_artifacts
from .audit import AuditLog
from .docproc import process_attachments
from .intake import SamGovClient, parse_notice_id, run_intake
from .kb.store import KnowledgeBase
from .models import HumanApproval, QAReport, ResponseArtifact
from .pricing import boe as boe_mod
from .pricing import estimator as estimator_mod
from .pricing import rates as rates_mod
from .pricing import structure as structure_mod
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


def run(ctx: RunContext) -> ProposalState:
    for node in build_graph():
        state = ctx.state
        if state.halted_reason:
            break
        if state.is_done(node.stage):
            continue
        ctx.console.print(f"[bold]▶ {node.stage.value}[/bold]")
        ctx.audit.record("stage_start", actor="orchestrator", stage=node.stage.value)
        node.fn(ctx)
        state.mark_done(node.stage)
        write_stage_artifacts(state)
        ctx.checkpoints.save(state)
        ctx.audit.record("stage_complete", actor="orchestrator", stage=node.stage.value)
        if state.halted_reason:
            break
        if node.gate_after:
            if not _gate(ctx, node.gate_after):
                break
    ctx.checkpoints.save(ctx.state)
    return ctx.state


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
    ocr_docs = [d.name for d in tree.docs if d.ocr_used]
    if ocr_docs:
        ctx.console.print(f"  [yellow]OCR needed (image-only): {', '.join(ocr_docs)} — route to manual review[/yellow]")
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
            "  [yellow]Sources sought: response is a capability statement, not a proposal. "
            "The pipeline continues through eligibility, then stops (full capability-statement "
            "flow is a v2 item).[/yellow]"
        )


def _eligibility(ctx: RunContext) -> None:
    state = ctx.state
    state.eligibility = eligibility.check_eligibility(
        ctx.router, state.notice.metadata, state.classification, state.doc_tree, ctx.kb, ctx.sam
    )
    if state.classification.response_artifact == ResponseArtifact.CAPABILITY_STATEMENT:
        state.halted_reason = "capability_statement_flow_v2"


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
    pricing.boe_narrative = boe_mod.write_boe(ctx.router, pricing)
    return pricing


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
    # Artifacts are written after every stage; assembly's job is the volume
    # merge + consistency inputs before QA.
    write_stage_artifacts(ctx.state)


def _qa(ctx: RunContext) -> None:
    state = ctx.state
    report = QAReport()

    for iteration in range(MAX_QA_FIX_ITERATIONS + 1):
        findings = []
        findings += qa_checks.citation_check(state.section_drafts, ctx.kb)
        findings += qa_checks.coverage_check(state.matrix, state.section_drafts)
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
