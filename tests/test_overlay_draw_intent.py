"""Both halves of "does it land where intended" are ARMED.

Finding 1: `draw_intent` was supplied by no production caller, so the
ink-against-intent check could not run - five reels shipped 17 motion
graphics stored at Tilt 5184 drawing entirely off the top of the frame
while every gate passed. Finding 2: `overlay_verify` had no production
caller at all - Reel 09's captions stored at -7680 were reported "held
exactly" by the read-back gate.

These tests prove the arming against the two MEASURED inputs, through
the production helpers (not hand-made dicts): the pixel half refuses
Tilt 5184, the values half refuses stored -7680 against computed -7929.
Companion no-false-alarm tests prove correct output still passes and
unverifiable segments ride exactly as before. Nothing here reaches
Resolve or a real project: fakes stand in for timeline items, `tmp_path`
for projects and renders.
"""

import json
import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools.overlay_draw_intent import (  # noqa: E402
    caption_draw_intent,
    draw_intent_for_segment,
    pin_draw_intent,
)
from library.tools.overlay_intent import matching_target  # noqa: E402
from library.tools.overlay_placement import (  # noqa: E402
    _intent_reason,
    place_overlay_segment,
)
from library.tools.overlay_verify import sweep_reel_overlays  # noqa: E402
from library.tools.tight_box import (  # noqa: E402
    canvas_screen_origin,
    constant_caption_box,
)

FRAME = (1080, 1920)

#: Reel 30's first motion graphic, measured 2026-09-11: a 724x480 canvas
#: stored at Tilt 5184 draws at frame rows -576..-96 - entirely off the
#: top - while reading back exactly what was set.
OFF_FRAME_CANVAS = (724, 480)
OFF_FRAME_TILT = 5184.0

#: Reel 09 captions: the computation asked for Tilt -7929 and Resolve
#: past its silent clamp held -7680, which the read-back gate reported
#: as "held exactly".
REEL09_COMPUTED = {"scaling": 1, "pan": 0.0, "tilt": -7929.0}
REEL09_CLAMPED = {"scaling": 1, "pan": 0.0, "tilt": -7680.0}
REEL09_CANVAS = (840, 480)


# ── Fakes: a Resolve timeline item and timeline ───────────────────────

class _Item:
    def __init__(self, start, held=None):
        self._start = start
        self._held = dict(held or {})

    def GetStart(self):
        return self._start

    def GetProperty(self, prop=None):
        if prop is None:
            return dict(self._held)
        return self._held.get(prop)

    def SetProperty(self, prop, value):
        self._held[prop] = value
        return True


class _Pool:
    def AppendToTimeline(self, specs):
        return ("placed",)


def _timeline_with(items_by_track, size=FRAME):
    """A fake timeline keyed by track index (GetStart is the record frame)."""

    class _T:
        def GetName(self):
            return "Reel"

        def GetSetting(self, name):
            return str(size[0] if name == "timelineResolutionWidth"
                       else size[1])

        def GetItemListInTrack(self, kind, index):
            assert kind == "video"
            return items_by_track.get(index, [])

    return _T()


def _project_with_current_timeline(timeline):
    class _P:
        def GetCurrentTimeline(self):
            return timeline

    return _P()


# ── Finding 1, proved: Tilt 5184 is REFUSED by the pixel half ─────────

def test_pin_path_refuses_the_off_frame_tilt():
    """The production pin helper against the measured Reel 30 input.

    The graphic is pinned where the captain put such graphics (frame
    centre, upper band); the stored Tilt 5184 draws its canvas at rows
    -576..-96. The helper must refuse it - a test that passes this
    input has armed nothing.
    """
    # History gain: this still measured rows -576..-96 on 2026-09-11
    # (see HISTORY_GAIN in test_tight_box.py).
    ox, oy = canvas_screen_origin(
        *OFF_FRAME_CANVAS,
        {"scaling": 1, "pan": 0.0, "tilt": OFF_FRAME_TILT}, *FRAME,
        draw_gain=1.0)
    assert (oy, oy + OFF_FRAME_CANVAS[1]) == pytest.approx((-576.0, -96.0),
                                                          abs=1.0)
    segment = {"tight_box": {"width": OFF_FRAME_CANVAS[0],
                             "height": OFF_FRAME_CANVAS[1],
                             "placement": {"scaling": 1, "pan": 0.0,
                                           "tilt": OFF_FRAME_TILT}}}
    intent = {"mg_reel30_first": {"canvas_centre": [540.0, 312.0],
                                  "scaling": 1}}
    draw_intent = draw_intent_for_segment(
        segment, kind="motion graphic",
        segment_id="mg_reel30_first", intent=intent, frame_wh=FRAME)
    assert draw_intent is not None, (
        "a pinned graphic must produce an intent, or the check is still "
        "a check-shaped parameter")
    reason = _intent_reason(
        draw_intent, {"scaling": 1, "pan": 0.0, "tilt": OFF_FRAME_TILT},
        {"Pan": 0.0, "Tilt": OFF_FRAME_TILT})
    assert reason, "Tilt 5184 on a 480 canvas must not pass this intent"
    assert "ENTIRELY OUTSIDE THE FRAME" in reason








