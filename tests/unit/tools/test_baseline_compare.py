"""The baseline gate compares honestly, or it refuses.

Every test here names the defect it catches. The comparator is the
thing that says a later change held the M04 baselines, so a comparison
that quietly passes drift teaches the lane that a regression is fine:
a drift that is REPORTED can be reverted, one that is defaulted away
cannot.
"""

import json

import pytest

from library.tools import baseline_compare


def _write(path, payload):
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def _baseline(tmp_path):
    path = tmp_path / "baselines.json"
    _write(path, {"baselines": [
        {"id": "m04-example", "title": "example",
         "source": {"pr": 1, "report": "r.md"},
         "metrics": [
             {"name": "recall", "kind": "quality", "direction": "higher",
              "baseline": 0.5, "unit": "fraction", "tolerance": 0.0},
             {"name": "seconds", "kind": "speed", "direction": "lower",
              "baseline": 100.0, "unit": "s", "tolerance": 0.25}]},
    ]})
    return str(path)


def test_matching_measurement_passes(tmp_path):
    """The defect: a gate that FAILS correct output is no coverage."""
    baseline = _baseline(tmp_path)
    bundle = _write(tmp_path / "m.json", {
        "m04-example": {"metrics": {"recall": 0.5, "seconds": 100.0},
                        "provenance": "test"}})
    assert baseline_compare.main(
        ["--baseline", baseline, "--measurement", bundle]) == 0


def test_quality_regression_fails(tmp_path, capsys):
    """The defect: a silent quality regression - the reverted kind."""
    baseline = _baseline(tmp_path)
    bundle = _write(tmp_path / "m.json", {
        "m04-example": {"metrics": {"recall": 0.49, "seconds": 100.0},
                        "provenance": "test"}})
    assert baseline_compare.main(
        ["--baseline", baseline, "--measurement", bundle]) == 2
    assert "REGRESSED" in capsys.readouterr().out


def test_speed_regression_inside_tolerance_passes_outside_fails(tmp_path):
    """The defect: machine noise read as a regression, or vice versa."""
    baseline = _baseline(tmp_path)
    noisy = _write(tmp_path / "n.json", {
        "m04-example": {"metrics": {"recall": 0.5, "seconds": 110.0},
                        "provenance": "test"}})
    assert baseline_compare.main(
        ["--baseline", baseline, "--measurement", noisy]) == 0
    slow = _write(tmp_path / "s.json", {
        "m04-example": {"metrics": {"recall": 0.5, "seconds": 200.0},
                        "provenance": "test"}})
    assert baseline_compare.main(
        ["--baseline", baseline, "--measurement", slow]) == 2


def test_missing_metric_is_unmeasured_not_defaulted(tmp_path, capsys):
    """The defect: a promised key read as a default that says it held."""
    baseline = _baseline(tmp_path)
    bundle = _write(tmp_path / "m.json", {
        "m04-example": {"metrics": {"recall": 0.5},
                        "provenance": "test"}})
    assert baseline_compare.main(
        ["--baseline", baseline, "--measurement", bundle]) == 2
    assert "UNMEASURED" in capsys.readouterr().out


def test_unknown_baseline_id_is_refused(tmp_path, capsys):
    """The defect: a typo that compares against nothing and passes."""
    baseline = _baseline(tmp_path)
    bundle = _write(tmp_path / "m.json", {
        "m04-typo": {"metrics": {}, "provenance": "test"}})
    assert baseline_compare.main(
        ["--baseline", baseline, "--measurement", bundle]) == 1
    assert "unknown baseline id" in capsys.readouterr().err


def test_targeted_extractor_reads_the_stored_shape():
    """The defect: an extractor that only works on the author's file."""
    artifact = {
        "routes": {
            "targeted": {"answered": "32/32", "correct": "32/32",
                         "context_bytes_per_call": 38415,
                         "o200k_tokens_per_call": 12294},
            "whole_view": {"context_bytes_per_call": 15533918},
        },
        "reel_plan": {"speaker_set_match": "37/37",
                      "speaker_share_within_0.10": "32/37"},
    }
    got = baseline_compare.extract_targeted_query(artifact)
    assert got["answer_rate"] == pytest.approx(1.0)
    assert got["context_bytes_per_call"] == pytest.approx(38415.0)
    assert got["reel_speaker_share_within_0.10"] == pytest.approx(32 / 37)


def test_visual_recall_extractor_refuses_the_wrong_set():
    """The defect: a merged-set run compared against the held-out floor."""
    artifact = {"eval_set": "editorial-retrieval-merged-2026-10-20",
                "summary": {}}
    with pytest.raises(baseline_compare.BaselineCompareError,
                       match="held-out"):
        baseline_compare.extract_visual_recall(artifact)


def test_visual_recall_extractor_reads_capped_and_fidelity():
    """The defect: the capped recall confused with its uncapped twin."""
    artifact = {
        "eval_set": "editorial-retrieval-held-out-2026-10-20",
        "summary": {
            "editorial": {
                "by_lane": {"visual": {"macro_recall": 0.0539,
                                       "macro_recall_uncredited": 0.1003,
                                       "macro_precision": 0.325}},
                "false_positive_consequence": {
                    "wrong_seconds_selected": 55.0,
                    "false_positive_seconds": 257.65},
            },
        },
    }
    got = baseline_compare.extract_visual_recall(artifact)
    assert got["visual_macro_recall_capped"] == pytest.approx(0.0539)
    assert got["visual_macro_recall_uncapped"] == pytest.approx(0.1003)
    assert got["false_positive_seconds"] == pytest.approx(257.65)


def test_progressive_extractor_takes_medians():
    """The defect: a mean where the report promised a median."""
    artifact = {"rows": [
        {"cold": {"before": {"seconds_full": 446.5},
                  "after": {"seconds_first": 62.0,
                            "seconds_full": 378.6}},
         "shared": {"answers_identical": True}},
        {"cold": {"before": {"seconds_full": 435.9},
                  "after": {"seconds_first": 25.4,
                            "seconds_full": 410.3}},
         "shared": {"answers_identical": True}},
        {"cold": {"before": {"seconds_full": 472.6},
                  "after": {"seconds_first": 19.1,
                            "seconds_full": 355.0}},
         "shared": {"answers_identical": True}},
    ]}
    got = baseline_compare.extract_progressive_retrieval(artifact)
    assert got["first_answer_s"] == pytest.approx(25.4)
    assert got["before_first_answer_s"] == pytest.approx(446.5)
    assert got["full_fidelity_s"] == pytest.approx(378.6)
    assert got["answers_identical_rate"] == pytest.approx(1.0)
