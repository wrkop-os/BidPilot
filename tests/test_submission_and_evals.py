from bidpilot.agents.submission import build_ics, sheet_to_markdown
from bidpilot.models import SubmissionSheet
from evals.harness import score_matrix


def _sheet(**kw):
    defaults = dict(channel="email", destination="co@agency.gov", deadline="2026-08-15 14:00")
    defaults.update(kw)
    return SubmissionSheet(**defaults)


def test_ics_contains_three_events_with_questions():
    sheet = _sheet(questions_deadline="2026-08-01 12:00", deadline_timezone="ET")
    ics = build_ics(sheet, "IT Services RFP")
    assert ics is not None
    assert ics.count("BEGIN:VEVENT") == 3
    assert "T-48h" in ics
    assert "DTSTART:20260813T140000" in ics  # 48h before the deadline


def test_ics_none_when_unparseable():
    assert build_ics(_sheet(deadline="two weeks after award"), "X") is None


def test_sheet_markdown_flags_missing_timezone():
    md = sheet_to_markdown(_sheet())
    assert "no timezone stated" in md
    assert "never submits" in md


def test_eval_harness_recall():
    gold = [
        "The offeror shall submit Volume I Technical in PDF format.",
        "Proposals must be received no later than 2:00 PM Eastern Time.",
        "The offeror shall provide three past performance references.",
    ]
    extracted = [
        "The offeror shall submit Volume I Technical in PDF format.",
        "Proposals must be received no later than 2:00 PM Eastern Time on the due date.",
        "Some unrelated extracted row.",
    ]
    score = score_matrix(extracted, gold)
    assert score.gold_total == 3
    assert 0.6 < score.recall < 1.0  # 2 of 3 matched
    assert score.missed == ["The offeror shall provide three past performance references."]
    assert not score.passes_g2()


def test_eval_harness_perfect_recall():
    gold = ["The offeror shall submit Volume I."]
    score = score_matrix(gold + ["extra row"], gold)
    assert score.recall == 1.0
    assert score.passes_g2()
