"""A wanted moment is a REGION, and one window earns ONE credit for it.

The metric these tests hold to account is the one the optimisation scout
found gameable: a returned range used to earn one hit per ten-second
sample it spanned, so "return the whole recording" scored 0.33 recall on
the held-out set - three times the incumbent - for 22 181 seconds of
footage nobody asked for.

The defects named here, one test each:

* a window that spans every sample of a wanted region still earns ONE
  credit (it must not);
* the degenerate answer therefore scores WORSE than five tight windows
  (it used to score better);
* the seconds a window covers outside the wanted region are charged as
  false-positive seconds, and as wrong seconds when the editor takes it
  (they used to be free);
* the uncapped number is still reported, so the change of definition is
  visible rather than silent;
* two angles of ONE wanted moment are one region, not two (the
  conversation clock's offsets say so).
"""

import json
from pathlib import Path

import pytest

from library.tools import retrieval_eval
from library.tools.retrieval import consequence, regions

REPO_ROOT = Path(__file__).resolve().parents[3]
EDITORIAL_SET = Path("library/tools/editorial_retrieval_set.json")
HELD_OUT_SET = Path("library/tools/editorial_retrieval_heldout_set.json")


def _load(path):
    return json.loads((REPO_ROOT / path).read_text(encoding="utf-8"))


def _samples(count, asset="LC4930", start=0.0, step=10.0, span=10.0):
    """`count` labelled samples, one every `step` seconds."""
    return [{"instances": [{"asset": asset,
                            "start": start + index * step,
                            "end": start + index * step + span}]}
            for index in range(count)]


def _rules(payload):
    rules = dict(payload["scoring"]["editorial"])
    rules.setdefault(
        "multicam_offsets_to_reference",
        (payload.get("ground_truth_mapping") or {}).get(
            "multicam_offsets_to_reference") or {})
    return rules


def _score(payload, query, rows):
    corpus = retrieval_eval.Corpus.__new__(retrieval_eval.Corpus)
    assets = {instance["asset"]
              for moment in (query.get("moments") or [])
              for instance in moment["instances"]}
    corpus.name_of_clip = {asset: asset for asset in assets}
    report = {"results": [{"clip_id": row["asset"], "start": row["start"],
                           "end": row["end"], "score": 0.2,
                           "dense_score": None, "text": ""}
                          for row in rows]}
    return retrieval_eval.score_editorial(query, report, corpus,
                                          _rules(payload))


# ─── the credit is capped ──────────────────────────────────────────


def test_a_window_that_spans_every_sample_earns_one_credit():
    moments = _samples(21)
    regions_found = regions.wanted_regions(moments)
    assert len(regions_found) == 1, (
        "twenty contiguous samples of one condition are ONE wanted region")
    credit = regions.credit_windows(
        moments, regions_found,
        [{"asset": "LC4930", "start": -5.0, "end": 205.0}])
    assert len(credit["credited"]) == 1, (
        "a window that spans every sample of a region earns ONE credit; "
        "one window is one wanted thing, however many samples it covers")
    assert len(credit["overlapped"]) == 21, (
        "the uncapped count is kept, so the change is visible")


def test_returning_the_whole_recording_scores_worse_than_five_tight_windows():
    """The degenerate answer must not beat the honest one."""
    payload = _load(HELD_OUT_SET)
    query = next(q for q in payload["queries"] if q["id"] == "H01")
    rules = _rules(payload)
    moments = query["moments"]
    whole = [{"asset": "LC4930", "start": 0.0, "end": 225.0}]
    tight = [{"asset": "LC4930", "start": start, "end": start + 10.0}
             for start in (10.0, 40.0, 70.0, 100.0, 130.0)]
    whole_score = _score(payload, query, whole)
    tight_score = _score(payload, query, tight)
    assert whole_score["recall"] < tight_score["recall"], (
        "returning the whole recording used to score three times the "
        "incumbent's recall; the metric must not reward the volume")
    assert whole_score["recall_uncredited"] > tight_score["recall_uncredited"], (
        "the uncapped number is the OLD metric, and it still says the "
        "degenerate answer covers more; that is why it is reported beside "
        "the capped one")


