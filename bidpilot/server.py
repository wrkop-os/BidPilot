"""Web layer: paste a SAM.gov listing URL -> the system analyzes the listing,
decides what response package that listing needs (full proposal, capability
statement, ...), and builds it — surfacing every human gate as an approval
button instead of a terminal prompt (FR-13 unchanged: gates block until a
human answers; nothing is signed or submitted).

Run with `bidpilot serve` (uvicorn). The pipeline needs a real filesystem,
LibreOffice, and API keys, so this server runs wherever the CLI runs — it is
the production-UI half of PRD §11 v1; React/Postgres later swap in behind
the same JSON API.
"""

from __future__ import annotations

import io
import threading
from pathlib import Path
from typing import Callable, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel
from rich.console import Console

from . import orchestrator
from .intake.samgov import parse_notice_id
from .state import Stage

CtxBuilder = Callable[..., "orchestrator.RunContext"]


class RunHandle:
    def __init__(self, notice_id: str):
        self.notice_id = notice_id
        self.thread: Optional[threading.Thread] = None
        self.ctx = None
        self.pending_gate: Optional[str] = None
        self._gate_answer: Optional[bool] = None
        self._gate_event = threading.Event()
        self.error: Optional[str] = None
        self.log = io.StringIO()
        self.lock = threading.Lock()

    def confirm(self, question: str) -> bool:
        """Runs on the pipeline thread: expose the gate, block for the API."""
        with self.lock:
            self.pending_gate = question
            self._gate_event.clear()
        self._gate_event.wait()
        with self.lock:
            self.pending_gate = None
            return bool(self._gate_answer)

    def answer_gate(self, approve: bool) -> bool:
        with self.lock:
            if self.pending_gate is None:
                return False
            self._gate_answer = approve
        self._gate_event.set()
        return True

    @property
    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()


class StartRun(BaseModel):
    url: str
    analyze_only: bool = False


class GateAnswer(BaseModel):
    approve: bool


class OutcomeReq(BaseModel):
    outcome: str  # won | lost | no_bid


def default_ctx_builder(url: str, out_root: Path, confirm, console) -> "orchestrator.RunContext":
    from .kb.store import load_kb

    return orchestrator.make_context(
        url, load_kb(), out_root, confirm=confirm, console=console, actor="web-operator"
    )


