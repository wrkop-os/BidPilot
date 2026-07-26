"""BidPilot CLI.

Usage:
    bidpilot run <sam.gov opportunity URL or notice ID> [--profile PATH] [--out DIR] [--yes]
    bidpilot init-profile [PATH]

Environment:
    ANTHROPIC_API_KEY   Claude API key (or an `ant auth login` profile)
    SAM_GOV_API_KEY     api.data.gov key for SAM.gov opportunity metadata
    BIDPILOT_MODEL      override the Claude model (default: claude-opus-5)
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from rich.console import Console

from .config import load_profile
from .llm import LLM, RefusalError
from .pipeline import run_pipeline

console = Console()

EXAMPLE_PROFILE = Path(__file__).resolve().parent.parent / "company_profile.example.yaml"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bidpilot", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="Analyze an opportunity and build the proposal package")
    run_p.add_argument("url", help="SAM.gov opportunity URL or 32-hex notice ID")
    run_p.add_argument("--profile", help="Path to company profile YAML", default=None)
    run_p.add_argument("--out", help="Output directory root", default="output")
    run_p.add_argument(
        "--yes", action="store_true",
        help="Non-interactive: answer yes at gates (still never submits anything)",
    )
    run_p.add_argument("--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"])

    init_p = sub.add_parser("init-profile", help="Create a company_profile.yaml from the example")
    init_p.add_argument("path", nargs="?", default="company_profile.yaml")

    args = parser.parse_args(argv)

    if args.command == "init-profile":
        return _init_profile(args.path)
    return _run(args)


def _init_profile(dest: str) -> int:
    dest_path = Path(dest)
    if dest_path.exists():
        console.print(f"[red]{dest} already exists — not overwriting.[/red]")
        return 1
    shutil.copy(EXAMPLE_PROFILE, dest_path)
    console.print(f"Created {dest}. Edit it with your company's real data before running BidPilot.")
    return 0


def _run(args) -> int:
    try:
        profile = load_profile(args.profile)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        return 1

    def confirm(question: str) -> bool:
        if args.yes:
            console.print(f"[dim]--yes: auto-continuing past gate: {question}[/dim]")
            return True
        if not sys.stdin.isatty():
            console.print(f"[yellow]Non-interactive session; stopping at gate: {question}[/yellow]")
            return False
        answer = console.input(f"{question} \\[y/N] ").strip().lower()
        return answer in ("y", "yes")

    try:
        result = run_pipeline(
            args.url,
            profile,
            Path(args.out),
            llm=LLM(effort=args.effort),
            confirm=confirm,
            console=console,
        )
    except RefusalError as exc:
        console.print(f"[red]Model declined a request: {exc}[/red]")
        return 2
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        return 1

    return 0 if result.stopped_reason is None else 3


if __name__ == "__main__":
    raise SystemExit(main())
