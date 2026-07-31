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
from .orchestrator import invalidate_for_amendment, make_context, run
from .routing import RefusalError
from .state import Stage

console = Console()

EXAMPLE_KB = Path(__file__).resolve().parent.parent / "kb.example"


def main(argv: list[str] | None = None) -> int:
    from .config import load_dotenv

    load_dotenv()   # `.env` in the project root; a real env var always wins
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
    disc_p.add_argument("--grants", action="store_true",
                        help="Also sweep Grants.gov (public API, no key) using KB capability keywords")

    reprice_p = sub.add_parser("reprice", help="Estimator review loop: recompute pricing after human edits to pricing_model.json")
    reprice_p.add_argument("url")
    reprice_p.add_argument("--kb", default=None)
    reprice_p.add_argument("--out", default="runs")

    costs_p = sub.add_parser("costs", help="Per-stage/per-model cost telemetry for a run (FR-22)")
    costs_p.add_argument("url")
    costs_p.add_argument("--out", default="runs")

    status_p = sub.add_parser("status", help="Run state: stages, gates, blockers, deliverables (no API keys needed)")
    status_p.add_argument("url")
    status_p.add_argument("--out", default="runs")

    sync_p = sub.add_parser("sync-drafts", help="Import reviewer edits from volumes/sections/*.md into the run, then re-run assemble+QA")
    sync_p.add_argument("url")
    sync_p.add_argument("--out", default="runs")

    redo_p = sub.add_parser("redo", help="Invalidate a stage (and everything after it) for a targeted re-run")
    redo_p.add_argument("stage", choices=[s.value for s in Stage])
    redo_p.add_argument("url")
    redo_p.add_argument("--out", default="runs")

    doctor_p = sub.add_parser("doctor", help="Environment & contract checks (keys, KB, renderer, SAM API)")
    doctor_p.add_argument("--kb", default=None)
    doctor_p.add_argument("--network", action="store_true", help="Also ping the SAM.gov API (NFR-3 contract check)")

    kbg_p = sub.add_parser("kb-gaps", help="Mine runs for knowledge the KB could not supply, ranked")
    kbg_p.add_argument("--out", default="runs")
    kbg_p.add_argument("--write", metavar="DIR", default=None, help="Also write the report to DIR")

    kbh_p = sub.add_parser("kb-health", help="KB quality: staleness, duplicates, broken refs, thin records")
    kbh_p.add_argument("--kb", default=None)
    kbh_p.add_argument("--max-age-days", type=int, default=365)
    kbh_p.add_argument("--strict", action="store_true", help="Exit 1 if any issue is found (CI gate)")

    int_p = sub.add_parser("interview", help="KB-gap onboarding interview")
    int_p.add_argument("--kb", default=None)

    init_p = sub.add_parser("init-kb", help="Create a KB directory from the example")
    init_p.add_argument("path", nargs="?", default="kb")

    watch_p = sub.add_parser("watch-amendments", help="Sweep all runs for new amendment notices (cron-able; exit 1 if any found)")
    watch_p.add_argument("--out", default="runs")

    bp_p = sub.add_parser("benchmark-price", help="Position the run's priced total against FPDS award history (advisory)")
    bp_p.add_argument("url")
    bp_p.add_argument("--out", default="runs")
    bp_p.add_argument("--pages", type=int, default=3, help="FPDS feed pages to pull (10 awards each)")

    out_p = sub.add_parser("outcome", help="Record a bid outcome (won/lost/no_bid) as P(win) training data")
    out_p.add_argument("url")
    out_p.add_argument("outcome", choices=["won", "lost", "no_bid"])
    out_p.add_argument("--kb", default=None)
    out_p.add_argument("--out", default="runs")

    serve_p = sub.add_parser("serve", help="Web UI/API: paste a listing URL, approve gates in the browser")
    serve_p.add_argument("--host", default="127.0.0.1")
    serve_p.add_argument("--port", type=int, default=8400)
    serve_p.add_argument("--out", default="runs")

    mle_p = sub.add_parser("mle", help="MLE workflows: collect run captures, export fine-tuning data")
    mle_sub = mle_p.add_subparsers(dest="mle_command", required=True)
    mle_c = mle_sub.add_parser("collect", help="Scan run dirs for training captures + human-preference pairs")
    mle_c.add_argument("--out", default="runs", help="Root of run directories")
    mle_e = mle_sub.add_parser("export", help="Export chat-format JSONL (train/val) for fine-tuning")
    mle_e.add_argument("--out", default="runs")
    mle_e.add_argument("--dataset-dir", default="mle_datasets")
    mle_e.add_argument("--stage-prefix", default=None, help="e.g. 'shred' or 'produce.write'")
    mle_e.add_argument("--val-fraction", type=float, default=0.1)
    mle_g = mle_sub.add_parser("gate", help="Fail-closed promotion gate: score extracted matrix vs gold (G2 recall)")
    mle_g.add_argument("extracted", help="compliance_matrix.json produced by the candidate model")
    mle_g.add_argument("gold", help="gold_matrix.csv")
    mle_g.add_argument("--record", default="mle_datasets/promotion_record.json")

    args = parser.parse_args(argv)

    if args.command == "watch-amendments":
        return _watch_amendments(args)
    if args.command == "benchmark-price":
        return _benchmark_price(args)
    if args.command == "outcome":
        return _outcome(args)
    if args.command == "serve":
        return _serve(args)
    if args.command == "mle":
        return _mle(args)
    if args.command == "init-kb":
        return _init_kb(args.path)
    if args.command == "kb-gaps":
        return _kb_gaps(args)
    if args.command == "kb-health":
        return _kb_health(args)
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
    if args.command == "status":
        return _status(args)
    if args.command == "sync-drafts":
        return _sync_drafts(args)
    if args.command == "redo":
        return _redo(args)
    if args.command == "doctor":
        return _doctor(args)
    return _run(args, analyst_only=(args.command == "analyze"))


