"""QA-010b: the tracked numbers, derived from a project's own records.

`library/tools/finished_video_history.py` reads the version ledger, the
creative commit ledger, the unified edit history and the delivery
acceptances of ONE project and derives the four numbers the
finished-video benchmark tracks.  Each test here names the defect it
would catch:

- a reel nobody looked at reading as a REFUSED first attempt;
- a reel with no recorded act reporting zero attempts;
- a reel nobody corrected reporting zero seconds of correction;
- the correction window measured from the wrong act;
- preserved work reporting success for a row that lost an item;
- a reel with no recorded rows reporting an empty success;
- an unreadable ledger reporting empty numbers instead of refusing;
- a corpus entry carrying two accounts of the same run;
- a derived history the benchmark cannot score.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from library.tools import finished_video_benchmark as benchmark
from library.tools import finished_video_history as history
from library.tools.versions import reel_versions

REEL = "Reel 13 - the-accounting-firm-ai-called-healthcare"


# ── helpers ───────────────────────────────────────────────────────────────

def _rows(name: str, items: list[dict]) -> dict:
    return {name: {"count": len(items), "frames": 10, "index": 1,
                   "items": items}}


def _item(name: str, start: int, end: int) -> dict:
    return {"name": name, "start": start, "end": end,
            "duration": end - start, "enabled": True}


@pytest.fixture
def records(monkeypatch, tmp_path: Path):
    """A project's records, held in memory, so a test can shape them."""
    state: dict = {"versions": [], "revisions": [], "corrections": [],
                   "acceptance": None, "unreadable": False}

    def _versions(_project, reel, *args, **kwargs):
        if state["unreadable"]:
            raise ValueError("the ledger is broken")
        return [row for row in state["versions"] if row["reel"] == reel]

    def _revisions(project, *args, **kwargs):
        if state["unreadable"]:
            raise ValueError("the ledger is broken")
        return list(state["revisions"])

    def _history_for(_review_dir, reel, *args, **kwargs):
        return [row for row in state["corrections"] if row["reel"] == reel]

    def _acceptance_for(_project, reel, *args, **kwargs):
        return state["acceptance"]

    monkeypatch.setattr(reel_versions, "versions_of", _versions)
    from library.tools import creative_revision, reel_acceptance, reel_edit_history

    monkeypatch.setattr(creative_revision, "list_revisions", _revisions)
    monkeypatch.setattr(reel_edit_history, "history_for", _history_for)
    monkeypatch.setattr(reel_acceptance, "acceptance_for", _acceptance_for)
    return state


# ── the tests ─────────────────────────────────────────────────────────────

def test_a_reel_nobody_looked_at_is_not_a_refused_first_attempt(
        records, tmp_path: Path) -> None:
    """Nothing recorded decides it, so the answer is `None`, not `False`.

    `False` would be counted as a refusal by the benchmark: a reel
    nobody looked at is not a reel that was turned down.
    """
    derived = history.derive_history(str(tmp_path), REEL)

    assert derived["first_attempt_accepted"] is None
    assert "first_pass_acceptance" in derived["unmeasured"]


def test_a_reel_with_no_recorded_act_reports_no_attempts_not_zero(
        records, tmp_path: Path) -> None:
    """Zero attempts would read as a reel nobody built."""
    derived = history.derive_history(str(tmp_path), REEL)

    assert derived["attempts"] is None
    assert "attempts" in derived["unmeasured"]


def test_a_reel_nobody_corrected_reports_no_correction_time_not_zero(
        records, tmp_path: Path) -> None:
    """Nobody corrected this reel: nothing was measured, and zero would
    be a claim that the reel needed no repair."""
    records["versions"] = [{
        "reel": REEL, "kind": "build", "version": 1,
        "at": "2026-10-20T08:00:00+00:00",
        "rows": _rows("video:Akshita", [_item("LC4932.MXF", 0, 100)]),
    }]
    derived = history.derive_history(str(tmp_path), REEL)

    assert derived["human_correction_seconds"] is None
    assert "human_correction_seconds" in derived["unmeasured"]


def test_a_recorded_correction_means_the_first_attempt_was_not_accepted(
        records, tmp_path: Path) -> None:
    records["corrections"] = [{
        "reel": REEL, "act": "manual_edit", "actor": "captain",
        "at": "2026-10-20T22:20:50+00:00",
    }]
    derived = history.derive_history(str(tmp_path), REEL)

    assert derived["first_attempt_accepted"] is False
    assert "first_pass_acceptance" not in derived["unmeasured"]


def test_an_accepted_reel_with_no_correction_is_a_first_pass_acceptance(
        records, tmp_path: Path) -> None:
    records["acceptance"] = {"by": "the captain",
                              "accepted_at": "2026-10-21T10:00:00+00:00"}
    derived = history.derive_history(str(tmp_path), REEL)

    assert derived["first_attempt_accepted"] is True


def test_a_reel_accepted_and_then_corrected_is_not_a_first_pass_acceptance(
        records, tmp_path: Path) -> None:
    """Taken and then changed is not accepted first-pass, and the rule
    is the one `edit_outcome` already follows."""
    records["acceptance"] = {"by": "the captain",
                              "accepted_at": "2026-10-21T10:00:00+00:00"}
    records["corrections"] = [{
        "reel": REEL, "act": "manual_edit", "actor": "captain",
        "at": "2026-10-21T18:00:00+00:00",
    }]
    derived = history.derive_history(str(tmp_path), REEL)

    assert derived["first_attempt_accepted"] is False