def create_app(ctx_builder: CtxBuilder = default_ctx_builder,
               output_root: Path = Path("runs")) -> FastAPI:
    app = FastAPI(title="BidPilot")
    runs: dict[str, RunHandle] = {}
    runs_lock = threading.Lock()

    def _pipeline(handle: RunHandle, url: str, analyze_only: bool) -> None:
        try:
            stop = Stage.SHRED if analyze_only else None
            orchestrator.run(handle.ctx, stop_after=stop)
        except Exception as exc:  # surfaced in status, run dir stays resumable
            handle.error = f"{type(exc).__name__}: {exc}"

    @app.post("/api/runs")
    def start_run(req: StartRun):
        try:
            notice_id = parse_notice_id(req.url)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        with runs_lock:
            handle = runs.get(notice_id)
            if handle and handle.running:
                raise HTTPException(status_code=409, detail="Run already in progress for this notice.")
            handle = RunHandle(notice_id)
            runs[notice_id] = handle
        console = Console(file=handle.log, width=100, no_color=True)
        handle.ctx = ctx_builder(req.url, output_root, handle.confirm, console)
        handle.thread = threading.Thread(
            target=_pipeline, args=(handle, req.url, req.analyze_only), daemon=True
        )
        handle.thread.start()
        return {"notice_id": notice_id, "run_id": handle.ctx.state.run_id}

    @app.get("/api/runs")
    def list_runs():
        with runs_lock:
            return [_status(h) for h in runs.values()]

    @app.get("/api/runs/{notice_id}")
    def run_status(notice_id: str):
        handle = runs.get(notice_id)
        if not handle:
            raise HTTPException(status_code=404, detail="Unknown run.")
        return _status(handle)

    @app.post("/api/runs/{notice_id}/gate")
    def answer_gate(notice_id: str, req: GateAnswer):
        handle = runs.get(notice_id)
        if not handle:
            raise HTTPException(status_code=404, detail="Unknown run.")
        if not handle.answer_gate(req.approve):
            raise HTTPException(status_code=409, detail="No gate is pending.")
        return {"ok": True}

    @app.post("/api/runs/{notice_id}/outcome")
    def report_outcome(notice_id: str, req: OutcomeReq):
        from .ml.pwin import VALID_OUTCOMES, build_features, record_outcome

        handle = runs.get(notice_id)
        if not handle:
            raise HTTPException(status_code=404, detail="Unknown run.")
        state = handle.ctx.state
        if req.outcome not in VALID_OUTCOMES:
            raise HTTPException(status_code=422, detail=f"outcome must be one of {VALID_OUTCOMES}")
        if state.eligibility is None or state.notice is None:
            raise HTTPException(status_code=409, detail="Run has no eligibility report yet.")
        features = build_features(state.notice.metadata, state.eligibility, handle.ctx.kb)
        record_outcome(output_root, state.notice.metadata.notice_id, req.outcome, features)
        return {"ok": True, "outcome": req.outcome}

    @app.get("/api/runs/{notice_id}/files/{file_path:path}")
    def get_file(notice_id: str, file_path: str):
        handle = runs.get(notice_id)
        if not handle:
            raise HTTPException(status_code=404, detail="Unknown run.")
        run_dir = Path(handle.ctx.state.run_dir).resolve()
        target = (run_dir / file_path).resolve()
        if run_dir not in target.parents and target != run_dir:
            raise HTTPException(status_code=403, detail="Path escapes the run directory.")
        if not target.is_file():
            raise HTTPException(status_code=404, detail="No such artifact.")
        return FileResponse(target)

    def _status(handle: RunHandle) -> dict:
        state = handle.ctx.state
        run_dir = Path(state.run_dir)
        artifacts = []
        if run_dir.exists():
            for p in sorted(run_dir.rglob("*")):
                rel = p.relative_to(run_dir)
                if p.is_file() and rel.parts[0] not in ("attachments", "api_cache"):
                    artifacts.append(str(rel))
        classification = state.classification
        return {
            "notice_id": handle.notice_id,
            "run_id": state.run_id,
            "title": state.notice.metadata.title if state.notice else None,
            "running": handle.running,
            "stages": [
                {"stage": s.value, "done": state.is_done(s)} for s in Stage
            ],
            "response_plan": classification.response_artifact.value if classification else None,
            "bid_recommendation": (
                state.eligibility.bid_recommendation.value if state.eligibility else None
            ),
            "pwin_advisory": state.eligibility.pwin_advisory if state.eligibility else None,
            "pending_gate": handle.pending_gate,
            "halted_reason": state.halted_reason,
            "error": handle.error,
            "export_path": state.export_path,
            "artifacts": artifacts,
            "log_tail": handle.log.getvalue()[-4000:],
        }

    @app.get("/", response_class=HTMLResponse)
    def index():
        return _UI

    return app


