"""BidPilot CLI.

Commands:
    run       Full pipeline: URL -> reviewed package (resumes automatically)
    analyze   Phase-1 "Analyst MVP" mode: stop after eligibility + matrix +
              submission sheet (bid/no-bid in minutes; no drafting/pricing)
    amend     Invalidate downstream stages after a solicitation amendment
    interview Print the KB-gap onboarding interview
    init-kb   Create a knowledge-base directory from the example

Environment:
    ANTHROPIC_API_KEY          Claude API key (or an `ant auth login` profile)
    SAM_GOV_API_KEY            api.data.gov key for SAM.gov APIs
    BIDPILOT_FRONTIER_MODEL    default claude-opus-5
    BIDPILOT_FAST_MODEL        default claude-haiku-4-5
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from rich.console import Console

from .kb.store import load_kb
from .orchestrator import build_graph, invalidate_for_amendment, make_context, run
from .routing import RefusalError
from .state import Stage

console = Console()

EXAMPLE_KB = Path(__file__).resolve().parent.parent / "kb.example"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bidpilot", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    for name, desc in (("run", "Full pipeline"), ("analyze", "Analyst MVP (stop after eligibility/matrix/submission)")):
        p = sub.add_parser(name, help=desc)
        p.add_argument("url", help="SAM.gov opportunity URL or 32-hex notice ID")
        p.add_argument("--kb", default=None, help="Knowledge base directory (default: ./kb)")
        p.add_argument("--out", default="runs", help="Output root for run directories")
        p.add_argument("--yes", action="store_true", help="Auto-approve gates (still never submits)")
        p.add_argument("--actor", default="operator", help="Name recorded on gate approvals")
        p.add_argument("--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"])

    amend_p = sub.add_parser("amend", help="Solicitation amended: archive docs, invalidate downstream stages, then re-run")
    amend_p.add_argument("url")
    amend_p.add_argument("--kb", default=None)
    amend_p.add_argument("--out", default="runs")

    disc_p = sub.add_parser("discover", help="Poll SAM.gov for recent opportunities matching the KB profile, pre-screened")
    disc_p.add_argument("--kb", default=None)
    disc_p.add_argument("--days", type=int, default=7, help="Look back N days (default 7)")
    disc_p.add_argument("--all", action="store_true", help="Include blocked opportunities in output")

    reprice_p = sub.add_parser("reprice", help="Estimator review loop: recompute pricing after human edits to pricing_model.json")
    reprice_p.add_argument("url")
    reprice_p.add_argument("--kb", default=None)
    reprice_p.add_argument("--out", default="runs")

    costs_p = sub.add_parser("costs", help="Per-stage/per-model cost telemetry for a run (FR-22)")
    costs_p.add_argument("url")
    costs_p.add_argument("--out", default="runs")

    doctor_p = sub.add_parser("doctor", help="Environment & contract checks (keys, KB, renderer, SAM API)")
    doctor_p.add_argument("--kb", default=None)
    doctor_p.add_argument("--network", action="store_true", help="Also ping the SAM.gov API (NFR-3 contract check)")

    int_p = sub.add_parser("interview", help="KB-gap onboarding interview")
    int_p.add_argument("--kb", default=None)

    init_p = sub.add_parser("init-kb", help="Create a KB directory from the example")
    init_p.add_argument("path", nargs="?", default="kb")

    args = parser.parse_args(argv)

    if args.command == "init-kb":
        return _init_kb(args.path)
    if args.command == "interview":
        return _interview(args)
    if args.command == "amend":
        return _amend(args)
    if args.command == "discover":
        return _discover(args)
    if args.command == "reprice":
        return _reprice(args)
    if args.command == "costs":
        return _costs(args)
    if args.command == "doctor":
        return _doctor(args)
    return _run(args, analyst_only=(args.command == "analyze"))


def _init_kb(dest: str) -> int:
    dest_path = Path(dest)
    if dest_path.exists():
        console.print(f"[red]{dest} already exists — not overwriting.[/red]")
        return 1
    shutil.copytree(EXAMPLE_KB, dest_path)
    console.print(f"Created {dest}/. Edit the YAML files with your company's real data — "
                  "the KB is the only source of company facts in drafts.")
    return 0


def _load_kb_or_fail(path):
    try:
        return load_kb(path)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1)


def _confirm_factory(args):
    def confirm(question: str) -> bool:
        if getattr(args, "yes", False):
            console.print(f"[dim]--yes: auto-approving: {question}[/dim]")
            return True
        if not sys.stdin.isatty():
            console.print(f"[yellow]Non-interactive; stopping at: {question}[/yellow]")
            return False
        return console.input(f"{question} \\[y/N] ").strip().lower() in ("y", "yes")

    return confirm


def _run(args, analyst_only: bool) -> int:
    kb = _load_kb_or_fail(args.kb)
    try:
        ctx = make_context(
            args.url, kb, Path(args.out),
            effort=args.effort, confirm=_confirm_factory(args), console=console,
            actor=args.actor,
        )
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        return 1

    if analyst_only:
        # Phase-1 mode: run through eligibility + shredding + submission only.
        for stage in (Stage.STRATEGY, Stage.PRODUCE, Stage.ASSEMBLE, Stage.QA, Stage.EXPORT):
            if stage not in ctx.state.completed_stages:
                ctx.state.completed_stages.append(stage)
        # Submission sheet is part of the analyst deliverable:
        from .agents.submission import extract_submission

        try:
            state = run(ctx)
            if state.notice and state.doc_tree and not state.halted_reason:
                state.submission_sheet = extract_submission(
                    ctx.router, state.notice.metadata, state.doc_tree
                )
                from .assembly import write_stage_artifacts

                write_stage_artifacts(state)
                ctx.checkpoints.save(state)
        except RefusalError as exc:
            console.print(f"[red]{exc}[/red]")
            return 2
        console.print(f"\nAnalyst artifacts in [bold]{ctx.state.run_dir}[/bold]: "
                      "ELIGIBILITY_REPORT.md, compliance_matrix.csv, SUBMISSION_INSTRUCTIONS.md")
        return 0 if ctx.state.halted_reason in (None, "gate_declined:bid_no_bid") else 3

    try:
        state = run(ctx)
    except RefusalError as exc:
        console.print(f"[red]{exc}[/red]")
        return 2
    if state.halted_reason and state.export_path is None:
        console.print(f"[yellow]Run stopped: {state.halted_reason} (resume with the same command)[/yellow]")
        return 3
    return 0


def _amend(args) -> int:
    from .amendments import archive_doc_tree

    kb = _load_kb_or_fail(args.kb)
    ctx = make_context(args.url, kb, Path(args.out), console=console)
    if ctx.state.doc_tree is not None:
        # Archive the pre-amendment documents so the re-run can diff and
        # produce the "what changed / what to re-review" report (FR-4).
        archive_doc_tree(Path(ctx.state.run_dir), ctx.state.doc_tree)
        console.print("Archived current documents for amendment diffing.")
    invalidated = invalidate_for_amendment(ctx.state)
    ctx.state.halted_reason = None
    ctx.checkpoints.save(ctx.state)
    ctx.audit.record("amendment_invalidation", actor="operator",
                     detail=[s.value for s in invalidated])
    console.print(
        f"Invalidated stages: {', '.join(s.value for s in invalidated) or '(none were complete)'}. "
        "Re-run `bidpilot run` — it will re-process the amended files and write AMENDMENT_REPORT.md."
    )
    return 0


def _discover(args) -> int:
    from .discover import discover
    from .intake.samgov import SamGovClient

    kb = _load_kb_or_fail(args.kb)
    sam = SamGovClient()
    if not sam.api_key:
        console.print("[red]SAM_GOV_API_KEY is required for discovery.[/red]")
        return 1
    results = discover(sam, kb.profile, days_back=args.days)
    if not results:
        console.print("No opportunities found for the profile's NAICS codes in the window.")
        return 0
    from rich.table import Table

    table = Table(title=f"Opportunities — last {args.days} days, pre-screened")
    for col in ("Screen", "Title", "NAICS", "Set-aside", "Deadline", "URL"):
        table.add_column(col, overflow="fold")
    shown = 0
    for opp in results:
        if opp.screen == "blocked" and not args.all:
            continue
        style = {"candidate": "green", "review": "yellow", "blocked": "red"}[opp.screen]
        table.add_row(
            f"[{style}]{opp.screen}[/{style}]",
            opp.title[:70], opp.naics or "?", opp.set_aside or "—",
            opp.deadline or "?", opp.url,
        )
        shown += 1
    console.print(table)
    blocked = sum(1 for o in results if o.screen == "blocked")
    console.print(f"{shown} shown, {blocked} blocked (use --all to see why). "
                  "Next: bidpilot analyze <url> for a full eligibility report.")
    return 0


def _reprice(args) -> int:
    """§9.7 estimator review loop: human edits hours/lines in
    pricing/pricing_model.json (the `estimate` block); this recomputes every
    downstream number deterministically — no LLM calls."""
    import json

    from .assembly import write_stage_artifacts
    from .pricing import rates as rates_mod
    from .pricing.models import PricingModel

    kb = _load_kb_or_fail(args.kb)
    ctx = make_context(args.url, kb, Path(args.out), console=console)
    state = ctx.state
    pricing_file = Path(state.run_dir) / "pricing" / "pricing_model.json"
    if not pricing_file.exists():
        console.print("[red]No pricing model on disk — run the pipeline first.[/red]")
        return 1
    pricing = PricingModel.model_validate_json(pricing_file.read_text(encoding="utf-8"))
    if pricing.estimate is None:
        console.print("[red]pricing_model.json has no estimate block.[/red]")
        return 1

    indirects = kb.profile.indirect_rates or rates_mod.IndirectRateStructure(
        fringe=0.0, overhead=0.0, gna=0.0, fee=0.0
    )
    option_years = max((l.year for l in pricing.priced_lines), default=0)
    wd = None
    if any(l.wd_floor is not None for l in pricing.priced_lines):
        # Rebuild the WD table from the previously priced floors (base-year rates).
        wd = rates_mod.WageDetermination(entries=[
            rates_mod.WageDeterminationEntry(labor_category=l.labor_category, minimum_wage=l.wd_floor)
            for l in pricing.priced_lines
            if l.wd_floor is not None and l.year == 0
        ])
    priced, violations, unresolved = rates_mod.price_estimate(
        pricing.estimate, kb.direct_rates(), indirects, wd, option_years=option_years
    )
    pricing.priced_lines = priced
    pricing.total = rates_mod.total_of(priced)
    pricing.wd_violations = violations
    pricing.sensitivity = rates_mod.sensitivity(priced)
    state.pricing = pricing
    write_stage_artifacts(state)
    ctx.checkpoints.save(state)
    ctx.audit.record("reprice", actor="operator", detail={"total": pricing.total})
    console.print(f"Repriced deterministically: total ${pricing.total:,.2f} "
                  f"({len(violations)} WD violations, {len(unresolved)} unresolved categories). "
                  "BOE narrative NOT regenerated — re-run the pipeline QA stage if line rationale changed.")
    return 0 if not violations else 3


def _costs(args) -> int:
    from .intake.samgov import parse_notice_id
    from .telemetry import compute_costs, report_markdown

    notice_id = parse_notice_id(args.url)
    audit_path = Path(args.out) / notice_id / "audit.jsonl"
    if not audit_path.exists():
        console.print(f"[red]No audit log at {audit_path}.[/red]")
        return 1
    run_cost = compute_costs(audit_path)
    console.print(report_markdown(run_cost))
    return 0


def _doctor(args) -> int:
    import os

    from . import rendering

    ok = True

    def check(label: str, passed: bool, detail: str = "", warn_only: bool = False):
        nonlocal ok
        mark = "[green]✔[/green]" if passed else ("[yellow]⚠[/yellow]" if warn_only else "[red]✘[/red]")
        suffix = f" — {detail}" if (detail and not passed) else ""
        console.print(f" {mark} {label}{suffix}", markup=True, highlight=False)
        if not passed and not warn_only:
            ok = False

    check("ANTHROPIC_API_KEY / auth", bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")),
          "set the key or run `ant auth login`", warn_only=True)
    check("SAM_GOV_API_KEY", bool(os.environ.get("SAM_GOV_API_KEY")),
          "required for metadata, amendments, entity check, discovery")
    try:
        kb = load_kb(args.kb)
        stale = kb.stale_entries()
        check(f"Knowledge base ({len(kb.known_ids())} entries)", True)
        check("KB freshness", not stale, f"{len(stale)} stale/unverified entries", warn_only=True)
        check("Indirect rates on file", kb.profile.indirect_rates is not None,
              "pricing totals will be unburdened without them", warn_only=True)
    except (FileNotFoundError, Exception) as exc:  # noqa: BLE001 — report, don't crash
        check("Knowledge base", False, str(exc))
    if rendering.soffice_available():
        check("LibreOffice DOCX->PDF conversion (exact page counts)",
              rendering.soffice_conversion_works(),
              "soffice found but conversion failed — install libreoffice-writer", warn_only=True)
    else:
        check("LibreOffice (exact page counts)", False,
              "not installed — word-count estimates only", warn_only=True)
    try:
        import pdfplumber  # noqa: F401
        check("pdfplumber (PDF tables)", True)
    except ImportError:
        check("pdfplumber (PDF tables)", False, r"pip install 'bidpilot\[tables]'", warn_only=True)

    if args.network:
        from .intake.samgov import SamGovClient

        sam = SamGovClient()
        record = None
        try:
            data = sam._search({"limit": 1, "ptype": "o"})
            record = (data.get("opportunitiesData") or [None])[0]
        except Exception as exc:
            check("SAM.gov Opportunities API contract", False, str(exc))
        else:
            shape_ok = bool(record) and all(k in record for k in ("noticeId", "title", "type"))
            check("SAM.gov Opportunities API contract", shape_ok,
                  "response shape changed!" if record and not shape_ok else "")
    console.print("[green]doctor: all required checks passed[/green]" if ok
                  else "[red]doctor: required checks failed[/red]")
    return 0 if ok else 1


def _interview(args) -> int:
    kb = _load_kb_or_fail(args.kb)
    from .kb.interview import build_interview
    from .routing import ModelRouter

    plan = build_interview(ModelRouter(), kb)
    for q in plan.questions:
        console.print(f"[bold]{q.topic}[/bold] — {q.question}")
        console.print(f"  [dim]{q.why_it_matters} → fills {q.fills_kb_field}[/dim]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
