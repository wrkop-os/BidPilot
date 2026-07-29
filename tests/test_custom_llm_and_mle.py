"""Custom-LLM backend (OpenAI-compatible serving) + MLE dataset workflows."""

import json
from pathlib import Path

import httpx
from pydantic import BaseModel

from bidpilot.audit import AuditLog
from bidpilot.mle import collect_runs, export_chat_jsonl
from bidpilot.routing import ModelRouter, Tier


class Verdict(BaseModel):
    label: str
    score: float


def _mock_backend(responses):
    """responses: list of body strings returned by successive calls."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        body = responses[min(len(calls) - 1, len(responses) - 1)]
        return httpx.Response(200, json={
            "choices": [{"message": {"content": body}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        })

    return httpx.MockTransport(handler), calls


def _router(monkeypatch, tmp_path, tiers="all", capture=False, responses=("{}",)):
    monkeypatch.setenv("BIDPILOT_CUSTOM_LLM_URL", "http://llm.local/v1")
    monkeypatch.setenv("BIDPILOT_CUSTOM_LLM_MODEL", "bidpilot-ft-1")
    monkeypatch.setenv("BIDPILOT_CUSTOM_LLM_TIERS", tiers)
    if capture:
        monkeypatch.setenv("BIDPILOT_CAPTURE_TRAINING_DATA", "1")
    audit = AuditLog(tmp_path / "audit.jsonl")
    router = ModelRouter(audit=audit)
    transport, calls = _mock_backend(list(responses))
    router.custom._http = httpx.Client(transport=transport)
    return router, calls, audit


def test_structured_via_custom_backend(monkeypatch, tmp_path):
    router, calls, audit = _router(
        monkeypatch, tmp_path, responses=['{"label": "bid", "score": 0.9}']
    )
    result = router.structured(
        Tier.FAST, system="sys", prompt="classify", output_type=Verdict, stage="classify.t"
    )
    assert result.label == "bid" and result.score == 0.9
    assert calls[0]["model"] == "bidpilot-ft-1"
    assert "JSON Schema" in calls[0]["messages"][0]["content"]
    events = [e for e in audit.entries() if e["event"] == "llm_call"]
    assert events and events[0]["model"] == "custom:bidpilot-ft-1"


def test_structured_retries_invalid_json_once(monkeypatch, tmp_path):
    router, calls, _ = _router(
        monkeypatch, tmp_path,
        responses=["not json at all", '```json\n{"label": "ok", "score": 1.0}\n```'],
    )
    result = router.structured(
        Tier.FAST, system="s", prompt="p", output_type=Verdict, stage="t"
    )
    assert result.label == "ok"
    assert len(calls) == 2
    assert "failed validation" in calls[1]["messages"][1]["content"]


def test_draft_via_custom_and_tier_scoping(monkeypatch, tmp_path):
    router, calls, _ = _router(monkeypatch, tmp_path, tiers="all",
                               responses=["Narrative text."])
    assert router.draft(system="s", prompt="write", stage="produce.boe") == "Narrative text."
    assert router.model_for(Tier.FRONTIER) == "custom:bidpilot-ft-1"

    fast_only, _, _ = _router(monkeypatch, tmp_path / "b", tiers="fast")
    assert fast_only._uses_custom(Tier.FAST)
    assert not fast_only._uses_custom(Tier.FRONTIER)


def test_capture_writes_training_data(monkeypatch, tmp_path):
    router, _, _ = _router(
        monkeypatch, tmp_path, capture=True,
        responses=['{"label": "x", "score": 0.1}'],
    )
    router.structured(Tier.FAST, system="sys", prompt="pr", output_type=Verdict, stage="shred.pass1")
    rows = [json.loads(l) for l in (tmp_path / "training_capture.jsonl").read_text().splitlines()]
    assert rows[0]["stage"] == "shred.pass1"
    assert rows[0]["prompt"] == "pr"
    assert json.loads(rows[0]["output"])["label"] == "x"


def _fake_run_dir(root: Path) -> Path:
    run = root / ("a" * 32)
    (run / "volumes" / "sections").mkdir(parents=True)
    (run / "state.json").write_text(json.dumps({
        "section_drafts": [
            {"section_id": "TECH-1", "human_edited": True},
            {"section_id": "PP-1", "human_edited": False},
        ]
    }))
    (run / "volumes" / "sections" / "TECH-1.md").write_text("Reviewer-corrected prose.")
    captures = [
        {"stage": "produce.write", "tier": "frontier", "system": "s",
         "prompt": 'outline: {"section_id": "TECH-1"} write it', "output": "Machine prose."},
        {"stage": "shred.pass1", "tier": "fast", "system": "s",
         "prompt": "extract", "output": '{"requirements": []}'},
    ]
    (run / "training_capture.jsonl").write_text(
        "\n".join(json.dumps(c) for c in captures)
    )
    return run


def test_collect_pairs_human_edits_as_preferences(tmp_path):
    _fake_run_dir(tmp_path)
    examples, stats = collect_runs(tmp_path)
    assert stats.runs_scanned == 1
    assert stats.captures == 1 and stats.preferences == 1
    pref = next(e for e in examples if e.kind == "preference")
    assert pref.corrected_output == "Reviewer-corrected prose."
    assert pref.output == "Machine prose."


def test_export_chat_jsonl_round_trip(tmp_path):
    _fake_run_dir(tmp_path)
    examples, _ = collect_runs(tmp_path)
    counts = export_chat_jsonl(examples, tmp_path / "ds", val_fraction=0.0)
    assert counts["train"] == 2 and counts["preference_pairs"] == 1
    rows = [json.loads(l) for l in (tmp_path / "ds" / "train.jsonl").read_text().splitlines()]
    pref = next(r for r in rows if r["meta"]["kind"] == "preference")
    # Human-corrected text is the training target; machine output is 'rejected'.
    assert pref["messages"][2]["content"] == "Reviewer-corrected prose."
    assert pref["rejected"] == "Machine prose."
    assert (tmp_path / "ds" / "DATASET_CARD.md").exists()
