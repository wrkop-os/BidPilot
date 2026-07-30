"""Real-browser e2e: a headless Chromium drives the actual inline HTML UI
served by `bidpilot.server` — paste a listing, approve the three human gates
as they appear, see the exported package, record a Won outcome — against a
live uvicorn server wired to the same fake-router context the API-level
suite uses (tests/test_server.py). No network, no keys.

Skips cleanly when the browser stack is absent: install it with
`pip install -e '.[dev,e2e]'`. Chromium resolution falls back to the
preinstalled binary at /opt/pw-browsers/chromium (override with
BIDPILOT_E2E_CHROMIUM) when playwright's default download is missing.
"""

import json
import os
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("uvicorn", reason="uvicorn not installed (pip install -e '.[dev]')")
pytest.importorskip(
    "playwright.sync_api",
    reason="playwright not installed (pip install -e '.[e2e]')",
)

import httpx
import uvicorn
from playwright.sync_api import expect, sync_playwright

from bidpilot.ml.pwin import OUTCOMES_NAME
from bidpilot.server import create_app

from test_orchestrator_e2e import NOTICE
from test_server import _fake_ctx_builder

CHROMIUM_FALLBACK = Path(os.environ.get("BIDPILOT_E2E_CHROMIUM", "/opt/pw-browsers/chromium"))
STEP_TIMEOUT = 60.0  # seconds; the UI only repaints on its 2s poll, so be generous


# -- live server + browser fixtures -------------------------------------------


@pytest.fixture(scope="module")
def output_root(tmp_path_factory):
    return tmp_path_factory.mktemp("web-e2e-runs")


@pytest.fixture(scope="module")
def server(output_root):
    """The real app (fake ctx builder) on a threaded uvicorn, ephemeral port."""
    app = create_app(ctx_builder=_fake_ctx_builder, output_root=output_root)
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error")
    srv = uvicorn.Server(config)
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not srv.started:
        if not thread.is_alive():
            raise RuntimeError("uvicorn thread died during startup")
        if time.monotonic() > deadline:
            raise RuntimeError("uvicorn did not report started within 30s")
        time.sleep(0.05)
    port = srv.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    thread.join(timeout=10)


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
        except Exception as exc:  # default browser download missing
            if not CHROMIUM_FALLBACK.exists():
                pytest.skip(f"chromium unavailable ({CHROMIUM_FALLBACK} absent): {exc}")
            try:
                browser = p.chromium.launch(
                    headless=True, executable_path=str(CHROMIUM_FALLBACK)
                )
            except Exception as exc2:
                pytest.skip(f"chromium at {CHROMIUM_FALLBACK} failed to launch: {exc2}")
        yield browser
        browser.close()


@pytest.fixture()
def page(browser):
    context = browser.new_context()
    pg = context.new_page()
    pg.set_default_timeout(int(STEP_TIMEOUT * 1000))
    yield pg
    context.close()


# -- helpers -------------------------------------------------------------------


def _wait_api(server, notice_id, predicate, timeout=STEP_TIMEOUT):
    """Poll the JSON status endpoint until predicate(status) holds."""
    deadline = time.monotonic() + timeout
    status = None
    while time.monotonic() < deadline:
        resp = httpx.get(f"{server}/api/runs/{notice_id}", timeout=10)
        resp.raise_for_status()
        status = resp.json()
        if predicate(status):
            return status
        time.sleep(0.1)
    raise AssertionError(f"Timed out waiting on API; last status: {status}")


# -- tests ---------------------------------------------------------------------


def test_browser_full_run_gates_export_outcome(server, page, output_root):
    page.goto(server + "/")
    expect(page.locator("#url")).to_be_visible()

    page.fill("#url", NOTICE)
    page.get_by_role("button", name="Analyze listing").click()

    # The three human gates fire in order; approve each one from the browser
    # as its panel appears. Gate questions are distinct, so wait for the
    # panel carrying the question the API reports as pending.
    approved = []
    for _ in range(3):
        status = _wait_api(server, NOTICE,
                           lambda s: s["pending_gate"] or not s["running"])
        question = status["pending_gate"]
        if not question:
            break
        gate_panel = page.locator(".gate", has_text=question[:25])
        expect(gate_panel).to_be_visible(timeout=STEP_TIMEOUT * 1000)
        expect(gate_panel).to_contain_text("HUMAN GATE")
        gate_panel.get_by_role("button", name="Approve").click()
        approved.append(question)
        _wait_api(server, NOTICE,
                  lambda s, q=question: s["pending_gate"] != q)
    assert len(approved) == 3, f"expected 3 gates, approved: {approved}"

    # Pipeline finishes clean and the UI shows the export line.
    status = _wait_api(server, NOTICE, lambda s: not s["running"])
    assert status["error"] is None
    assert status["halted_reason"] is None
    assert status["export_path"]
    expect(page.get_by_text("exported package ready")).to_be_visible(
        timeout=STEP_TIMEOUT * 1000
    )

    # Artifacts list (inside <details>, re-rendered on every UI poll — assert
    # DOM presence, not visibility) links the QA dashboard.
    dash_link = page.locator(f"a[href='/api/runs/{NOTICE}/files/dashboard.html']")
    expect(dash_link).to_be_attached(timeout=STEP_TIMEOUT * 1000)

    # Record the outcome from the browser -> P(win) training label on disk.
    # Success is a visible state change (not an alert): the panel reports the
    # recorded label, so a user never re-clicks blind and double-labels a bid.
    page.get_by_role("button", name="Won", exact=True).click()
    expect(page.get_by_text("outcome recorded:")).to_be_visible(
        timeout=STEP_TIMEOUT * 1000
    )

    outcomes = output_root / OUTCOMES_NAME
    assert outcomes.exists(), f"{OUTCOMES_NAME} was not written to the output root"
    row = json.loads(outcomes.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert row["notice_id"] == NOTICE
    assert row["outcome"] == "won"


def test_browser_bad_url_shows_error_without_starting_run(server, page):
    before = httpx.get(f"{server}/api/runs", timeout=10).json()

    page.goto(server + "/")
    page.fill("#url", "not-a-sam-listing")
    page.get_by_role("button", name="Analyze listing").click()

    expect(page.locator("#startErr")).to_contain_text("notice ID")

    after = httpx.get(f"{server}/api/runs", timeout=10).json()
    assert len(after) == len(before), "a run was started for a garbage URL"
