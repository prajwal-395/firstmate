"""A score is not a probability, a top hit is not checked, a new model is
not an argument.

Three defects, three families of tests:

* **VI-004** - the retrieval score was read as if it were the chance of
  being right, and no threshold was derived from anything measured.  A
  calibration curve and an abstention threshold must come from the
  harness's own labelled outcomes, per lane, and a score with no
  probability-scale signal must be reported as uncalibratable rather
  than quietly pooled in.
* **VI-008** - the frame lane ships every hit labelled UNVERIFIED, and
  nothing ever re-read one.  The top candidates must be re-read at
  higher fidelity, and a window that cannot be re-read must be reported
  as unverifiable rather than dressed up as confirmed.
* **VI-009** - a candidate retrieval was adopted because it was newer.
  A change must be measured against the incumbent on the same queries,
  weighing recall AND the false-positive consequence, and a candidate
  that ties must NOT be adopted.
"""

import pytest

from library.tools.retrieval import calibration, incumbent, verify

# ─── VI-004: the score reads as a probability, or says it does not ──


def _samples():
    """Twenty windows: high scores that are usually right, low ones that
    are usually wrong, and one high score that is wrong."""
    rows = []
    for _ in range(9):
        rows.append({"score": 0.9, "hit": True, "lane": "text",
                     "signal": "dense"})
    rows.append({"score": 0.9, "hit": False, "lane": "text",
                 "signal": "dense"})
    for _ in range(9):
        rows.append({"score": 0.2, "hit": False, "lane": "text",
                     "signal": "dense"})
    rows.append({"score": 0.2, "hit": True, "lane": "text",
                 "signal": "dense"})
    return rows


def test_a_calibrated_score_reads_as_a_probability():
    report = calibration.calibrate(_samples())
    assert report["expected_calibration_error"] < 0.1, (
        "a score of 0.9 that comes back right nine times in ten reads as "
        "a probability")
    assert report["verdict"].startswith("calibrated:"), (
        f"expected a calibrated verdict, got {report['verdict']!r}")


def test_a_score_that_overstates_its_confidence_is_reported_as_such():
    rows = [{"score": 0.9, "hit": False, "lane": "text",
             "signal": "dense"} for _ in range(9)]
    rows += [{"score": 0.2, "hit": True, "lane": "text",
              "signal": "dense"} for _ in range(9)]
    report = calibration.calibrate(rows)
    assert report["expected_calibration_error"] > 0.25
    assert "miscalibrated" in report["verdict"]


def test_a_score_with_no_probability_scale_is_not_calibrated_silently():
    rows = [{"score": 0.06, "hit": True, "lane": "text",
             "signal": "fused"}]
    report = calibration.calibrate(rows)
    assert report["reliability"]["samples"] == 0, (
        "a fused rank is not on a probability scale; pooling it would "
        "describe nothing")
    assert report["reliability"]["uncalibratable"], (
        "the sample must be reported as uncalibratable, not dropped")


def test_the_threshold_keeps_the_right_answers_and_refuses_the_wrong_ones():
    threshold = calibration.abstention_threshold(_samples())
    assert threshold["cutoff"] is not None
    assert threshold["balanced_accuracy"] > 0.8
    assert threshold["present_retained_rate"] > 0.8, (
        "a threshold that throws away the hits is not a threshold")
    assert threshold["absent_abstained_rate"] > 0.8, (
        "a threshold that keeps the misses is not a threshold")


def test_one_class_of_labels_has_no_threshold_to_find():
    rows = [{"score": 0.9, "hit": True, "lane": "text",
             "signal": "dense"} for _ in range(5)]
    threshold = calibration.abstention_threshold(rows)
    assert threshold["cutoff"] is None
    assert "one class only" in threshold["status"]


def test_the_lanes_are_calibrated_separately():
    rows = [{"score": 0.9, "hit": True, "lane": "text",
             "signal": "dense"} for _ in range(5)]
    rows += [{"score": 0.2, "hit": False, "lane": "text",
              "signal": "dense"} for _ in range(5)]
    rows += [{"score": 0.9, "hit": False, "lane": "visual",
              "signal": "frame_cosine"} for _ in range(5)]
    rows += [{"score": 0.2, "hit": True, "lane": "visual",
              "signal": "frame_cosine"} for _ in range(5)]
    lanes = calibration.by_lane(rows)
    assert set(lanes) == {"text", "visual"}
    assert lanes["text"]["verdict"] != lanes["visual"]["verdict"], (
        "the text lane's cosine and the frame lane's cosine are different "
        "spaces; pooling them would describe neither")


# ─── VI-008: the top candidate is re-read, or said to be unread ────


def test_the_top_candidates_are_re_read_and_the_rest_are_reported():
    hits = [{"segment_id": f"s{index}", "clip_id": "clip_001",
             "start": float(index), "end": float(index) + 1,
             "score": 0.9 - index * 0.1} for index in range(5)]
    report = verify.verify_candidates(
        "a dog", hits,
        verify=lambda hit: {"status": verify.CONFIRMED, "verification_score": 0.8},
        top_k=2)
    assert report["verified_count"] == 2
    assert report["skipped_count"] == 3
    assert report["confirmed"] == 2
    assert report["skipped"][0]["reason"], (
        "a candidate that was not verified must say so, not vanish")


