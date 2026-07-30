"""Web layer e2e: paste-a-listing -> analyzed -> gates approved in the API ->
package exported, all through the real orchestrator with the fake router/SAM
from the e2e suite. No network, no keys."""

import time
from pathlib import Path

from fastapi.testclient import TestClient
from rich.console import Console as RichConsole

from bidpilot.audit import AuditLog
from bidpilot.kb.store import load_kb
from bidpilot.orchestrator import RunContext, new_run
from bidpilot.server import create_app

from test_orchestrator_e2e import EXAMPLE_KB, NOTICE, FakeRouter, FakeSam


def _fake_ctx_builder(url, out_root, confirm, console):
    state, checkpoints = new_run(url, out_root)
    audit = AuditLog(Path(state.run_dir) / "audit.jsonl")
    return RunContext(
        state=state,
        router=FakeRouter(),
        sam=FakeSam(),
        kb=load_kb(str(EXAMPLE_KB)),
        checkpoints=checkpoints,
        audit=audit,
        console=console or RichConsole(quiet=True),
        confirm=confirm,
        actor="web-test",
    )


def _wait(client, notice_id, predicate, timeout=30.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = client.get(f"/api/runs/{notice_id}").json()
        if predicate(status):
            return status
        time.sleep(0.05)
    raise AssertionError(f"Timed out waiting; last status: {status}")


def test_full_run_through_web_gates(tmp_path):
    app = create_app(ctx_builder=_fake_ctx_builder, output_root=tmp_path)
    client = TestClient(app)

    resp = client.post("/api/runs", json={"url": NOTICE})
    assert resp.status_code == 200
    notice_id = resp.json()["notice_id"]

    # Three human gates fire in order; approve each as it appears.
    for _ in range(3):
        status = _wait(client, notice_id,
                       lambda s: s["pending_gate"] or not s["running"])
        if not status["pending_gate"]:
            break
        assert client.post(f"/api/runs/{notice_id}/gate",
                           json={"approve": True}).status_code == 200

    status = _wait(client, notice_id, lambda s: not s["running"])
    assert status["error"] is None
    assert status["halted_reason"] is None
    assert status["export_path"]
    assert all(s["done"] for s in status["stages"])
    # The listing was analyzed and the plan derived from it, not assumed.
    assert status["response_plan"] == "full_proposal"
    assert status["bid_recommendation"] == "bid"
    assert "dashboard.html" in status["artifacts"]

    # Artifact serving works and stays inside the run dir.
    ok = client.get(f"/api/runs/{notice_id}/files/dashboard.html")
    assert ok.status_code == 200 and b"<html" in ok.content[:200]
    assert client.get(
        f"/api/runs/{notice_id}/files/../../../etc/passwd"
    ).status_code in (403, 404)


def test_gate_endpoint_rejects_when_none_pending(tmp_path):
    app = create_app(ctx_builder=_fake_ctx_builder, output_root=tmp_path)
    client = TestClient(app)
    client.post("/api/runs", json={"url": NOTICE})
    notice_id = NOTICE
    _wait(client, notice_id, lambda s: not s["running"] or s["pending_gate"])
    # Finish the run, then a gate answer with nothing pending is a 409.
    for _ in range(3):
        s = _wait(client, notice_id, lambda s: s["pending_gate"] or not s["running"])
        if s["pending_gate"]:
            client.post(f"/api/runs/{notice_id}/gate", json={"approve": True})
    _wait(client, notice_id, lambda s: not s["running"])
    assert client.post(f"/api/runs/{notice_id}/gate",
                       json={"approve": True}).status_code == 409


def test_bad_listing_url_is_rejected_not_run(tmp_path):
    app = create_app(ctx_builder=_fake_ctx_builder, output_root=tmp_path)
    client = TestClient(app)
    resp = client.post("/api/runs", json={"url": "not-a-sam-listing"})
    assert resp.status_code == 422
    assert "notice ID" in resp.json()["detail"]


def test_ui_served():
    app = create_app(ctx_builder=_fake_ctx_builder, output_root=Path("."))
    client = TestClient(app)
    page = client.get("/")
    assert page.status_code == 200
    assert "Paste a SAM.gov listing" in page.text


def test_failed_ctx_builder_does_not_brick_the_dashboard(tmp_path):
    """A ctx_builder failure (missing ./kb is the likeliest first-run error)
    must not leave a ctx-less handle that makes every later poll 500."""
    def exploding(url, out_root, confirm, console):
        raise FileNotFoundError("No knowledge base directory found.")

    app = create_app(ctx_builder=exploding, output_root=tmp_path)
    client = TestClient(app, raise_server_exceptions=False)
    started = client.post("/api/runs", json={"url": NOTICE})
    assert started.status_code == 500
    assert "knowledge base" in started.json()["detail"]
    # The dashboard still works for every run.
    assert client.get("/api/runs").status_code == 200
    assert client.get("/api/runs").json() == []


def test_stale_gate_answer_cannot_answer_the_next_gate(tmp_path):
    """A click aimed at gate N must never approve gate N+1 (FR-13)."""
    app = create_app(ctx_builder=_fake_ctx_builder, output_root=tmp_path)
    client = TestClient(app)
    client.post("/api/runs", json={"url": NOTICE})
    first = _wait(client, NOTICE, lambda s: s["pending_gate"])
    stale_token = first["gate_token"]
    assert stale_token
    assert client.post(
        f"/api/runs/{NOTICE}/gate", json={"approve": True, "token": stale_token}
    ).status_code == 200

    second = _wait(client, NOTICE,
                   lambda s: (s["pending_gate"] and s["gate_token"] != stale_token)
                   or not s["running"])
    if second["pending_gate"]:
        # The stale token is refused; the new gate is still waiting on a human.
        replay = client.post(
            f"/api/runs/{NOTICE}/gate", json={"approve": True, "token": stale_token}
        )
        assert replay.status_code == 409
        assert client.get(f"/api/runs/{NOTICE}").json()["pending_gate"]
        client.post(f"/api/runs/{NOTICE}/gate",
                    json={"approve": False, "token": second["gate_token"]})
    _wait(client, NOTICE, lambda s: not s["running"])
