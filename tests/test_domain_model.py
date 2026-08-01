"""The trained domain model: corpus, promotion discipline, and the recall net.

The model is a narrow expert on one question — is this sentence of federal
solicitation text a binding requirement? These tests defend the things that
make it trustworthy rather than merely present: it cannot serve without a
promotion record bound to its own bytes, it cannot be leaked into by its own
training data, and it can only ever add coverage, never remove it.
"""

import json
from pathlib import Path

import pytest

from bidpilot.ml import recall_net, requirements_model
from bidpilot.ml.corpus import CATEGORIES, CorpusStats, build_corpus, seed_corpus
from bidpilot.ml.train_requirements import (
    choose_none_threshold,
    evaluate,
    split_by_family,
    train,
)

MODELS = Path(__file__).resolve().parent.parent / "models"
ARTIFACT = MODELS / "requirements.joblib"
pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")


# -- corpus -------------------------------------------------------------------


def test_seed_corpus_covers_every_category_with_several_families_each():
    """One family per category would mean a held-out family removes an entire
    sub-topic from training — which is exactly how the first version of this
    model scored at chance on categories."""
    rows = seed_corpus()
    stats = CorpusStats.of(rows)
    assert set(stats.by_category) == set(CATEGORIES)
    by_cat_families: dict[str, set[str]] = {}
    for row in rows:
        by_cat_families.setdefault(row.category, set()).add(row.family)
    for category, families in by_cat_families.items():
        assert len(families) >= 5, f"{category} has only {len(families)} families"


def test_corpus_is_deterministic_for_a_seed():
    assert [r.text for r in seed_corpus(seed=17)] == [r.text for r in seed_corpus(seed=17)]
    assert [r.text for r in seed_corpus(seed=17)] != [r.text for r in seed_corpus(seed=99)]


def test_the_split_never_puts_a_family_on_both_sides():
    """Paraphrases within a family are near-duplicates. A row-level split would
    report a score the model has not earned."""
    rows = build_corpus()
    train_rows, test_rows = split_by_family(rows)
    train_families = {r.family for r in train_rows}
    test_families = {r.family for r in test_rows}
    assert train_families & test_families == set()
    assert test_rows and train_rows
    # Every category must survive on the training side or it cannot be learned.
    assert {r.category for r in train_rows} == set(CATEGORIES)


def test_captured_requirements_are_grouped_by_run(tmp_path):
    """Sentences from one solicitation must not straddle the split either."""
    from bidpilot.ml.corpus import captured_corpus

    run = tmp_path / ("a" * 32)
    run.mkdir(parents=True)
    (run / "state.json").write_text(json.dumps({
        "matrix": {"requirements": [
            {"verbatim_text": "The offeror shall submit a quality control plan.",
             "category": "content"},
            {"verbatim_text": "short", "category": "content"},          # too short
            {"verbatim_text": "Something with a bogus label here.", "category": "nope"},
        ]},
    }))
    rows = captured_corpus(tmp_path)
    assert len(rows) == 1
    assert rows[0].source == "captured"
    assert rows[0].family.startswith("run:")


# -- promotion discipline -----------------------------------------------------


def test_a_model_without_a_promotion_record_refuses_to_load(tmp_path):
    fake = tmp_path / "model.joblib"
    fake.write_bytes(b"not really a model")
    requirements_model.reset_cache()
    assert requirements_model.load(str(fake)) is None


def test_a_swapped_artifact_is_rejected_even_with_a_passing_record(tmp_path):
    """joblib.load is pickle: a swapped artifact is code execution, not just a
    bad score. The record must describe THESE bytes."""
    fake = tmp_path / "model.joblib"
    fake.write_bytes(b"original")
    fake.with_suffix(".metrics.json").write_text(json.dumps({
        "ships_screen": True,
        "model_sha256": "0" * 64,          # does not match the file
    }))
    requirements_model.reset_cache()
    assert requirements_model.load(str(fake)) is None


