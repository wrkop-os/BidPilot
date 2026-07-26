"""Pipeline orchestrator: SAM.gov URL -> reviewed, assembled proposal package.

Stages:
  1. Retrieve   — fetch notice + attachments from SAM.gov
  2. Parse      — extract text, build corpus, structured analysis
  3. Eligibility— GATE: stop on hard blockers unless the human overrides
  4. Compliance — requirement-by-requirement matrix
  5. Submission — who/where/how/when instructions (never auto-submit)
  6. Draft      — technical/management volumes per the solicitation's structure
  7. Cost       — ROM estimate + basis of estimate
  8. Forms      — forms & reps checklist with safe pre-fill
  9. Assemble   — write everything to disk with a human review checklist

Human-in-the-loop is structural, not stylistic: BidPilot drafts, checks, and
assembles; a human reviews, approves, signs, and submits.
"""

from __future__ import annotations

import datetime as _dt
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from rich.console import Console

from . import documents
from .agents import (
    analyze_solicitation,
    build_compliance_matrix,
    check_eligibility,
    draft_volume,
    estimate_cost,
    extract_submission_instructions,
    prepare_forms,
)
from .agents.compliance import matrix_to_csv
from .agents.cost import estimate_to_markdown
from .agents.forms import forms_to_markdown
from .agents.submission import instructions_to_markdown
from .config import CompanyProfile
from .llm import LLM
from .models import (
    ComplianceMatrix,
    CostEstimate,
    EligibilityReport,
    EligibilityVerdict,
    FormsPackage,
    PackageManifest,
    RawOpportunity,
    SolicitationAnalysis,
    SubmissionInstructions,
    VolumeRequirement,
)
from .samgov import SamGovClient, parse_notice_id


@dataclass
class PipelineResult:
    opportunity: RawOpportunity
    analysis: Optional[SolicitationAnalysis] = None
    eligibility: Optional[EligibilityReport] = None
    matrix: Optional[ComplianceMatrix] = None
    submission: Optional[SubmissionInstructions] = None
    volumes: dict[str, str] = field(default_factory=dict)
    cost: Optional[CostEstimate] = None
    forms: Optional[FormsPackage] = None
    manifest: Optional[PackageManifest] = None
    stopped_reason: Optional[str] = None


# confirm(question) -> bool; injected so tests/CI can run non-interactively.
ConfirmFn = Callable[[str], bool]


