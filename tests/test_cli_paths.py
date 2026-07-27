"""CLI-path tests for the no-LLM commands: status, reprice, costs, doctor.

These drive bidpilot.cli.main() with real run directories on disk. reprice
constructs a ModelRouter (hence the dummy API key) but must make zero model
calls — that's the point of the deterministic estimator review loop.
"""

import json
from pathlib import Path

import pytest

from bidpilot import cli
from bidpilot.models import NoticeMetadata, NoticePackage
from bidpilot.pricing.models import (
    EstimationMethod,
    LaborEstimate,
    LaborLine,
    PricingModel,
)
from bidpilot.state import CheckpointStore, ProposalState, Stage

NOTICE = "d" * 32
EXAMPLE_KB = str(Path(__file__).resolve().parent.parent / "kb.example")


@pytest.fixture(autouse=True)
def _dummy_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-never-used")


def _seed_run(out_root: Path, with_pricing: bool = True) -> ProposalState:
    run_dir = out_root / NOTICE
    state = ProposalState(run_id="r-cli", input_url=NOTICE, run_dir=str(run_dir))
    state.notice = NoticePackage(
        metadata=NoticeMetadata(notice_id=NOTICE, title="CLI Test Opp", response_deadline="2026-09-01")
    )
    state.mark_done(Stage.INTAKE)
    if with_pricing:
        state.pricing = PricingModel(
            estimate=LaborEstimate(lines=[
                LaborLine(task_id="T1", labor_category="Software Engineer", hours=100,
                          method=EstimationMethod.BOTTOM_UP, rationale="100 units x 1 hr"),
            ]),
        )
        pricing_dir = run_dir / "pricing"
        pricing_dir.mkdir(parents=True, exist_ok=True)
        (pricing_dir / "pricing_model.json").write_text(
            state.pricing.model_dump_json(indent=2), encoding="utf-8"
        )
    CheckpointStore(run_dir).save(state)
    return state


def test_status_command(tmp_path, capsys):
    _seed_run(tmp_path)
    rc = cli.main(["status", NOTICE, "--out", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "CLI Test Opp" in out
    assert "intake" in out
    assert "2026-09-01" in out


def test_status_missing_run(tmp_path, capsys):
    rc = cli.main(["status", "a" * 32, "--out", str(tmp_path)])
    assert rc == 1
    assert "No run found" in capsys.readouterr().out


def test_reprice_recomputes_deterministically(tmp_path, capsys):
    _seed_run(tmp_path)
    # Human edits hours 100 -> 150 in pricing_model.json:
    pricing_file = tmp_path / NOTICE / "pricing" / "pricing_model.json"
    data = json.loads(pricing_file.read_text())
    data["estimate"]["lines"][0]["hours"] = 150
    pricing_file.write_text(json.dumps(data))

    rc = cli.main(["reprice", NOTICE, "--kb", EXAMPLE_KB, "--out", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Repriced deterministically" in out

    updated = PricingModel.model_validate_json(pricing_file.read_text())
    assert updated.priced_lines[0].hours == 150
    # Example-KB: SE $58 direct, wrap 1.32*1.28*1.12*1.08 -> extended = 150 * wrapped
    assert updated.total == pytest.approx(150 * updated.priced_lines[0].wrapped_rate)
    # Fresh sensitivity computed
    assert [p.label for p in updated.sensitivity][0] == "baseline"


def test_reprice_without_pricing_model(tmp_path, capsys):
    _seed_run(tmp_path, with_pricing=False)
    rc = cli.main(["reprice", NOTICE, "--kb", EXAMPLE_KB, "--out", str(tmp_path)])
    assert rc == 1
    assert "No pricing model" in capsys.readouterr().out


def test_costs_command(tmp_path, capsys):
    _seed_run(tmp_path)
    audit = tmp_path / NOTICE / "audit.jsonl"
    audit.write_text(json.dumps({
        "event": "llm_call", "stage": "shred.extract", "model": "claude-haiku-4-5",
        "tokens_in": 500_000, "tokens_out": 50_000,
    }), encoding="utf-8")
    rc = cli.main(["costs", NOTICE, "--out", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "claude-haiku-4-5" in out and "WITHIN" in out


def test_costs_missing_audit(tmp_path, capsys):
    rc = cli.main(["costs", "b" * 32, "--out", str(tmp_path)])
    assert rc == 1


def test_doctor_runs(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rc = cli.main(["doctor", "--kb", EXAMPLE_KB])
    out = capsys.readouterr().out
    assert "Knowledge base" in out
    assert rc in (0, 1)  # env-dependent (SAM key), but never crashes