_UI = """<!doctype html><html><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width, initial-scale=1'>
<title>BidPilot</title><style>
body{font-family:Georgia,serif;margin:0;background:#f6f4ee;color:#1e2229}
header{background:#1f3a5f;color:#fff;padding:18px 28px}
header h1{margin:0;font-size:22px}header p{margin:4px 0 0;opacity:.85;font-size:13px}
main{max-width:1000px;margin:0 auto;padding:20px 28px}
section{background:#fff;border:1px solid #ddd6c8;border-radius:6px;margin:16px 0;padding:16px 20px}
input[type=text]{width:62%;padding:8px;font-size:14px;border:1px solid #bbb;border-radius:4px}
button{background:#1f3a5f;color:#fff;border:0;border-radius:4px;padding:8px 16px;font-size:14px;cursor:pointer}
button.decline{background:#c0392b}
.stage{display:inline-block;padding:2px 9px;border-radius:10px;font-size:11px;margin:2px}
.done{background:#27ae60;color:#fff}.todo{background:#e2dccd}
.gate{background:#fdf3d9;border-left:4px solid #e6a817;padding:10px 14px;margin:10px 0}
.err{background:#fbe4e0;border-left:4px solid #c0392b;padding:10px 14px;margin:10px 0}
pre{background:#f2efe6;padding:10px;font-size:11px;overflow-x:auto;max-height:200px}
a{color:#1f3a5f}.small{font-size:12px;color:#666}
</style></head><body>
<header><h1>BidPilot</h1><p>Paste a SAM.gov listing — it is analyzed and the response package the listing calls for is built. Humans keep the gates; nothing is signed or submitted.</p></header>
<main>
<section>
<input type='text' id='url' placeholder='https://sam.gov/opp/<notice-id>/view'>
<label class='small'><input type='checkbox' id='analyzeOnly'> analyze only (stop after compliance shred)</label>
<button onclick='startRun()'>Analyze listing</button>
<div id='startErr' class='small' style='color:#c0392b'></div>
</section>
<div id='runs'></div>
</main>
<script>
async function startRun(){
  const url=document.getElementById('url').value.trim();
  const analyze_only=document.getElementById('analyzeOnly').checked;
  const r=await fetch('/api/runs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url,analyze_only})});
  document.getElementById('startErr').textContent=r.ok?'':(await r.json()).detail;
  refresh();
}
async function outcome(id,o){
  const r=await fetch(`/api/runs/${id}/outcome`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({outcome:o})});
  if(r.ok) alert('Outcome recorded — P(win) training data captured.');
}
async function gate(id,approve){
  await fetch(`/api/runs/${id}/gate`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({approve})});
  refresh();
}
async function refresh(){
  const runs=await (await fetch('/api/runs')).json();
  document.getElementById('runs').innerHTML=runs.map(r=>`
  <section>
    <b>${r.title||r.notice_id}</b> <span class='small'>run ${r.run_id} · ${r.running?'running':'idle'}</span><br>
    ${r.stages.map(s=>`<span class='stage ${s.done?'done':'todo'}'>${s.stage}</span>`).join('')}
    ${r.response_plan?`<div class='small'>listing analysis says respond with: <b>${r.response_plan}</b></div>`:''}
    ${r.bid_recommendation?`<div class='small'>bid recommendation: <b>${r.bid_recommendation}</b></div>`:''}
    ${r.pending_gate?`<div class='gate'><b>HUMAN GATE:</b> ${r.pending_gate}<br>
      <button onclick='gate("${r.notice_id}",true)'>Approve</button>
      <button class='decline' onclick='gate("${r.notice_id}",false)'>Decline</button></div>`:''}
    ${r.halted_reason?`<div class='err'>halted: ${r.halted_reason}</div>`:''}
    ${r.error?`<div class='err'>${r.error}</div>`:''}
    ${r.pwin_advisory?`<div class='small'>${r.pwin_advisory}</div>`:''}
    ${r.export_path?`<div>📦 exported package ready &middot; record outcome:
      <button onclick='outcome("${r.notice_id}","won")'>Won</button>
      <button onclick='outcome("${r.notice_id}","lost")' class='decline'>Lost</button>
      <button onclick='outcome("${r.notice_id}","no_bid")'>No-bid</button></div>`:''}
    <details><summary class='small'>artifacts (${r.artifacts.length})</summary>
      ${r.artifacts.map(a=>`<a href='/api/runs/${r.notice_id}/files/${a}' target='_blank'>${a}</a>`).join('<br>')}
    </details>
    <details><summary class='small'>log</summary><pre>${(r.log_tail||'').replace(/</g,'&lt;')}</pre></details>
  </section>`).join('');
}
setInterval(refresh,2000);refresh();
</script></body></html>"""