def test_an_unpromoted_artifact_refuses_even_if_it_loads(tmp_path):
    import hashlib

    import joblib

    from bidpilot.ml.requirements_model import build_pipeline

    pipeline = build_pipeline()
    rows = seed_corpus(per_phrasing=2)
    pipeline.fit([r.text for r in rows], [r.category for r in rows])
    path = tmp_path / "m.joblib"
    joblib.dump(pipeline, path)
    path.with_suffix(".metrics.json").write_text(json.dumps({
        "ships_screen": False,             # failed its gate
        "model_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }))
    requirements_model.reset_cache()
    assert requirements_model.load(str(path)) is None


def test_no_configured_model_is_a_normal_state_not_an_error(monkeypatch):
    monkeypatch.delenv(requirements_model.ENV_MODEL_PATH, raising=False)
    requirements_model.reset_cache()
    assert requirements_model.load() is None


# -- training -----------------------------------------------------------------


def test_training_produces_a_promoted_screen_and_an_honest_record(tmp_path):
    """The end-to-end training contract: grouped split, threshold chosen on
    train-side folds, gates applied to held-out families, record bound to the
    artifact's hash."""
    out = tmp_path / "requirements.joblib"
    record = train(out, per_phrasing=4)

    assert out.exists()
    sidecar = out.with_suffix(".metrics.json")
    assert sidecar.exists()

    import hashlib
    assert record["model_sha256"] == hashlib.sha256(out.read_bytes()).hexdigest()

    # Screening is the capability that matters and must be earned.
    assert record["ships_screen"] is True
    assert record["held_out"]["requirement_recall"] >= 0.95
    # Categorization is judged separately — it may or may not be earned, but
    # the record must state which.
    assert isinstance(record["ships_categorize"], bool)
    assert record["corpus"]["split"].startswith("grouped by template family")
    # The operating point comes from several folds, not one lucky split.
    assert len(record["threshold_folds"]) >= 3


def test_the_threshold_search_is_conservative_by_construction():
    """Recall rises with the threshold, so the search must return something
    that clears the *calibration* target, not merely the gate."""
    from bidpilot.ml.requirements_model import build_pipeline

    rows = build_corpus(per_phrasing=3)
    train_rows, calib_rows = split_by_family(rows, seed=5)
    pipeline = build_pipeline()
    pipeline.fit([r.text for r in train_rows], [r.category for r in train_rows])

    threshold = choose_none_threshold(pipeline, calib_rows, 0.95)
    scored = evaluate(pipeline, calib_rows, threshold)
    assert scored["requirement_recall"] >= 0.95


def test_evaluate_measures_the_rule_that_actually_serves():
    """Scoring with plain argmax would measure a decision rule the product
    does not use — the serving path thresholds on P(none)."""
    from bidpilot.ml.requirements_model import build_pipeline

    rows = build_corpus(per_phrasing=3)
    pipeline = build_pipeline()
    pipeline.fit([r.text for r in rows], [r.category for r in rows])

    permissive = evaluate(pipeline, rows, threshold=0.95)
    aggressive = evaluate(pipeline, rows, threshold=0.10)
    # A higher bar for "none" keeps more sentences, so recall cannot fall.
    assert permissive["requirement_recall"] >= aggressive["requirement_recall"]
    # And it necessarily screens less text away.
    assert permissive["screened_out_rate"] <= aggressive["screened_out_rate"]


# -- the recall net -----------------------------------------------------------


class _Doc:
    def __init__(self, name, full_text):
        self.name = name
        self.full_text = full_text


class _Tree:
    def __init__(self, docs):
        self.docs = docs


class _Req:
    def __init__(self, verbatim_text):
        self.verbatim_text = verbatim_text


class _Matrix:
    def __init__(self, requirements):
        self.requirements = requirements


def test_sentence_splitting_survives_clause_numbers_and_section_refs():
    text = ("The offeror shall comply with FAR 52.222-43. "
            "Volume I is limited to 20 pages. See Sec. 3 for details.")
    sentences = recall_net.split_sentences(text)
    assert any("52.222-43" in s for s in sentences)
    # A clause number must not be split into fragments.
    assert not any(s.strip() in {"222-43.", "43."} for s in sentences)
    assert len(sentences) <= 4


def test_the_net_is_a_no_op_without_a_promoted_model(monkeypatch):
    monkeypatch.delenv(requirements_model.ENV_MODEL_PATH, raising=False)
    requirements_model.reset_cache()
    tree = _Tree([_Doc("rfp.pdf", "The offeror shall submit a staffing plan.")])
    assert recall_net.find_missed(tree, _Matrix([])) == []


@pytest.mark.skipif(not ARTIFACT.exists(),
                    reason="no trained artifact (run bidpilot.ml.train_requirements)")
def test_the_net_finds_a_requirement_the_matrix_missed(monkeypatch):
    """The whole point: a requirement present in the corpus but absent from
    the matrix must come back as a candidate for a human to confirm."""
    monkeypatch.setenv(requirements_model.ENV_MODEL_PATH, str(ARTIFACT))
    requirements_model.reset_cache()

    tree = _Tree([_Doc("rfp.pdf",
                       "The Government operates a nationwide network of offices. "
                       "The offeror shall submit a Quality Control Plan describing "
                       "inspection and corrective action procedures. "
                       "Figure 2 depicts the current architecture.")])
    # The matrix caught nothing.
    found = recall_net.find_missed(tree, _Matrix([]))
    assert any("Quality Control Plan" in c.text for c in found)
    assert all(0.0 <= c.confidence <= 1.0 for c in found)
    assert all(c.doc == "rfp.pdf" for c in found)


@pytest.mark.skipif(not ARTIFACT.exists(), reason="no trained artifact")
def test_the_net_stays_quiet_about_requirements_already_captured(monkeypatch):
    monkeypatch.setenv(requirements_model.ENV_MODEL_PATH, str(ARTIFACT))
    requirements_model.reset_cache()

    sentence = ("The offeror shall submit a Quality Control Plan describing "
                "inspection and corrective action procedures.")
    tree = _Tree([_Doc("rfp.pdf", sentence)])
    assert recall_net.find_missed(tree, _Matrix([_Req(sentence)])) == []


@pytest.mark.skipif(not ARTIFACT.exists(), reason="no trained artifact")
def test_coverage_matching_tolerates_a_trimmed_matrix_extract(monkeypatch):
    """The LLM often records a slightly trimmed version of a sentence. Treating
    that as uncovered would bury the reviewer in false candidates."""
    monkeypatch.setenv(requirements_model.ENV_MODEL_PATH, str(ARTIFACT))
    requirements_model.reset_cache()

    full = ("The offeror shall submit a transition plan covering the 30-day "
            "phase-in period.")
    trimmed = "offeror shall submit a transition plan covering the 30-day phase-in"
    tree = _Tree([_Doc("rfp.pdf", full)])
    assert recall_net.find_missed(tree, _Matrix([_Req(trimmed)])) == []


@pytest.mark.skipif(not ARTIFACT.exists(), reason="no trained artifact")
def test_the_promoted_artifact_reports_what_it_earned():
    metrics = json.loads(ARTIFACT.with_suffix(".metrics.json").read_text())
    assert metrics["ships_screen"] is True
    assert metrics["held_out"]["requirement_recall"] >= 0.95
    # The record must be explicit about the split and the operating point, so
    # a reader can tell whether the number means anything.
    assert "grouped by template family" in metrics["corpus"]["split"]
    assert 0.0 < metrics["none_threshold"] <= 1.0
    assert metrics["gold_independent"]["n"] > 0


# -- wrapped text (the shape real documents arrive in) ------------------------


def test_wrapped_lines_are_rejoined_into_whole_sentences():
    """PDF and DOCX extraction wraps constantly. Splitting on newlines alone
    turns one requirement into fragments — each too short to classify, and none
    quotable as verbatim binding language."""
    text = ("The offeror shall provide Tier 1 and Tier 2 service desk support for\n"
            "approximately 1,200 users, including incident intake and triage.\n"
            "Period of performance: one base year plus\n"
            "four 12-month option periods.\n")
    sentences = recall_net.split_sentences(text)
    assert any("incident intake and triage" in s and "Tier 1" in s for s in sentences)
    assert any("base year plus four 12-month" in s for s in sentences)
    assert not any(s.endswith("support for") for s in sentences)


def test_headings_and_bullets_still_start_new_blocks():
    text = ("SECTION L INSTRUCTIONS\n"
            "The offeror shall submit three copies.\n"
            "- Volume I is limited to 20 pages\n"
            "- Volume II is limited to 10 pages\n")
    sentences = recall_net.split_sentences(text)
    joined = " || ".join(sentences)
    assert "Volume I is limited to 20 pages" in joined
    assert "Volume II is limited to 10 pages" in joined
    # The two bullets must not be welded together.
    assert not any("20 pages - Volume II" in s for s in sentences)


# -- the prefilter (opt-in cost lever) ----------------------------------------


def test_prefilter_is_off_unless_explicitly_enabled(monkeypatch):
    """Invariant 4 prefers recall over cost on this stage, so trading recall
    for tokens must never happen by default."""
    from bidpilot.ml import prefilter

    monkeypatch.delenv(prefilter.ENV_ENABLED, raising=False)
    assert prefilter.enabled() is False
    text = "The offeror shall submit a staffing plan. Attachment 3 is the wage determination."
    result = prefilter.prefilter_text(text)
    assert result.text == text                 # untouched
    assert result.dropped_sentences == 0


@pytest.mark.skipif(not ARTIFACT.exists(), reason="no trained artifact")
def test_prefilter_shrinks_text_and_keeps_the_requirements(monkeypatch):
    from bidpilot.ml import prefilter

    monkeypatch.setenv(prefilter.ENV_ENABLED, "1")
    monkeypatch.setenv(requirements_model.ENV_MODEL_PATH, str(ARTIFACT))
    requirements_model.reset_cache()

    text = (
        "The offeror shall submit Volume I not to exceed 20 pages.\n"
        "Attachment 3 contains the wage determination.\n"
        "The Government will evaluate technical merit and price.\n"
        "Figure 2 depicts the current system architecture.\n"
        "Proposals are due at 2:00 PM Eastern Time on March 14, 2027.\n"
    )
    result = prefilter.prefilter_text(text)
    assert result.dropped_sentences > 0
    assert result.chars_after < result.chars_before
    # Every binding sentence must survive.
    assert "not to exceed 20 pages" in result.text
    assert "2:00 PM Eastern" in result.text
    assert "evaluate technical merit" in result.text
    assert "prefilter:" in result.summary()


@pytest.mark.skipif(not ARTIFACT.exists(), reason="no trained artifact")
def test_the_prefilter_notice_states_the_measured_risk(monkeypatch):
    """Enabling this trades recall for tokens; the operator must be told how
    much recall, in requirements, not adjectives."""
    from bidpilot.ml import prefilter

    monkeypatch.setenv(requirements_model.ENV_MODEL_PATH, str(ARTIFACT))
    requirements_model.reset_cache()
    notice = prefilter.prefilter_notice()
    assert "in every 1,000" in notice
    assert "cannot be recovered" in notice
    assert prefilter.ENV_ENABLED in notice


# -- gate robustness ----------------------------------------------------------


@pytest.mark.skipif(not ARTIFACT.exists(), reason="no trained artifact")
def test_the_gate_is_measured_across_several_splits_not_one():
    """A single family split swung recall from 0.884 to 1.000 on this corpus.
    Promoting off one split would ship a headline number that was an artifact
    of the seed."""
    metrics = json.loads(ARTIFACT.with_suffix(".metrics.json").read_text())
    held = metrics["held_out"]
    assert held["splits_evaluated"] >= 3
    assert held["aggregation"] == "worst across splits"
    # The reported figure is the worst case, so it cannot exceed the mean.
    assert held["requirement_recall"] <= held["requirement_recall_mean"] + 1e-9
    assert held["requirement_recall"] >= 0.95
