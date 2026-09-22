"""Two animations that cannot share one tight box get a row each.

The captain, on Reel 26, on the one full-canvas file two animations had
been rendered into, 2026-09-11::

    "this was a full frame compostie render of two different animations,
     see if you can make it so that its two different tighbox animations
     that are layered on seperate rows on the timeline"

He is describing `mg_tight_box`'s own refusal from the outside. Reel 26's
segment carries a `title_lockup` at `top_centre` and a `subject_emblem`
at `middle_right`; their spans touch, so `plan_segments` clustered them
into one segment, and a middle zone beside another zone is exactly the
case a tight box cannot bound - `top: 50%` centres on the CANVAS, so on
a small canvas the middle stack centres on the wrong frame. One segment,
one full 1080x1920 render, two million pixels a frame for two small
graphics.

Nothing was baked. They are separate plan entries with their own
anchors, timings and copy, and the only thing joining them was the
cluster - so the cluster splits, each half is its own tight box, and
the two overlap in time, which makes them two rows.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import pytest

from library.tools import motion_graphics_plan as mgp
from library.tools.mg_tight_box import (
    separable_groups,
    tighten_motion_graphics_props,
)
from library.tools.timeline_layout import (
    EXPLAINER,
    SEMANTIC,
    TrackPlan,
    plan_layout,
)

FPS = 24000 / 1001
SAFE = {"top": 120, "right": 120, "bottom": 320, "left": 90}


def _moment(element, anchor, start, frames, text="WORDS", row=0):
    return {"element": element, "anchor": anchor, "row": row,
            "runs": [{"text": text, "type_role": "display"}],
            "startFrame": start, "durationFrames": frames,
            "footprint": None, "emphasis": None, "data": {}}


def _props(elements):
    return {"elements": elements, "fps": FPS, "width": 1080,
            "height": 1920, "safeArea": SAFE,
            "durationInFrames": 205}


# Reel 26's own two, at its own frames.
REEL_26 = [
    _moment("title_lockup", "top_centre", 0, 144,
            text="PICTURE ONE CUSTOMER"),
    _moment("subject_emblem", "middle_right", 109, 96, text="?"),
]


# ── The partition ──────────────────────────────────────────────────

def test_the_reel_26_pair_cannot_share_one_tight_box():
    """The premise, measured rather than recalled: together they force
    the full canvas."""
    assert tighten_motion_graphics_props(_props(REEL_26)) is None


def test_split_apart_each_half_is_a_tight_box():
    groups = separable_groups(REEL_26)
    assert [[e["element"] for e in g] for g in groups] == [
        ["title_lockup"], ["subject_emblem"]]
    boxes = [tighten_motion_graphics_props(_props(g)) for g in groups]
    assert all(box is not None for box in boxes)
    # Both are a fraction of the frame they used to cost.
    for box in boxes:
        assert box.width * box.height < 0.3 * 1080 * 1920
        assert box.placement["scaling"] == 1


def test_a_segment_already_in_one_zone_family_is_not_split():
    """A split that buys nothing is two rows for no reason. Top and
    bottom share canvas edges, so they bound exactly together.

    Side-anchored on both edges, so no layout-width floor applies
    and the pair stays tight. With a CENTRE anchor in the mix the
    floored canvas covers the frame and the pair renders full canvas
    instead - which is the fix, not a regression: the old narrow
    canvas rewrapped the centre copy (see
    test_centre_tall_mix_falls_back_to_full_canvas in
    test_mg_tight_layout_width.py).
    """
    together = [_moment("title_lockup", "top_left", 0, 60),
                _moment("lower_third", "bottom_left", 10, 60)]
    assert separable_groups(together) == [together]
    assert tighten_motion_graphics_props(_props(together)) is not None


def test_one_element_is_never_split():
    one = [_moment("subject_emblem", "middle_right", 0, 60, text="?")]
    assert separable_groups(one) == [one]
    assert separable_groups([]) == []


# ── The lanes ──────────────────────────────────────────────────────

def test_the_split_segments_land_on_two_lanes():
    segments = mgp.plan_segments(REEL_26, fps=FPS, width=1080, height=1920,
                                 safe_area=SAFE)
    assert [s["elements"] for s in segments] == [
        ["title_lockup"], ["subject_emblem"]]
    assert [s["lane"] for s in segments] == [0, 1]
    # Each segment is its own span, rebased to its own start.
    assert segments[0]["total_frames"] == 144
    assert segments[1]["total_frames"] == 96
    assert segments[0]["props"]["elements"][0]["startFrame"] == 0
    assert segments[1]["props"]["elements"][0]["startFrame"] == 0
    # And they overlap, which is the whole point.
    assert segments[1]["timeline_start"] < segments[0]["timeline_end"]


def test_a_full_canvas_project_is_not_split(tmp_path):
    """Splitting only WINS where the render would be tight. Under a
    full-canvas declaration two groups are two full-frame renders on two
    rows for no gain, so the declaration is read rather than assumed."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "project.yaml").write_text(
        "name: p\nslug: p\npipeline:\n"
        "  motion_graphics_overlay_geometry: full\n", encoding="utf-8")
    segments = mgp.plan_segments(REEL_26, fps=FPS, width=1080, height=1920,
                                 safe_area=SAFE,
                                 project_folder=str(project))
    assert len(segments) == 1
    assert segments[0]["elements"] == ["subject_emblem", "title_lockup"]
    assert segments[0]["lane"] == 0