def test_the_capped_number_is_the_headline_and_the_old_one_is_beside_it():
    payload = _load(HELD_OUT_SET)
    query = next(q for q in payload["queries"] if q["id"] == "H01")
    scored = _score(payload, query,
                    [{"asset": "LC4930", "start": 0.0, "end": 225.0}])
    assert scored["recall"] == pytest.approx(1 / 43, abs=1e-4)
    assert scored["recall_uncredited"] == pytest.approx(22 / 43, abs=1e-4)
    assert scored["credit_cap"] == "one credit per window per region"
    assert scored["regions"] == 1
    assert scored["regions_found"] == 1


def test_the_text_lane_is_not_changed_by_the_cap():
    """A text moment is a passage, and a window covering it is one credit."""
    payload = _load(EDITORIAL_SET)
    query = next(q for q in payload["queries"] if q["id"] == "E07")
    rules = _rules(payload)
    moment = query["moments"][0]
    instance = moment["instances"][0]
    scored = _score(payload, query,
                    [{"asset": instance["asset"], "start": instance["start"],
                      "end": instance["end"]}])
    assert scored["recall"] == scored["recall_uncredited"] == pytest.approx(
        1 / 3, abs=1e-4)
    assert scored["regions"] == len(query["moments"])


# ─── the extra seconds are charged ─────────────────────────────────


def test_the_seconds_outside_the_wanted_moment_are_charged():
    payload = _load(EDITORIAL_SET)
    query = next(q for q in payload["queries"] if q["id"] == "E01")
    instance = query["moments"][0]["instances"][0]
    scored = _score(payload, query, [
        {"asset": instance["asset"], "start": instance["start"] - 20.0,
         "end": instance["end"] + 20.0},
    ])
    consequence_row = scored["consequence"]
    assert consequence_row["excess_seconds"] == pytest.approx(38.0), (
        "the twenty seconds on each side of the wanted moment are footage "
        "nobody asked for, and they are charged")
    assert consequence_row["false_positive_seconds"] == pytest.approx(38.0)
    assert consequence_row["wrong_seconds_selected"] == pytest.approx(38.0), (
        "the editor takes the first window, and the extra footage would go "
        "into the cut")
    assert consequence_row["wrong_windows_selected"] == 0, (
        "the window does hold what the editor asked for; the charge is for "
        "the footage it brought along, not for being wrong")


def test_a_window_inside_the_wanted_moment_is_charged_nothing():
    payload = _load(EDITORIAL_SET)
    query = next(q for q in payload["queries"] if q["id"] == "E01")
    instance = query["moments"][0]["instances"][0]
    scored = _score(payload, query, [
        {"asset": instance["asset"], "start": instance["start"],
         "end": instance["end"]}])
    assert scored["consequence"]["excess_seconds"] == pytest.approx(0.0)
    assert scored["consequence"]["wrong_seconds_selected"] == pytest.approx(0.0)


# ─── two angles of one wanted moment are one region ────────────────


def test_two_angles_of_one_moment_are_one_region():
    payload = _load(EDITORIAL_SET)
    query = next(q for q in payload["queries"] if q["id"] == "E01")
    found = regions.wanted_regions(
        query["moments"], _rules(payload)["multicam_offsets_to_reference"])
    assert len(found) == 1, (
        "the two angles of one wanted moment are one region; the clock's "
        "offsets say which angles they are")
    assert len(found[0]["moments"]) == 1


def test_separate_moments_are_separate_regions():
    payload = _load(EDITORIAL_SET)
    query = next(q for q in payload["queries"] if q["id"] == "E02")
    found = regions.wanted_regions(
        query["moments"], _rules(payload)["multicam_offsets_to_reference"])
    assert len(found) == len(query["moments"]), (
        "three passages are three wanted things, and a window may cover "
        "two of them")


# ─── the summary reports the change ────────────────────────────────


def test_the_summary_reports_the_capped_and_the_uncapped_number():
    payload = _load(HELD_OUT_SET)
    query = next(q for q in payload["queries"] if q["id"] == "H01")
    scored = _score(payload, query,
                    [{"asset": "LC4930", "start": 0.0, "end": 225.0}])
    summary = retrieval_eval.summarise([scored], {})
    block = summary["editorial"]
    assert block["macro_recall"] == pytest.approx(1 / 43, abs=1e-4)
    assert block["macro_recall_uncredited"] == pytest.approx(22 / 43,
                                                              abs=1e-4)
    assert block["credit_cap"] == "one credit per window per region"
    assert block["excess_seconds"] == pytest.approx(0.0, abs=1e-4)
