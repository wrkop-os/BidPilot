"""Reviewer dashboard (A13, pragmatic v1): a single self-contained HTML file
per run — matrix→draft coverage, claim provenance, [NEEDS INPUT] queue, QA
findings, mock evaluation, pricing summary, gates. The product's job is to
make the reviewer fast: sources, confidence, and status up front."""

from __future__ import annotations

import html
from pathlib import Path

_CSS = """
body{font-family:Georgia,serif;margin:0;background:#f6f4ee;color:#1e2229}
header{background:#1f3a5f;color:#fff;padding:18px 28px}
header h1{margin:0;font-size:22px} header p{margin:4px 0 0;opacity:.85;font-size:13px}
main{max-width:1100px;margin:0 auto;padding:20px 28px}
section{background:#fff;border:1px solid #ddd6c8;border-radius:6px;margin:16px 0;padding:16px 20px}
h2{font-size:16px;margin:0 0 10px;border-bottom:2px solid #1f3a5f;padding-bottom:6px}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{border:1px solid #e2dccd;padding:5px 8px;text-align:left;vertical-align:top}
th{background:#efe9db}
.badge{display:inline-block;padding:1px 8px;border-radius:10px;font-size:11px;font-weight:bold}
.hard{background:#c0392b;color:#fff}.soft{background:#e67e22;color:#fff}.info{background:#95a5a6;color:#fff}
.ok{background:#27ae60;color:#fff}.warn{background:#f39c12;color:#fff}.open{background:#7f8c8d;color:#fff}
.stat{display:inline-block;margin-right:28px}.stat b{font-size:22px;display:block}
.needs{background:#fdf3d9;border-left:4px solid #e6a817;padding:8px 12px;margin:6px 0;font-size:13px}
.small{font-size:12px;color:#666}
"""


def _e(text) -> str:
    return html.escape(str(text if text is not None else ""))


def render_dashboard(state) -> str:
    parts: list[str] = []
    title = (state.notice.metadata.title if state.notice else None) or state.run_id
    parts.append(f"<header><h1>BidPilot — {_e(title)}</h1>"
                 f"<p>run {_e(state.run_id)} · notice {_e(state.notice.metadata.notice_id if state.notice else '?')}"
                 f" · DRAFT — a human reviews, signs, and submits</p></header><main>")

    parts.append(_summary_section(state))
    if state.eligibility:
        parts.append(_eligibility_section(state.eligibility))
    if state.matrix:
        parts.append(_matrix_section(state))
    if state.section_drafts:
        parts.append(_claims_section(state))
    if state.pricing:
        parts.append(_pricing_section(state.pricing))
    if state.qa_report:
        parts.append(_qa_section(state.qa_report))
    parts.append(_gates_section(state))
    parts.append("</main>")
    body = "".join(parts)
    return f"<!doctype html><html><head><meta charset='utf-8'><title>BidPilot — {_e(title)}</title><style>{_CSS}</style></head><body>{body}</body></html>"


def _summary_section(state) -> str:
    matrix = state.matrix
    total = len(matrix.requirements) if matrix else 0
    drafted = sum(1 for r in (matrix.requirements if matrix else []) if r.status.value != "unaddressed")
    needs_input = sum(1 for d in state.section_drafts for c in d.claims if c.needs_input)
    hard = len(state.qa_report.hard_failures()) if state.qa_report else 0
    rec = state.eligibility.bid_recommendation.value if state.eligibility else "—"
    return (
        "<section><h2>Run summary</h2>"
        f"<span class='stat'><b>{_e(rec)}</b>bid rec</span>"
        f"<span class='stat'><b>{drafted}/{total}</b>requirements addressed</span>"
        f"<span class='stat'><b>{needs_input}</b>[NEEDS INPUT]</span>"
        f"<span class='stat'><b>{hard}</b>open hard failures</span>"
        f"<span class='stat'><b>{len(state.completed_stages)}/10</b>stages complete</span>"
        "</section>"
    )


def _eligibility_section(report) -> str:
    rows = "".join(
        f"<div class='needs'>🛑 {_e(b)}</div>" for b in report.hard_blockers
    ) + "".join(
        f"<div class='needs'>⚠️ {_e(r)}</div>" for r in report.soft_risks
    ) + "".join(
        f"<div class='needs'>❓ {_e(m)}</div>" for m in report.missing_info
    )
    return (
        f"<section><h2>Eligibility — {_e(report.bid_recommendation.value)} "
        f"({report.confidence:.0%})</h2><p>{_e(report.rationale)}</p>{rows}</section>"
    )