def _watch_amendments(args) -> int:
    from .intake.samgov import SamGovClient
    from .watch import report_lines, watch_all

    sam = SamGovClient()
    if not sam.api_key:
        console.print("[red]SAM_GOV_API_KEY is required to re-query amendment chains.[/red]")
        return 1
    results = watch_all(sam, Path(args.out))
    if not results:
        console.print("No runs found to watch.")
        return 0
    for line in report_lines(results):
        console.print(line)
    stale = sum(1 for r in results if r.stale)
    console.print(f"{len(results)} run(s) checked, {stale} with new amendments.")
    return 1 if stale else 0


def _benchmark_price(args) -> int:
    from .intake.fpds import FpdsClient, price_position, save_awards

    state, _ = _load_state_or_fail(args)
    if not (state.pricing and state.pricing.total):
        console.print("[red]Run has no priced total yet — run the pipeline through produce first.[/red]")
        return 1
    meta = state.notice.metadata
    if not meta.naics_code:
        console.print("[red]Notice has no NAICS code — nothing comparable to search.[/red]")
        return 1
    run_dir = Path(state.run_dir)
    client = FpdsClient(cache_dir=run_dir / "api_cache")
    awards = client.search_awards(meta.naics_code, pages=args.pages)
    position = price_position(state.pricing.total, awards)
    save_awards(awards, run_dir / "pricing" / "fpds_awards.json")
    (run_dir / "PRICE_POSITION.md").write_text(
        "# Price position (FPDS award history)\n\n"
        f"{position['advisory']}\n\n"
        f"Source: FPDS ATOM feed, NAICS {meta.naics_code}, {position['n']} awards "
        "with dollar values. Raw records: pricing/fpds_awards.json\n",
        encoding="utf-8",
    )
    console.print(position["advisory"])
    return 0


def _outcome(args) -> int:
    from .kb.store import load_kb
    from .ml.pwin import build_features, record_outcome

    state, _ = _load_state_or_fail(args)
    if state.eligibility is None or state.notice is None:
        console.print("[red]Run has no eligibility report yet — run `bidpilot analyze` first.[/red]")
        return 1
    features = build_features(state.notice.metadata, state.eligibility, load_kb(args.kb))
    path = record_outcome(Path(args.out), state.notice.metadata.notice_id, args.outcome, features)
    from .ml.pwin import load_outcomes
    labeled = sum(1 for r in load_outcomes(Path(args.out)) if r["outcome"] in ("won", "lost"))
    console.print(
        f"Recorded '{args.outcome}' for {state.notice.metadata.notice_id} -> {path} "
        f"({labeled} labeled outcomes; training unlocks at 30)"
    )
    return 0