# ── The caption row path ──────────────────────────────────────────────

def _project_with_caption_row(tmp_path, row=None):
    project = tmp_path / "proj"
    project.mkdir()
    body = "name: t\n"
    if row is not None:
        body += ("pipeline:\n  subtitle_position:\n"
                 f"    caption_row: {row}\n    reason: test\n")
    (project / "project.yaml").write_text(body, encoding="utf-8")
    return str(project)


def _caption_segment(tmp_path, canvas=(904, 480), tilt=-1744.0,
                     position="bottom"):
    render_dir = tmp_path / "renders"
    render_dir.mkdir(exist_ok=True)
    overlay_path = str(render_dir / "sub_test.mov")
    open(overlay_path, "wb").close()
    props_path = str(render_dir / "sub_test_props.json")
    with open(props_path, "w", encoding="utf-8") as handle:
        json.dump({"style": {"position": position}}, handle)
    return {"overlay_path": overlay_path,
            "segment_id": "sub_test",
            "tight_box": {"width": canvas[0], "height": canvas[1],
                          "placement": {"scaling": 1, "pan": 0.0,
                                        "tilt": tilt}}}




def test_caption_row_path_refuses_a_stale_sidecar_placement(tmp_path):
    """Reel 28's shape: 18 tight captions placed under a superseded row
    draw ~220px below the declared one. The row path refuses them."""
    from library.tools.safe_area import resolve_safe_area

    project = _project_with_caption_row(tmp_path)
    insets = resolve_safe_area(project, width=FRAME[0], height=FRAME[1])
    intended = constant_caption_box({
        "width": FRAME[0], "height": FRAME[1],
        "style": {"safeArea": {**insets.as_props(),
                               "bottom": insets.bottom + 1},
                  "captionMaxWidth": insets.centered_usable_width,
                  "position": "bottom"},
        "subtitles": [{"text": ""}]})
    segment = _caption_segment(tmp_path,
                               canvas=(intended.width, intended.height))
    draw_intent = caption_draw_intent(
        segment, frame_wh=FRAME, project_folder=project)
    assert draw_intent is not None
    stale_tilt = intended.placement["tilt"] - 220.0 * (FRAME[1] / 480.0)
    reason = _intent_reason(
        draw_intent, {"scaling": 1, "pan": 0.0, "tilt": stale_tilt},
        {"Pan": 0.0, "Tilt": stale_tilt})
    assert reason, "a placement 220px off the declared row must be refused"
    assert "off by" in reason








# ── matching_target: the quiet lookup the pin path reads ─────────────

def test_matching_target_exact_prefix_and_kind():
    intent = {"sub_x": {"canvas_centre": [1.0, 2.0], "scaling": 1},
              "caption": {"canvas_centre": [3.0, 4.0], "scaling": 1}}
    key, target = matching_target(intent, "caption", "sub_x")
    assert key == "sub_x" and target["canvas_centre"] == [1.0, 2.0]
    key, target = matching_target(intent, "caption", "sub_other")
    assert key == "caption", "the kind default is the fallback"
    assert matching_target(intent, "mg", "zzz") == (None, None)
    assert matching_target({}, "caption", "sub_x") == (None, None)
    assert pin_draw_intent((100, 100), "caption", "sub_x", intent,
                           frame_wh=FRAME) is not None
    assert pin_draw_intent((100, 100), "mg", "zzz", intent,
                           frame_wh=FRAME) is None








# ── Finding 2, proved: stored -7680 is REFUSED by the values half ─────

def _sweep_entry(label, stored, track=3, frame=10, canvas=REEL09_CANVAS,
                 computed=None, placement_label=None):
    return {"label": label, "kind": "caption", "segment_id": label,
            "placement_label": placement_label,
            "track_index": track, "record_frame": frame,
            "canvas_wh": canvas,
            "placement": dict(computed or REEL09_COMPUTED),
            "overlay_path": "", "frames_dir": ""}


