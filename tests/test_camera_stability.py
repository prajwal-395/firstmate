"""The residual is read from the key step 1.04 writes, and the two
stability signals say when they disagree.

`compute_deterministic_assessment` indexed
``temporal_index["camera_motion"]["residual"]``. Step 1.04 writes
``camera_motion_decomposition``, with the residual per sample inside
``values``. On project 001 ``ti.get("camera_motion")`` was ``None`` on
17 of 17 clips, so the optical-flow residual had never been read on any
clip of any run, every label came from frame differencing, and the label
called the steadiest clip in the edit ``unstable``.

The test that covered this passed, because its fixture was written in
the same wrong shape as the reader.
"""

import json
from pathlib import Path

import pytest

from library.tools.camera_stability import (
    HANDHELD_BELOW,
    MIN_RESIDUAL_SAMPLES,
    STABLE_BELOW,
    compare_stability_signals,
    read_camera_stability,
    residual_samples,
)
from library.tools.context_views import build_view


def _index(residuals):
    return {"camera_motion_decomposition": {
        "sample_rate_hz": 5,
        "values": [{"translation_x": 0.0, "translation_y": 0.0,
                    "zoom_factor": 1.0, "residual": r} for r in residuals],
    }}


def test_the_residual_is_read_under_the_key_step_1_04_writes():
    index = _index([0.01] * 20)
    assert len(residual_samples(index)) == 20
    label, method, mean = read_camera_stability(index)
    assert method == "optical_flow_residual"
    assert label == "stable"
    assert mean == pytest.approx(0.01)


def test_the_key_nobody_writes_measures_nothing():
    """The shape the reader used to expect answers with no measurement."""
    label, method, _ = read_camera_stability(
        {"camera_motion": {"residual": [0.01] * 20}})
    assert (label, method) == ("unknown", "unmeasured")


def test_the_tiers_are_the_search_grid():
    """Half a grid step and one grid step of mean global displacement."""
    assert read_camera_stability(_index([STABLE_BELOW - 0.001] * 20))[0] == "stable"
    assert read_camera_stability(_index([STABLE_BELOW] * 20))[0] == "handheld"
    assert read_camera_stability(_index([HANDHELD_BELOW - 0.001] * 20))[0] == "handheld"
    assert read_camera_stability(_index([HANDHELD_BELOW] * 20))[0] == "unstable"


def test_too_few_samples_is_not_a_measurement():
    label, method, mean = read_camera_stability(
        _index([0.01] * MIN_RESIDUAL_SAMPLES))
    assert (label, method, mean) == ("unknown", "unmeasured", None)


def test_the_fallback_answers_and_says_it_is_the_fallback():
    label, method, mean = read_camera_stability(
        {"motion_energy": {"values": [0.02] * 40}})
    assert (label, method, mean) == ("stable", "motion_energy_std", None)


def test_nothing_measured_answers_unknown():
    assert read_camera_stability({}) == ("unknown", "unmeasured", None)
    assert read_camera_stability(None) == ("unknown", "unmeasured", None)


# ── The disagreement is DATA ────────────────────────────────────────────

@pytest.mark.parametrize("deterministic,words,expected", [
    ("stable", ["stable"], "agree"),
    ("unstable", ["shaky"], "agree"),
    ("unstable", ["stable"], "disagree"),
    ("stable", ["shaky"], "disagree"),
    ("handheld", ["stable"], "partial"),
    ("unstable", ["stable", "shaky"], "partial"),
    ("unknown", ["stable"], "one_sided"),
    ("stable", [], "one_sided"),
    ("stable", ["gliding"], "unrecognised"),
])
def test_compare_stability_signals(deterministic, words, expected):
    assert compare_stability_signals(deterministic, words) == expected


def test_the_view_carries_both_signals_a_legend_and_the_disagreement():
    view = build_view("stability", {"semantic_analysis_documents": [
        {"clip_id": "clip_017",
         "assessment": {"camera_stability": "unstable",
                        "camera_stability_method": "optical_flow_residual"},
         "camera": [{"stability": "stable"}]},
        {"clip_id": "clip_002",
         "assessment": {"camera_stability": "unstable",
                        "camera_stability_method": "optical_flow_residual"},
         "camera": [{"stability": "shaky"}]},
    ]})["stability"]

    assert set(view["legend"]) == {
        "deterministic_stability", "deterministic_method",
        "vlm_stability", "signals_agree"}
    rows = {r["clip_id"]: r for r in view["clips"]}
    assert rows["clip_017"]["signals_agree"] == "disagree"
    assert rows["clip_017"]["vlm_stability"] == "stable"
    assert rows["clip_017"]["deterministic_method"] == "optical_flow_residual"
    assert rows["clip_002"]["signals_agree"] == "agree"
    assert "1 of 2" in view["disagreements"]
    assert "clip_017" in view["disagreements"]


def test_a_document_with_no_method_says_unrecorded_not_unmeasured():
    """A label whose signal was not recorded is not a label of nothing."""
    view = build_view("stability", {"semantic_analysis_documents": [
        {"clip_id": "clip_001",
         "assessment": {"camera_stability": "handheld"},
         "camera": [{"stability": "stable"}]},
    ]})["stability"]
    assert view["clips"][0]["deterministic_method"] == "unrecorded"


def test_the_view_leaves_out_a_clip_neither_signal_measured():
    assert build_view("stability", {"semantic_analysis_documents": [
        {"clip_id": "clip_001", "assessment": {"camera_stability": "unknown"},
         "camera": []}]}) == {}


def test_the_three_steps_that_decide_from_stability_declare_the_view():
    """A view is not routing: the step declares the view AND its source."""
    root = Path(__file__).resolve().parents[1] / "library" / "steps"
    for step in ("step_3_02_select_broll", "step_4_02_plan_transitions",
                 "step_4_03_plan_vfx"):
        manifest = json.loads((root / step / "manifest.json").read_text())
        assert "view:stability" in manifest["context_fields"], step
        declared = {i["name"] for i in manifest["interface"]["inputs"]}
        assert declared & {"semantic_analysis_documents",
                           "semantic_analysis"}, step