def run_pipeline(
    url_or_id: str,
    profile: CompanyProfile,
    output_root: Path,
    llm: Optional[LLM] = None,
    sam: Optional[SamGovClient] = None,
    confirm: Optional[ConfirmFn] = None,
    console: Optional[Console] = None,
) -> PipelineResult:
    console = console or Console()
    llm = llm or LLM()
    sam = sam or SamGovClient()
    confirm = confirm or (lambda q: True)

    notice_id = parse_notice_id(url_or_id)
    out_dir = output_root / notice_id
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. Retrieve ------------------------------------------------------------
    console.print(f"[bold]1/9 Retrieving[/bold] notice {notice_id} from SAM.gov…")
    opportunity = sam.fetch_opportunity(notice_id)
    attachments_dir = out_dir / "attachments"
    opportunity.attachments = sam.download_attachments(notice_id, attachments_dir)
    result = PipelineResult(opportunity=opportunity)
    console.print(
        f"   title: {opportunity.title or '(no API metadata — set SAM_GOV_API_KEY for full data)'}\n"
        f"   attachments downloaded: {len(opportunity.attachments)}"
    )

    # 2. Parse ---------------------------------------------------------------
    console.print("[bold]2/9 Parsing[/bold] attachments and analyzing the solicitation…")
    attachment_texts = documents.extract_attachments(opportunity.attachments)
    corpus = documents.build_corpus(opportunity.description_text, attachment_texts)
    if len(corpus.strip()) < 200:
        console.print(
            "[yellow]   Warning: very little solicitation text was retrieved. "
            "Analysis will be shallow — consider adding attachments manually to "
            f"{attachments_dir} and re-running.[/yellow]"
        )
    _write(out_dir / "corpus.txt", corpus)
    result.analysis = analyze_solicitation(llm, opportunity, corpus)
    _write(out_dir / "solicitation_analysis.json", result.analysis.model_dump_json(indent=2))

    # 3. Eligibility gate ----------------------------------------------------
    console.print("[bold]3/9 Eligibility[/bold] check against company profile…")
    result.eligibility = check_eligibility(llm, result.analysis, profile)
    _write(out_dir / "eligibility.json", result.eligibility.model_dump_json(indent=2))
    console.print(f"   verdict: [bold]{result.eligibility.verdict.value}[/bold]")
    if result.eligibility.verdict == EligibilityVerdict.INELIGIBLE:
        for blocker in result.eligibility.blockers:
            console.print(f"   [red]blocker:[/red] {blocker}")
        if not confirm("Eligibility verdict is INELIGIBLE. Continue anyway?"):
            result.stopped_reason = "ineligible"
            console.print("[red]Stopped: company is not eligible for this opportunity.[/red]")
            return result
    elif result.eligibility.verdict == EligibilityVerdict.NEEDS_HUMAN_REVIEW:
        for item in result.eligibility.human_review_items:
            console.print(f"   [yellow]review:[/yellow] {item}")
        if not confirm("Eligibility needs human review. Continue drafting while you verify?"):
            result.stopped_reason = "eligibility_review"
            return result

    # 4. Compliance matrix ---------------------------------------------------
    console.print("[bold]4/9 Compliance matrix[/bold]…")
    result.matrix = build_compliance_matrix(llm, result.analysis, corpus)
    _write(out_dir / "compliance_matrix.json", result.matrix.model_dump_json(indent=2))
    _write(out_dir / "compliance_matrix.csv", matrix_to_csv(result.matrix))
    console.print(f"   {len(result.matrix.rows)} requirements extracted")

    # 5. Submission instructions --------------------------------------------
    console.print("[bold]5/9 Submission instructions[/bold]…")
    result.submission = extract_submission_instructions(llm, result.analysis, corpus)
    _write(out_dir / "submission_instructions.json", result.submission.model_dump_json(indent=2))
    _write(out_dir / "SUBMISSION_INSTRUCTIONS.md", instructions_to_markdown(result.submission))
    console.print(f"   method: {result.submission.method} -> {result.submission.destination}")

    # 6. Draft volumes -------------------------------------------------------
    volumes = result.analysis.volumes or [_default_volume()]
    console.print(f"[bold]6/9 Drafting[/bold] {len(volumes)} volume(s)…")
    volumes_dir = out_dir / "volumes"
    volumes_dir.mkdir(exist_ok=True)
    for vol in volumes:
        console.print(f"   drafting: {vol.name}")
        draft = draft_volume(llm, vol, result.analysis, result.matrix, profile)
        filename = _slug(vol.name) + ".md"
        result.volumes[vol.name] = draft
        _write(volumes_dir / filename, draft)

    # 7. Cost estimate -------------------------------------------------------
    console.print("[bold]7/9 Cost estimate[/bold] (ROM + basis of estimate)…")
    result.cost = estimate_cost(llm, result.analysis, profile)
    cost_dir = out_dir / "cost"
    cost_dir.mkdir(exist_ok=True)
    _write(cost_dir / "cost_estimate.json", result.cost.model_dump_json(indent=2))
    _write(cost_dir / "cost_estimate.md", estimate_to_markdown(result.cost))

    # 8. Forms ---------------------------------------------------------------
    console.print("[bold]8/9 Forms & representations[/bold]…")
    result.forms = prepare_forms(llm, result.analysis, profile)
    _write(out_dir / "forms_checklist.json", result.forms.model_dump_json(indent=2))
    _write(out_dir / "FORMS_CHECKLIST.md", forms_to_markdown(result.forms))

    # 9. Assemble ------------------------------------------------------------
    console.print("[bold]9/9 Assembling[/bold] package…")
    result.manifest = assemble_package(out_dir, result)
    console.print(
        f"\n[green]Done.[/green] Package written to [bold]{out_dir}[/bold]\n"
        "Start with REVIEW_CHECKLIST.md — a human must review, price, sign, and submit."
    )
    return result


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def assemble_package(out_dir: Path, result: PipelineResult) -> PackageManifest:
    files = sorted(
        str(p.relative_to(out_dir))
        for p in out_dir.rglob("*")
        if p.is_file() and p.name != "manifest.json"
    )
    manifest = PackageManifest(
        notice_id=result.opportunity.notice_id,
        title=result.opportunity.title,
        generated_at=_dt.datetime.now(_dt.timezone.utc).isoformat(),
        output_dir=str(out_dir),
        files=files,
    )
    _write(out_dir / "manifest.json", manifest.model_dump_json(indent=2))
    _write(out_dir / "REVIEW_CHECKLIST.md", build_review_checklist(result))
    return manifest


