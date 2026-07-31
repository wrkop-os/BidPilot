"""The keyless live-run path, exercised over real HTTP.

`BIDPILOT_CUSTOM_LLM_URL` is how BidPilot runs with no Anthropic key: point it
at anything OpenAI-shaped. Until now that route was covered only by env-var
routing assertions, so the HTTP request, the response parsing, the schema
retry, and the resilience behaviour had never actually executed. These tests
run them against a real server on a real socket.
"""

import os
from pathlib import Path

import pytest
from rich.console import Console

from bidpilot.audit import AuditLog
from bidpilot.kb.store import load_kb
from bidpilot.orchestrator import RunContext, new_run, run
from bidpilot.routing import ModelRouter, RefusalError, Tier
from bidpilot.state import Stage
from openai_stub import StubBehavior, StubServer
from test_orchestrator_e2e import EXAMPLE_KB, DESCRIPTION


_PORT = [8791]


def _next_port() -> int:
    _PORT[0] += 1
    return _PORT[0]


@pytest.fixture(autouse=True)
def _fast_backoff(monkeypatch):
    """Exercise the retry logic without paying its wall-clock cost."""
    from bidpilot.routing import CustomLLMBackend
    monkeypatch.setattr(CustomLLMBackend, "BACKOFF_BASE_S", 0.01)
    monkeypatch.setattr(CustomLLMBackend, "BACKOFF_CAP_S", 0.05)


@pytest.fixture
def no_anthropic_key(monkeypatch):
    """The whole point: prove it runs with no Anthropic credentials at all."""
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        monkeypatch.delenv(var, raising=False)


def _point_router_at(monkeypatch, server: StubServer, tiers: str = "all") -> None:
    monkeypatch.setenv("BIDPILOT_CUSTOM_LLM_URL", server.base_url)
    monkeypatch.setenv("BIDPILOT_CUSTOM_LLM_MODEL", "stub-model")
    monkeypatch.setenv("BIDPILOT_CUSTOM_LLM_TIERS", tiers)


def _package(tmp_path: Path) -> Path:
    src = tmp_path / "RFP_Package"
    src.mkdir()
    (src / "solicitation.txt").write_text(
        "Solicitation Number: W9123-26-R-0001\n"
        "NAICS Code: 541511\n"
        "Offers due: August 15, 2026 at 1:00 PM ET\n"
        "This is a Total Small Business Set-Aside.\n\n" + DESCRIPTION
    )
    return src


class _NoSam:
    def __getattr__(self, name):
        return lambda *a, **k: None


# -- the headline: a full run with no Anthropic key --------------------------


def _run_pipeline(tmp_path, server, cite: bool):
    # The stub answers enums with their first value, which for QAFinding is
    # "hard" — that would fail every run for a reason that has nothing to do
    # with what is under test. Pin the LLM half of QA to "soft" so any HARD
    # finding provably comes from a deterministic check.
    overrides = {"severity": "soft"}
    if cite:
        # A real KB entry, so writer claims are properly sourced.
        overrides["kb_source_id"] = "pp-gsa-dataplatform"
    server.behavior.field_overrides = overrides
    server.behavior.fill_optional = True
    src = _package(tmp_path)
    state, checkpoints = new_run("local:pkg", tmp_path / "runs", local_source=src)
    audit = AuditLog(Path(state.run_dir) / "audit.jsonl")
    ctx = RunContext(
        state=state,
        router=ModelRouter(audit=audit),
        sam=_NoSam(),
        kb=load_kb(str(EXAMPLE_KB)),
        checkpoints=checkpoints,
        audit=audit,
        console=Console(quiet=True),
        confirm=lambda q: True,
        actor="test",
        local_source=src,
    )
    return run(ctx)