def test_segments_that_do_not_overlap_share_one_lane():
    """A lane is a row, and a row holds everything that fits on it."""
    apart = [
        _moment("title_lockup", "top_centre", 0, 40),
        _moment("subject_emblem", "middle_right", 200, 40, text="?"),
    ]
    segments = mgp.plan_segments(apart, fps=FPS, width=1080, height=1920,
                                 safe_area=SAFE)
    assert len(segments) == 2
    assert [s["lane"] for s in segments] == [0, 0]


# ── The rows ───────────────────────────────────────────────────────

def _reel_material(**kwargs):
    return {"angles": [{"key": "a", "label": "Akshita",
                        "speech_name": "Akshita CH1",
                        "program_channel": 1}],
            "has_broll": False, "has_frame": True,
            "caption_spans": [(0, 100)], "has_transitions": False,
            "has_explainer": False, "has_semantic": True,
            "mg_spans": [], "has_generators": False,
            "timed_text_spans": [], "music_spans": [], "sfx_spans": [],
            **kwargs}


def test_two_overlapping_semantic_segments_are_two_rows():
    plan = plan_layout(_reel_material(
        semantic_spans=[(234, 378), (343, 439)]))
    rows = plan.rows_for_role(SEMANTIC)
    assert [r.name for r in rows] == ["Semantic", "Semantic 2"]
    assert [r.index for r in rows] == sorted(r.index for r in rows)
    # Contiguous with the caption row above them, no gap and no reuse.
    indices = [t.index for t in plan.video_tracks]
    assert indices == list(range(1, len(indices) + 1))


def test_semantic_segments_that_do_not_overlap_stay_one_row():
    plan = plan_layout(_reel_material(semantic_spans=[(0, 100), (200, 300)]))
    assert [r.name for r in plan.rows_for_role(SEMANTIC)] == ["Semantic"]


def test_the_bare_boolean_still_means_one_row():
    """Material written before either kind could layer keeps its row."""
    plan = plan_layout(_reel_material())
    assert [r.name for r in plan.rows_for_role(SEMANTIC)] == ["Semantic"]


def test_the_explainer_layers_on_the_same_terms():
    plan = plan_layout(_reel_material(
        has_semantic=False, has_explainer=True,
        explainer_spans=[(0, 100), (50, 150), (60, 200)]))
    assert [r.name for r in plan.rows_for_role(EXPLAINER)] == [
        "Explainer", "Explainer 2", "Explainer 3"]


def test_a_numbered_layer_row_is_still_a_layer_not_a_camera():
    """`reel_build._is_layer_row` tells decoration from cameras; a
    second semantic row must not become a reel angle."""
    from library.tools.reel_build import _is_layer_row

    assert _is_layer_row("Semantic")
    assert _is_layer_row("Semantic 2")
    assert _is_layer_row("Explainer 3")
    assert not _is_layer_row("Akshita")


def test_the_verifier_files_a_numbered_row_under_its_own_role():
    """Matching the bare name alone would file every further row as
    unclassified and report a correct build as carrying items nothing
    grades."""
    from library.tools.reel_conformance_verifier import _is_layer_named

    assert _is_layer_named("Semantic", "Semantic")
    assert _is_layer_named("Semantic 2", "Semantic")
    assert not _is_layer_named("Semantic Extra", "Semantic")
    assert not _is_layer_named("Subtitles", "Semantic")


# ── The manifest gate ──────────────────────────────────────────────

def test_the_overlap_gate_reads_lanes_not_the_whole_track():
    from library.tools.manifest_validator import (
        _check_overlay_segments_do_not_overlap,
    )

    layered = {"motion_graphics_overlay": {"segments": [
        {"timeline_start": 9.7, "timeline_end": 15.8, "lane": 0},
        {"timeline_start": 14.3, "timeline_end": 18.3, "lane": 1},
    ]}}
    assert _check_overlay_segments_do_not_overlap(layered) == []

    stacked = {"motion_graphics_overlay": {"segments": [
        {"timeline_start": 9.7, "timeline_end": 15.8, "lane": 0},
        {"timeline_start": 14.3, "timeline_end": 18.3, "lane": 0},
    ]}}
    errors = _check_overlay_segments_do_not_overlap(stacked)
    assert len(errors) == 1
    assert "lane 0" in errors[0]


def test_a_segment_naming_no_lane_is_lane_zero():
    """Every manifest written before lanes existed."""
    from library.tools.manifest_validator import (
        _check_overlay_segments_do_not_overlap,
    )

    legacy = {"motion_graphics_overlay": {"segments": [
        {"timeline_start": 0.0, "timeline_end": 5.0},
        {"timeline_start": 4.0, "timeline_end": 8.0},
    ]}}
    assert len(_check_overlay_segments_do_not_overlap(legacy)) == 1


def test_a_lane_with_no_row_refuses_the_build():
    """A graphic stacked onto another lane's row hides one of two the
    plan puts on screen together, so the placer refuses instead."""
    from library.tools.reel_build import ReelBuildError, place_overlay_segments

    with pytest.raises(ReelBuildError) as raised:
        place_overlay_segments(
            None, None, None, "Reel 26", FPS,
            [{"overlay_path": "/tmp/a.mov", "timeline_start": 0.0,
              "timeline_end": 1.0, "total_frames": 24, "lane": 1}],
            [5], kind="semantic visual", check="F22")
    assert "lane 1" in str(raised.value)
    assert "1 row(s)" in str(raised.value)


def test_the_plan_row_list_is_what_the_placer_indexes():
    """A TrackPlan with two semantic rows hands the placer two indices,
    in row order."""
    plan: TrackPlan = plan_layout(_reel_material(
        semantic_spans=[(234, 378), (343, 439)]))
    rows = [row.index for row in plan.rows_for_role(SEMANTIC)]
    assert len(rows) == 2 and rows[1] == rows[0] + 1