def build_review_checklist(result: PipelineResult) -> str:
    opp = result.opportunity
    lines = [
        "# Human Review Checklist",
        "",
        f"**Opportunity:** {opp.title or opp.notice_id}",
        f"**Solicitation #:** {opp.solicitation_number or 'unknown'}",
        f"**Response deadline (SAM.gov):** {opp.response_deadline or 'unknown'}",
        "",
        "> Everything in this package is a DRAFT produced by BidPilot. Federal",
        "> proposals carry legal certifications — a human must complete every item",
        "> below before anything is submitted. **BidPilot never submits.**",
        "",
        "## Before drafting further",
    ]
    if result.eligibility:
        lines.append(f"- [ ] Confirm eligibility verdict: **{result.eligibility.verdict.value}**")
        lines += [f"- [ ] {item}" for item in result.eligibility.human_review_items]
    lines += [
        "",
        "## Compliance",
        "- [ ] Walk every row of `compliance_matrix.csv`; mark each as addressed with its proposal location",
        "- [ ] Verify page limits, fonts, margins, and file formats against Section L",
        "- [ ] Confirm all amendments are acknowledged",
        "",
        "## Content",
        "- [ ] Resolve every `[[HUMAN: ...]]` placeholder in the volume drafts",
        "- [ ] Technical approach reviewed by the capture/technical lead",
        "",
        "## Pricing",
    ]
    if result.cost:
        lines += [f"- [ ] {a}" for a in result.cost.human_pricing_actions] or [
            "- [ ] Pricing lead validates rates, hours, fee, and total"
        ]
    lines += [
        "",
        "## Forms & certifications",
        "- [ ] Complete and sign all forms in `FORMS_CHECKLIST.md` (certifications are human-only)",
        "- [ ] Verify SAM.gov registration and reps & certs are current",
        "",
        "## Submission",
    ]
    if result.submission:
        lines += [
            f"- [ ] Verify delivery method/destination: {result.submission.method} -> {result.submission.destination}",
            f"- [ ] Verify deadline: {result.submission.deadline}",
            "- [ ] Submit per `SUBMISSION_INSTRUCTIONS.md` and keep proof of timely delivery",
        ]
    if result.analysis and result.analysis.ambiguities_and_risks:
        lines += ["", "## Ambiguities & risks flagged during analysis"]
        lines += [f"- [ ] {r}" for r in result.analysis.ambiguities_and_risks]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _default_volume() -> VolumeRequirement:
    return VolumeRequirement(
        name="Technical and Management Approach",
        required_content=["Technical approach", "Management approach", "Staffing", "Past performance"],
    )


def _slug(name: str) -> str:
    import re

    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return slug or "volume"


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def dump_json(path: Path, data: dict) -> None:
    _write(path, json.dumps(data, indent=2, default=str))