def test_full_pipeline_runs_over_http_with_no_anthropic_key(
    tmp_path, monkeypatch, no_anthropic_key,
):
    """Local documents in, exported package out, every model call crossing a
    real socket to an OpenAI-compatible server. No Anthropic key, no SAM.gov,
    no Anthropic SDK involved anywhere in the run."""
    with StubServer(port=_next_port()) as server:
        _point_router_at(monkeypatch, server)
        state = _run_pipeline(tmp_path, server, cite=True)

        assert state.halted_reason is None, state.halted_reason
        assert all(state.is_done(s) for s in Stage)
        assert state.export_path and Path(state.export_path).exists()

        # Every call really went over the wire to the stub.
        assert len(server.behavior.calls) > 5
        assert all(c["model"] == "stub-model" for c in server.behavior.calls)
        assert os.environ.get("ANTHROPIC_API_KEY") is None


def test_uncited_claims_from_a_custom_model_still_block_export(
    tmp_path, monkeypatch, no_anthropic_key,
):
    """FR-10 is a property of the system, not of Anthropic. Pointing the router
    at an arbitrary endpoint must not become a way to ship a package whose
    company claims cite nothing."""
    with StubServer(port=_next_port()) as server:
        _point_router_at(monkeypatch, server)
        state = _run_pipeline(tmp_path, server, cite=False)

        assert state.halted_reason == "qa_hard_failures"
        assert state.export_path is None          # nothing shipped
        hard = state.qa_report.hard_failures()
        assert hard and all(f.category == "fabrication" for f in hard)
        assert any("Uncited company-fact claim" in f.description for f in hard)


def test_a_fabricated_kb_id_from_a_custom_model_is_caught(
    tmp_path, monkeypatch, no_anthropic_key,
):
    """The likelier failure with a smaller model: not omitting the citation but
    inventing one. A plausible-looking ID that resolves to nothing must not be
    mistaken for a real source."""
    with StubServer(port=_next_port()) as server:
        _point_router_at(monkeypatch, server)
        server.behavior.field_overrides = {"severity": "soft",
                                           "kb_source_id": "pp-totally-invented"}
        server.behavior.fill_optional = True
        src = _package(tmp_path)
        state, checkpoints = new_run("local:pkg", tmp_path / "runs", local_source=src)
        audit = AuditLog(Path(state.run_dir) / "audit.jsonl")
        state = run(RunContext(
            state=state, router=ModelRouter(audit=audit), sam=_NoSam(),
            kb=load_kb(str(EXAMPLE_KB)), checkpoints=checkpoints, audit=audit,
            console=Console(quiet=True), confirm=lambda q: True, actor="test",
            local_source=src,
        ))

        assert state.halted_reason == "qa_hard_failures"
        assert state.export_path is None
        hard = state.qa_report.hard_failures()
        assert any("nonexistent KB entry" in f.description
                   and "pp-totally-invented" in f.description for f in hard)


# -- the failure modes a self-hosted model actually exhibits -----------------


def test_a_transient_5xx_does_not_kill_the_run(monkeypatch, no_anthropic_key):
    """Self-hosted models restart, scale, and 503. A single blip must not
    destroy a run that is ten stages deep."""
    behavior = StubBehavior()
    behavior.fail_times = 2
    behavior.fail_status = 503
    with StubServer(behavior, port=_next_port()) as server:
        _point_router_at(monkeypatch, server)
        from bidpilot.models import Classification

        result = ModelRouter().structured(
            Tier.FAST, system="classify", prompt="do it",
            output_type=Classification, stage="test",
        )
        assert isinstance(result, Classification)
        assert behavior.fail_times == 0          # it really retried


def test_a_rate_limit_is_retried_too(monkeypatch, no_anthropic_key):
    behavior = StubBehavior()
    behavior.fail_times = 1
    behavior.fail_status = 429
    with StubServer(behavior, port=_next_port()) as server:
        _point_router_at(monkeypatch, server)
        from bidpilot.models import Classification

        assert ModelRouter().structured(
            Tier.FAST, system="classify", prompt="do it",
            output_type=Classification, stage="test",
        )