def _matrix_section(state) -> str:
    rows = []
    for r in state.matrix.requirements:
        status_class = {"verified": "ok", "drafted": "warn"}.get(r.status.value, "open")
        rows.append(
            f"<tr><td>{_e(r.req_id)}</td><td>{_e(r.category.value)}</td>"
            f"<td>{_e(r.verbatim_text[:220])}</td><td class='small'>{_e(r.source.render())}</td>"
            f"<td>{_e(', '.join(r.addressed_in) or '—')}</td>"
            f"<td><span class='badge {status_class}'>{_e(r.status.value)}</span></td></tr>"
        )
    return (
        "<section><h2>Compliance matrix → draft coverage</h2><table>"
        "<tr><th>ID</th><th>Category</th><th>Requirement</th><th>Source</th>"
        "<th>Addressed in</th><th>Status</th></tr>" + "".join(rows) + "</table></section>"
    )


def _claims_section(state) -> str:
    needs = [
        (d.section_id, c) for d in state.section_drafts for c in d.claims if c.needs_input
    ]
    cited = sum(1 for d in state.section_drafts for c in d.claims if c.kb_source_id)
    uncited = sum(
        1 for d in state.section_drafts for c in d.claims if not c.kb_source_id and not c.needs_input
    )
    blocks = "".join(
        f"<div class='needs'><b>{_e(sec)}</b>: {_e(c.input_note or c.text[:160])}</div>"
        for sec, c in needs
    )
    return (
        "<section><h2>Claim provenance</h2>"
        f"<span class='stat'><b>{cited}</b>KB-cited claims</span>"
        f"<span class='stat'><b>{len(needs)}</b>[NEEDS INPUT]</span>"
        f"<span class='stat'><b>{uncited}</b>uncited (build-breaking)</span>"
        f"{blocks}</section>"
    )


def _pricing_section(pricing) -> str:
    rows = "".join(
        f"<tr><td>{_e(l.task_id)}</td><td>{_e(l.labor_category)}</td><td>{l.year}</td>"
        f"<td>{l.hours:.0f}</td><td>${l.wrapped_rate:.2f}</td><td>${l.extended:,.0f}</td></tr>"
        for l in pricing.priced_lines
    )
    wd = "".join(f"<div class='needs'>🛑 {_e(v.detail)}</div>" for v in pricing.wd_violations)
    quotes = "".join(f"<div class='needs'>[QUOTE NEEDED] {_e(q)}</div>" for q in pricing.quote_needed)
    return (
        f"<section><h2>Pricing — total ${pricing.total or 0:,.0f} "
        "<span class='small'>(ROM; human validates)</span></h2>"
        "<table><tr><th>Task</th><th>Labor category</th><th>Yr</th><th>Hours</th>"
        f"<th>Wrapped</th><th>Extended</th></tr>{rows}</table>{wd}{quotes}</section>"
    )


def _qa_section(report) -> str:
    rows = "".join(
        f"<tr><td><span class='badge {_e(f.severity.value)}'>{_e(f.severity.value)}</span></td>"
        f"<td>{_e(f.category)}</td><td>{_e(f.description)}</td><td>{_e(f.location or '')}</td></tr>"
        for f in report.findings
    )
    mock = ""
    if report.mock_evaluation:
        factor_rows = "".join(
            f"<tr><td>{_e(fs.factor)}</td><td>{_e(fs.adjectival_rating)}</td>"
            f"<td>{_e('; '.join(fs.strengths))}</td><td>{_e('; '.join(fs.weaknesses))}</td>"
            f"<td>{_e('; '.join(fs.deficiencies))}</td></tr>"
            for fs in report.mock_evaluation.factor_scores
        )
        mock = (
            "<h2 style='margin-top:18px'>Mock evaluation (vs Section M)</h2>"
            "<table><tr><th>Factor</th><th>Rating</th><th>Strengths</th><th>Weaknesses</th>"
            f"<th>Deficiencies</th></tr>{factor_rows}</table>"
            f"<p>{_e(report.mock_evaluation.overall_assessment)}</p>"
        )
    return (
        f"<section><h2>QA / Red team ({len(report.findings)} findings)</h2>"
        f"<table><tr><th>Sev</th><th>Category</th><th>Finding</th><th>Where</th></tr>{rows}</table>"
        f"{mock}</section>"
    )


def _gates_section(state) -> str:
    rows = "".join(
        f"<tr><td>{_e(a.gate)}</td><td>{'✅ approved' if a.approved else '❌ declined'}</td>"
        f"<td>{_e(a.actor)}</td><td class='small'>{_e(a.timestamp)}</td>"
        f"<td class='small'>{_e(a.notes or '')}</td></tr>"
        for a in state.approvals
    )
    return (
        "<section><h2>Human gates (audit)</h2><table>"
        "<tr><th>Gate</th><th>Decision</th><th>Actor</th><th>When</th><th>Notes</th></tr>"
        + rows + "</table></section>"
    )


def write_dashboard(state) -> Path:
    path = Path(state.run_dir) / "dashboard.html"
    path.write_text(render_dashboard(state), encoding="utf-8")
    return path