def test_the_correction_window_is_measured_from_the_last_act_before_it(
        records, tmp_path: Path) -> None:
    records["versions"] = [
        {"reel": REEL, "kind": "build", "version": 1,
         "at": "2026-10-20T08:00:00+00:00", "rows": {}},
        {"reel": REEL, "kind": "touch", "version": 2,
         "at": "2026-10-20T20:00:00+00:00", "rows": {}},
        {"reel": REEL, "kind": "touch", "version": 3,
         "at": "2026-10-21T12:00:00+00:00", "rows": {}},
    ]
    records["corrections"] = [{
        "reel": REEL, "act": "manual_edit", "actor": "captain",
        "at": "2026-10-20T22:20:50+00:00",
    }]
    derived = history.derive_history(str(tmp_path), REEL)

    # 20:00 -> 22:20:50, and NOT from the 08:00 build and NOT from the
    # act that came after the correction.
    assert derived["human_correction_seconds"] == pytest.approx(8450, abs=1)
    assert "UPPER BOUND" in derived["basis"]["human_correction_seconds"]


def test_the_attempt_count_is_the_recorded_acts_on_the_reel(
        records, tmp_path: Path) -> None:
    records["versions"] = [
        {"reel": REEL, "kind": "build", "version": 1,
         "at": "2026-10-20T08:00:00+00:00", "rows": {}},
        {"reel": REEL, "kind": "touch", "version": 2,
         "at": "2026-10-20T20:00:00+00:00", "rows": {}},
    ]
    derived = history.derive_history(str(tmp_path), REEL)

    assert derived["attempts"] == 2


def test_preserved_work_reports_the_row_that_lost_an_item(
        records, tmp_path: Path) -> None:
    records["versions"] = [
        {"reel": REEL, "kind": "build", "version": 1,
         "at": "2026-10-20T08:00:00+00:00",
         "rows": _rows("video:Subtitles",
                       [_item("sub_a.mov", 0, 20), _item("sub_b.mov", 20, 40)])},
        {"reel": REEL, "kind": "touch", "version": 2,
         "at": "2026-10-20T20:00:00+00:00",
         "rows": _rows("video:Subtitles", [_item("sub_a.mov", 0, 20)])},
    ]
    derived = history.derive_history(str(tmp_path), REEL)

    checks = derived["preserved_work"]["checks"]
    assert checks["row:video:Subtitles"] is False
    assert checks["no_item_was_dropped"] is False


def test_preserved_work_reports_a_surviving_row_as_preserved(
        records, tmp_path: Path) -> None:
    records["versions"] = [
        {"reel": REEL, "kind": "build", "version": 1,
         "at": "2026-10-20T08:00:00+00:00",
         "rows": _rows("video:Akshita", [_item("LC4932.MXF", 0, 100)])},
        {"reel": REEL, "kind": "touch", "version": 2,
         "at": "2026-10-20T20:00:00+00:00",
         "rows": _rows("video:Akshita", [_item("LC4932.MXF", 0, 100)])},
    ]
    derived = history.derive_history(str(tmp_path), REEL)

    checks = derived["preserved_work"]["checks"]
    assert checks["row:video:Akshita"] is True
    assert checks["no_item_was_dropped"] is True


def test_a_reel_with_no_recorded_rows_has_no_preserved_work_to_report(
        records, tmp_path: Path) -> None:
    """An empty success is not a success: nothing was compared."""
    records["versions"] = [
        {"reel": REEL, "kind": "build", "version": 1,
         "at": "2026-10-20T08:00:00+00:00", "rows": {}},
    ]
    derived = history.derive_history(str(tmp_path), REEL)

    assert derived["preserved_work"] is None
    assert "preserved_work" in derived["unmeasured"]


def test_an_unreadable_ledger_refuses_instead_of_reporting_no_numbers(
        records, tmp_path: Path) -> None:
    """A broken record is not an empty one: reporting `None` for
    everything would claim that nobody did anything."""
    records["unreadable"] = True

    with pytest.raises(history.HistoryError):
        history.derive_history(str(tmp_path), REEL)


def test_a_corpus_entry_carrying_two_accounts_of_one_run_is_refused(
        tmp_path: Path) -> None:
    entry = {"video_id": "reel-01",
             "history": {"attempts": 2},
             "history_from": {"project": str(tmp_path), "reel": REEL}}

    with pytest.raises(history.HistoryError):
        history.history_for_corpus(entry)


def test_a_derived_history_is_a_history_the_benchmark_can_score(
        records, tmp_path: Path) -> None:
    """The derived block must pass the corpus contract, or the numbers
    the module derives could never be scored."""
    records["versions"] = [
        {"reel": REEL, "kind": "build", "version": 1,
         "at": "2026-10-20T08:00:00+00:00",
         "rows": _rows("video:Akshita", [_item("LC4932.MXF", 0, 100)])},
        {"reel": REEL, "kind": "touch", "version": 2,
         "at": "2026-10-20T20:00:00+00:00",
         "rows": _rows("video:Akshita", [_item("LC4932.MXF", 0, 100)])},
    ]
    records["corrections"] = [{
        "reel": REEL, "act": "manual_edit", "actor": "captain",
        "at": "2026-10-20T22:20:50+00:00",
    }]
    derived = history.derive_history(str(tmp_path), REEL)

    assert benchmark.validate_corpus({
        "schema": benchmark.CORPUS_SCHEMA,
        "corpus_id": "a-corpus",
        "videos": [{"video_id": "reel-01", "path": "/tmp/reel.mp4",
                    "brief": {"id": "b", "instruction": "do it",
                              "acceptance_criteria": [
                                  {"id": "c", "statement": "it is done"}]},
                    "history": derived}],
    }) == []