def test_prose_wrapped_json_is_still_parsed(monkeypatch, no_anthropic_key):
    """Small models chatter and fence their JSON no matter what the prompt
    says. Refusing to parse that would make the path unusable in practice."""
    behavior = StubBehavior()
    behavior.wrap_in_prose = True
    with StubServer(behavior, port=_next_port()) as server:
        _point_router_at(monkeypatch, server)
        from bidpilot.models import Classification

        assert ModelRouter().structured(
            Tier.FAST, system="classify", prompt="do it",
            output_type=Classification, stage="test",
        )


def test_a_schema_violation_is_retried_with_the_error_fed_back(
    monkeypatch, no_anthropic_key,
):
    behavior = StubBehavior()
    behavior.bad_json_once = True
    with StubServer(behavior, port=_next_port()) as server:
        _point_router_at(monkeypatch, server)
        from bidpilot.models import Classification

        assert ModelRouter().structured(
            Tier.FAST, system="classify", prompt="do it",
            output_type=Classification, stage="test",
        )
        assert len(behavior.calls) == 2
        # The retry must actually tell the model what was wrong.
        retry_prompt = behavior.calls[1]["messages"][-1]["content"]
        assert "failed validation" in retry_prompt


def test_a_content_filter_surfaces_as_a_refusal_not_a_parse_error(
    monkeypatch, no_anthropic_key,
):
    behavior = StubBehavior()
    behavior.content_filter = True
    with StubServer(behavior, port=_next_port()) as server:
        _point_router_at(monkeypatch, server)
        from bidpilot.models import Classification

        with pytest.raises(RefusalError):
            ModelRouter().structured(
                Tier.FAST, system="classify", prompt="do it",
                output_type=Classification, stage="test",
            )


def test_an_unreachable_endpoint_says_so_instead_of_hanging(
    monkeypatch, no_anthropic_key,
):
    """A typo'd URL is the most likely first-run mistake on this path."""
    monkeypatch.setenv("BIDPILOT_CUSTOM_LLM_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("BIDPILOT_CUSTOM_LLM_MODEL", "stub-model")
    monkeypatch.setenv("BIDPILOT_CUSTOM_LLM_TIERS", "all")
    from bidpilot.models import Classification
    from bidpilot.routing import MissingCredentialsError

    with pytest.raises((RuntimeError, OSError)) as exc:
        ModelRouter().structured(
            Tier.FAST, system="classify", prompt="do it",
            output_type=Classification, stage="test",
        )
    # And specifically NOT the "no credentials" error — one IS configured.
    assert not isinstance(exc.value, MissingCredentialsError)


# -- tier routing over the wire ----------------------------------------------


def test_only_the_configured_tier_goes_to_the_custom_model(
    monkeypatch, no_anthropic_key,
):
    """A partial rollout (fast tier local, frontier still Anthropic) must not
    silently send frontier work to the custom model."""
    with StubServer(port=_next_port()) as server:
        _point_router_at(monkeypatch, server, tiers="fast")
        router = ModelRouter()
        assert router.model_for(Tier.FAST).startswith("custom:")
        assert not router.model_for(Tier.FRONTIER).startswith("custom:")

        # Frontier now needs Anthropic credentials, which are absent — and it
        # must say so rather than quietly falling back to the custom model.
        from bidpilot.models import Classification
        from bidpilot.routing import MissingCredentialsError

        with pytest.raises(MissingCredentialsError):
            router.structured(Tier.FRONTIER, system="s", prompt="p",
                              output_type=Classification, stage="test")
        assert server.behavior.calls == []


def test_the_audit_log_records_custom_model_calls(tmp_path, monkeypatch, no_anthropic_key):
    """Invariant: every model call is audited, whichever backend served it."""
    with StubServer(port=_next_port()) as server:
        _point_router_at(monkeypatch, server)
        audit_path = tmp_path / "audit.jsonl"
        router = ModelRouter(audit=AuditLog(audit_path))
        from bidpilot.models import Classification

        router.structured(Tier.FAST, system="s", prompt="p",
                          output_type=Classification, stage="classify")

        assert audit_path.exists()
        body = audit_path.read_text(encoding="utf-8")
        assert "classify" in body
        assert "custom" in body.lower()
        assert os.environ.get("ANTHROPIC_API_KEY") is None
