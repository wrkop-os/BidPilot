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

    amend_p = sub.add_parser("amend", help="Solicitation amended: invalidate downstream stages, then re-run")
    amend_p.add_argument("url")
    amend_p.add_argument("--kb", default=None)
    amend_p.add_argument("--out", default="runs")

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
    kb = _load_kb_or_fail(args.kb)
    ctx = make_context(args.url, kb, Path(args.out), console=console)
    invalidated = invalidate_for_amendment(ctx.state)
    ctx.state.halted_reason = None
    ctx.checkpoints.save(ctx.state)
    ctx.audit.record("amendment_invalidation", actor="operator",
                     detail=[s.value for s in invalidated])
    console.print(
        f"Invalidated stages: {', '.join(s.value for s in invalidated) or '(none were complete)'}. "
        "Re-run `bidpilot run` to re-process from document processing with the amended files."
    )
    return 0


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