def test_a_window_that_cannot_be_re_read_is_unverifiable_not_confirmed():
    hits = [{"segment_id": "s0", "clip_id": "clip_001", "start": 0.0,
             "end": 5.0, "score": 0.7}]

    def refuse(hit):
        return {"status": verify.UNVERIFIABLE,
                "reason": "no media file is known for clip_001"}

    report = verify.verify_candidates("a dog", hits, verify=refuse)
    assert report["unverifiable"] == 1
    assert report["confirmed"] == 0
    assert report["verified"][0]["reason"]


def test_a_verifier_that_raises_is_reported_and_does_not_stop_the_run():
    hits = [{"segment_id": "s0", "clip_id": "clip_001", "start": 0.0,
             "end": 5.0, "score": 0.7},
            {"segment_id": "s1", "clip_id": "clip_001", "start": 6.0,
             "end": 9.0, "score": 0.6}]

    def explode(hit):
        raise RuntimeError("the decoder fell over")

    report = verify.verify_candidates("a dog", hits, verify=explode)
    assert report["unverifiable"] == 2
    assert "decoder fell over" in report["verified"][0]["reason"]


def test_the_verification_reports_two_scores_and_not_a_probability():
    hits = [{"segment_id": "s0", "clip_id": "clip_001", "start": 0.0,
             "end": 5.0, "score": 0.7}]
    report = verify.verify_candidates(
        "a dog", hits,
        verify=lambda hit: {"status": verify.CONFIRMED,
                            "verification_score": 0.68,
                            "index_score": 0.7,
                            "delta": -0.02,
                            "frames_examined": 5,
                            "stride_seconds": 1.0})
    row = report["verified"][0]
    assert row["verification_score"] == 0.68
    assert row["index_score"] == 0.7
    assert row["frames_examined"] == 5
    assert "probability" not in row, (
        "nothing measured the chance that the window is right")


# ─── VI-009: a change is measured against what it replaces ─────────


def _result(rows):
    return {"results": rows}


def _row(identifier, recall, wrong=0.0, missing=0.0):
    return {
        "id": identifier, "family": "editorial",
        "recall": recall, "precision": recall,
        "consequence": {"wrong_seconds_selected": wrong,
                        "missing_seconds": missing},
    }


def test_a_candidate_that_finds_more_and_wrongs_less_is_better():
    before = _result([_row("q1", 0.5, wrong=4.0)])
    after = _result([_row("q1", 1.0, wrong=0.0)])
    report = incumbent.compare(before, after)
    assert report["verdict"] == incumbent.CANDIDATE_BETTER
    assert report["adoption"] == incumbent.ADOPT
    assert report["macro"]["recall_delta"] == pytest.approx(0.5)


def test_a_candidate_that_finds_more_but_wrongs_more_is_not_decided():
    before = _result([_row("q1", 0.5, wrong=0.0)])
    after = _result([_row("q1", 1.0, wrong=9.0)])
    report = incumbent.compare(before, after)
    assert report["verdict"] == incumbent.NOT_DECIDED, (
        "trading a missed moment for a wrong window is the edit policy's "
        "trade to make, not the harness's")
    assert report["adoption"] == incumbent.NOT_DECIDED


def test_a_candidate_that_ties_is_not_adopted():
    before = _result([_row("q1", 0.5, wrong=1.0)])
    after = _result([_row("q1", 0.5, wrong=1.0)])
    report = incumbent.compare(before, after)
    assert report["verdict"] == incumbent.INDISTINGUISHABLE
    assert report["adoption"] == incumbent.RETAIN, (
        "newer is not a measurement; a tie is not a reason to change")


def test_a_candidate_that_finds_less_is_worse():
    before = _result([_row("q1", 1.0, wrong=0.0)])
    after = _result([_row("q1", 0.0, wrong=2.0)])
    report = incumbent.compare(before, after)
    assert report["verdict"] == incumbent.INCUMBENT_BETTER
    assert report["adoption"] == incumbent.RETAIN


def test_queries_only_one_result_answered_are_reported_as_unmatched():
    before = _result([_row("q1", 1.0), _row("q2", 1.0)])
    after = _result([_row("q1", 1.0)])
    report = incumbent.compare(before, after)
    assert report["queries_compared"] == 1
    assert report["queries_unmatched"] == 1
    assert report["unmatched_query_ids"] == ["q2"]


def test_results_with_no_query_in_common_have_no_verdict():
    before = _result([_row("q1", 1.0)])
    after = _result([_row("q9", 1.0)])
    report = incumbent.compare(before, after)
    assert report["verdict"] == incumbent.NO_VERDICT
    assert report["queries_compared"] == 0