def test_sweep_honours_a_label_pin():
    """The sweep resolves the label tier too: a store at the label
    pin's transform passes, while the same store judged against the
    computation alone would cry foul on the captain's correction."""
    from library.tools.overlay_intent import resolve as resolve_intent

    intent = {"vox_r1_00": {"canvas_centre": [540.0, 312.0], "scaling": 1}}
    canvas = (724, 480)
    computed = {"scaling": 1, "pan": 0.0, "tilt": 100.0}
    expected, provenance = resolve_intent(
        "explainer", "mg_proj_newdigest", computed, intent,
        canvas=canvas, frame=FRAME, placement_label="vox_r1_00")
    assert provenance == "declared"
    entry = _sweep_entry("vox_r1_00", expected, track=5, frame=30,
                         canvas=canvas, computed=computed,
                         placement_label="vox_r1_00")
    entry["kind"] = "explainer"
    entry["segment_id"] = "mg_proj_newdigest"
    timeline = _timeline_with({5: [_Item(30, {"Scaling": 1,
                                              "Pan": expected["pan"],
                                              "Tilt": expected["tilt"]})]})
    report = sweep_reel_overlays(timeline, [entry], intent=intent,
                                 full_wh=FRAME,
                                 resolve_project=_project_with_current_timeline(
                                     timeline))
    assert report["passed"] and report["values"]["checked"] == 1, (
        "a label-pinned store must pass the sweep, not foul it")


def test_sweep_refuses_the_reel09_clamp():
    """The production sweep against the measured Reel 09 input: stored
    -7680 where the computation asked -7929. A sweep that passes this
    has armed nothing."""
    timeline = _timeline_with({3: [_Item(10, {"Scaling": 1, "Pan": 0.0,
                                              "Tilt": -7680.0})]})
    report = sweep_reel_overlays(
        timeline, [_sweep_entry("sub_r09_x", REEL09_CLAMPED)], intent={},
        full_wh=FRAME,
        resolve_project=_project_with_current_timeline(timeline))
    assert not report["passed"]
    assert not report["values"]["passed"]
    finding = report["values"]["findings"][0]
    assert finding["stored"]["tilt"] == -7680.0
    assert finding["expected"]["tilt"] == -7929.0


def test_overlay_sweep_restores_cross_current_pan_and_tilt_units():
    """Intent checks use the same target-timeline units as picture checks."""
    expected = {"scaling": 1.0, "pan": 23.0, "tilt": -400.0}
    timeline = _timeline_with({
        3: [_Item(10, {"Scaling": 1.0,
                       "Pan": expected["pan"] * 3840 / FRAME[0],
                       "Tilt": expected["tilt"] * 2160 / FRAME[1]})]})
    current = _timeline_with({}, size=(3840, 2160))
    report = sweep_reel_overlays(
        timeline, [_sweep_entry("overlay_units", expected,
                                computed=expected)],
        intent={}, full_wh=FRAME,
        resolve_project=_project_with_current_timeline(current))

    assert report["passed"]
    assert report["values"]["checked"] == 1
    assert report["values"]["findings"] == []










# ── The placer evaluates what the callers now supply ──────────────────

def test_placer_reports_a_stale_value_through_draw_intent():
    """Before this change the placer could not see this class at all:
    the value reads back exactly what was set."""
    draw_intent = {"canvas": OFF_FRAME_CANVAS, "frame": FRAME,
                   "ink_in_canvas": (0.0, 40.0, 724.0, 440.0),
                   "intent_box": (272.0, 1415.0, 800.0, 1572.0)}
    item = _Item(10)
    ok, note = place_overlay_segment(
        _Pool(), _timeline_with({3: [item]}), object(),
        track_index=3, record_frame=10,
        source_in_frame=0, source_out_frame=40,
        placement={"scaling": 1, "pan": 0.0, "tilt": OFF_FRAME_TILT},
        label="mg_stale", draw_intent=draw_intent)
    assert ok, "a misplaced overlay is REPORTED, never failed"
    assert "draws at" in note and "mg_stale" in note


def test_placer_stays_quiet_without_draw_intent():
    item = _Item(10)
    ok, note = place_overlay_segment(
        _Pool(), _timeline_with({3: [item]}), object(),
        track_index=3, record_frame=10,
        source_in_frame=0, source_out_frame=40,
        placement={"scaling": 1, "pan": 0.0, "tilt": OFF_FRAME_TILT},
        label="mg_stale")
    assert ok and note == "", (
        "no intent supplied behaves exactly as before")


def test_placer_normalizes_cross_current_transform_readback():
    class _ScaledItem(_Item):
        def GetProperty(self, prop=None):
            values = dict(self._held)
            values["Pan"] *= 3840 / FRAME[0]
            values["Tilt"] *= 2160 / FRAME[1]
            return values if prop is None else values.get(prop)

    item = _ScaledItem(10)
    timeline = _timeline_with({3: [item]})
    current = _timeline_with({}, size=(3840, 2160))
    ok, note = place_overlay_segment(
        _Pool(), timeline, object(), track_index=3, record_frame=10,
        source_in_frame=0, source_out_frame=40,
        placement={"scaling": 1, "pan": 23.0, "tilt": -400.0},
        label="overlay_units",
        resolve_project=_project_with_current_timeline(current))

    assert ok and note == ""


# ── The wiring itself, pinned so it cannot drift back to zero ─────────

def _library_source(relative):
    path = os.path.join(PROJECT_ROOT, *relative.split("/"))
    with open(path, encoding="utf-8") as handle:
        return handle.read()