def _kb_gaps(args) -> int:
    from .kb.ops import gaps_markdown, mine_gaps

    gaps = mine_gaps(Path(args.out))
    console.print(gaps_markdown(gaps))
    if args.write:
        dest = Path(args.write)
        dest.mkdir(parents=True, exist_ok=True)
        path = dest / "KB_GAPS.md"
        path.write_text(gaps_markdown(gaps), encoding="utf-8")
        console.print(f"\nwritten: {path}")
    return 0


def _kb_health(args) -> int:
    from .kb.ops import health, health_markdown

    kb = _load_kb_or_fail(args.kb)
    report = health(kb, max_age_days=args.max_age_days)
    console.print(health_markdown(report, kb))
    return 1 if (args.strict and report.issues) else 0


def _serve(args) -> int:
    try:
        import uvicorn
    except ImportError:
        print("The web UI needs the server extras: pip install -e '.[server]'")
        return 1
    from .server import create_app

    app = create_app(output_root=Path(args.out))
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


def _mle(args) -> int:
    from .mle import collect_runs, export_chat_jsonl

    if args.mle_command == "gate":
        from .mle.promotion import gate_custom_llm

        record = gate_custom_llm(Path(args.extracted), Path(args.gold), Path(args.record))
        console.print(
            f"recall {record['metrics']['recall']:.1%} vs gate >= 98% -> "
            f"{'[green]PROMOTED[/green]' if record['passed'] else '[red]NOT PROMOTED[/red]'}"
            f" (record: {args.record})"
        )
        return 0 if record["passed"] else 1

    examples, stats = collect_runs(Path(args.out))
    print(
        f"scanned {stats.runs_scanned} run(s): {stats.captures} captures, "
        f"{stats.preferences} human-preference pairs"
    )
    for stage, n in sorted(stats.by_stage.items()):
        print(f"  {stage}: {n}")
    if not examples:
        print("No captures found. Run the pipeline with BIDPILOT_CAPTURE_TRAINING_DATA=1 first.")
        return 0
    if args.mle_command == "export":
        counts = export_chat_jsonl(
            examples, Path(args.dataset_dir),
            stage_prefix=args.stage_prefix, val_fraction=args.val_fraction,
        )
        print(f"wrote {args.dataset_dir}/train.jsonl ({counts['train']}), "
              f"val.jsonl ({counts['val']}), {counts['preference_pairs']} preference pairs")
    return 0


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
        # Phase-1 mode: stop after the shredder — no drafting, no pricing.
        # stop_after leaves the checkpoint truthful, so a later full
        # `bidpilot run` continues from strategy instead of skipping stages.
        from .agents.submission import extract_submission

        try:
            state = run(ctx, stop_after=Stage.SHRED)
            if state.notice and state.doc_tree and not state.halted_reason:
                if state.submission_sheet is None:
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
    sources = None
    if args.grants:
        from .intake.sources import GrantsGovSource, SamGovSource

        keywords = [c.split(" (")[0][:60] for c in (kb.profile.capabilities or [])][:5]
        sources = [SamGovSource(sam), GrantsGovSource(keywords=keywords)]
    results = discover(sam, kb.profile, days_back=args.days, sources=sources)
    if not results:
        console.print("No opportunities found for the profile's NAICS codes in the window.")
        return 0
    from rich.table import Table

    table = Table(title=f"Opportunities — last {args.days} days, pre-screened")
    for col in ("Screen", "P(win)", "Source", "Title", "NAICS", "Set-aside", "Deadline", "URL"):
        table.add_column(col, overflow="fold")
    shown = 0
    for opp in results:
        if opp.screen == "blocked" and not args.all:
            continue
        style = {"candidate": "green", "review": "yellow", "blocked": "red"}[opp.screen]
        table.add_row(
            f"[{style}]{opp.screen}[/{style}]",
            f"~{opp.pwin:.0%}" if opp.pwin is not None else "?",
            opp.source,
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
    from .assembly import write_stage_artifacts
    from .pricing import compliance
    from .pricing import rates as rates_mod
    from .pricing import sca_erosion
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
    option_years = max((line.year for line in pricing.priced_lines), default=0)
    wd = None
    if any(line.wd_floor is not None for line in pricing.priced_lines):
        # Rebuild the WD table from the previously priced floors (base-year rates).
        wd = rates_mod.WageDetermination(entries=[
            rates_mod.WageDeterminationEntry(labor_category=line.labor_category, minimum_wage=line.wd_floor)
            for line in pricing.priced_lines
            if line.wd_floor is not None and line.year == 0
        ])
    # The pricing clauses found during the run are persisted as obligations
    # ("FAR 52.222-43: ..."); recover them so a reprice keeps honoring the same
    # regulatory constraints instead of quietly reverting to plain escalation.
    clauses = [line.split(":", 1)[0].strip() for line in pricing.pricing_obligations]
    sca_price_adjustment = any(
        c in clause for clause in clauses for c in ("52.222-43", "52.222-44")
    )
    priced, violations, unresolved = rates_mod.price_estimate(
        pricing.estimate,
        kb.direct_rates(),
        indirects,
        wd,
        option_years=option_years,
        sca_price_adjustment=sca_price_adjustment,
    )
    pricing.priced_lines = priced
    pricing.total = rates_mod.total_of(priced)
    pricing.wd_violations = violations
    pricing.sensitivity = rates_mod.sensitivity(priced)
    pricing.sca_erosion = (
        sca_erosion.project_erosion(priced, indirects, total_price=pricing.total)
        if sca_price_adjustment else None
    )
    pricing.compliance_findings = compliance.check(
        compliance.context_from_pricing(
            pricing, clauses, escalation_rate=indirects.escalation_per_year
        )
    )
    hard = [f for f in pricing.compliance_findings if f.severity == "hard"]
    state.pricing = pricing
    write_stage_artifacts(state)
    ctx.checkpoints.save(state)
    ctx.audit.record("reprice", actor="operator", detail={"total": pricing.total})
    console.print(f"Repriced deterministically: total ${pricing.total:,.2f} "
                  f"({len(violations)} WD violations, {len(hard)} hard compliance findings, "
                  f"{len(unresolved)} unresolved categories). "
                  "BOE narrative NOT regenerated — re-run the pipeline QA stage if line rationale changed.")
    for finding in hard:
        console.print(f"  [red]{finding.rule}:[/red] {finding.detail}")
    return 0 if not (violations or hard) else 3


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


def _load_state_or_fail(args):
    from .intake.samgov import parse_notice_id
    from .state import CheckpointStore

    notice_id = parse_notice_id(args.url)
    run_dir = Path(args.out) / notice_id
    store = CheckpointStore(run_dir)
    state = store.load()
    if state is None:
        console.print(f"[red]No run found at {run_dir}.[/red]")
        raise SystemExit(1)
    return state, store


def _sync_drafts(args) -> int:
    """Reviewer edit loop: import edits from volumes/sections/*.md into the
    checkpoint, then invalidate assemble+QA+export so `bidpilot run`
    re-renders and re-verifies the edited package. No model calls."""
    from .audit import AuditLog
    from .drafts import sync_drafts
    from .state import Stage

    state, store = _load_state_or_fail(args)
    if not state.section_drafts:
        console.print("[red]This run has no section drafts yet — nothing to sync.[/red]")
        return 1
    result = sync_drafts(state)
    for name in result.missing_files:
        console.print(f"[yellow]missing on disk: {name}[/yellow]")
    if not result.updated:
        console.print(f"No edits found ({result.unchanged} section(s) unchanged).")
        return 0
    invalidated = state.invalidate_from(Stage.ASSEMBLE)
    state.halted_reason = None
    store.save(state)
    AuditLog(Path(state.run_dir) / "audit.jsonl").record(
        "sync_drafts", actor="operator",
        detail={"updated": result.updated, "invalidated": [s.value for s in invalidated]},
    )
    console.print(
        f"Imported edits to: {', '.join(result.updated)} "
        f"({result.unchanged} unchanged). Invalidated: "
        f"{', '.join(s.value for s in invalidated) or '(nothing was complete)'}.\n"
        "Run [bold]bidpilot run[/bold] to re-render, re-QA, and re-export with your edits."
    )
    return 0


def _redo(args) -> int:
    """Targeted re-run: invalidate one stage and everything downstream."""
    from .audit import AuditLog
    from .state import Stage

    state, store = _load_state_or_fail(args)
    stage = Stage(args.stage)
    invalidated = state.invalidate_from(stage)
    state.halted_reason = None
    store.save(state)
    AuditLog(Path(state.run_dir) / "audit.jsonl").record(
        "redo", actor="operator",
        detail={"stage": stage.value, "invalidated": [s.value for s in invalidated]},
    )
    console.print(
        f"Invalidated: {', '.join(s.value for s in invalidated) or '(nothing was complete)'}. "
        "Run [bold]bidpilot run[/bold] to re-execute."
    )
    return 0


def _status(args) -> int:
    """Operator view of a run, straight from the checkpoint — safe to call
    anytime, needs no API keys and makes no model calls."""
    from .intake.samgov import parse_notice_id
    from .state import STAGE_ORDER, CheckpointStore

    notice_id = parse_notice_id(args.url)
    run_dir = Path(args.out) / notice_id
    state = CheckpointStore(run_dir).load()
    if state is None:
        console.print(f"[red]No run found at {run_dir}.[/red]")
        return 1

    title = (state.notice.metadata.title if state.notice else None) or notice_id
    console.print(f"[bold]{title}[/bold]  (run {state.run_id})")
    if state.notice and state.notice.metadata.response_deadline:
        console.print(f"deadline (SAM.gov): {state.notice.metadata.response_deadline}")

    for stage in STAGE_ORDER:
        mark = "[green]✔[/green]" if state.is_done(stage) else "[dim]·[/dim]"
        console.print(f" {mark} {stage.value}")

    if state.halted_reason:
        console.print(f"[yellow]halted: {state.halted_reason} — re-run `bidpilot run` to resume[/yellow]")
    for approval in state.approvals:
        verdict = "[green]approved[/green]" if approval.approved else "[red]declined[/red]"
        console.print(f" gate {approval.gate}: {verdict} by {approval.actor} at {approval.timestamp}")

    if state.eligibility:
        console.print(f" bid recommendation: [bold]{state.eligibility.bid_recommendation.value}[/bold]")
    if state.matrix:
        drafted = sum(1 for r in state.matrix.requirements if r.status.value != "unaddressed")
        console.print(f" compliance: {drafted}/{len(state.matrix.requirements)} requirements addressed")
    needs_input = sum(1 for d in state.section_drafts for c in d.claims if c.needs_input)
    if state.section_drafts:
        console.print(f" drafts: {len(state.section_drafts)} section(s), {needs_input} [NEEDS INPUT] items")
    if state.pricing and state.pricing.total is not None:
        console.print(f" pricing: ${state.pricing.total:,.2f} ROM"
                      + (f", [red]{len(state.pricing.wd_violations)} WD violations[/red]"
                         if state.pricing.wd_violations else ""))
    if state.qa_report:
        hard = state.qa_report.hard_failures()
        color = "red" if hard else "green"
        console.print(f" QA: {len(state.qa_report.findings)} findings, [{color}]{len(hard)} open hard[/{color}]")
    if state.export_path:
        console.print(f" [green]exported:[/green] {state.export_path}")
    dashboard = run_dir / "dashboard.html"
    if dashboard.exists():
        console.print(f" dashboard: {dashboard}")
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
        from .intake.samgov import SamGovClient, diagnose_api_failure

        sam = SamGovClient()
        record = None
        try:
            data = sam.search_raw({"limit": 1, "ptype": "o"})
            record = (data.get("opportunitiesData") or [None])[0]
        except Exception as exc:
            # "It failed" is useless here: a rejected key, a blocked egress
            # path, and a rate limit all surface as an exception but need
            # completely different fixes.
            check("SAM.gov Opportunities API contract", False, diagnose_api_failure(exc))
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
