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








# ── The disagreement is DATA ────────────────────────────────────────────



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




