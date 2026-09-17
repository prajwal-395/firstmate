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

    def GetProperty(self, prop):
        return self._held.get(prop)

    def SetProperty(self, prop, value):
        self._held[prop] = value
        return True


class _Pool:
    def AppendToTimeline(self, specs):
        return ("placed",)


def _timeline_with(items_by_track):
    """A fake timeline keyed by track index (GetStart is the record frame)."""

    class _T:
        def GetItemListInTrack(self, kind, index):
            assert kind == "video"
            return items_by_track.get(index, [])

    return _T()


# ── Finding 1, proved: Tilt 5184 is REFUSED by the pixel half ─────────

def test_pin_path_refuses_the_off_frame_tilt():
    """The production pin helper against the measured Reel 30 input.

    The graphic is pinned where the captain put such graphics (frame
    centre, upper band); the stored Tilt 5184 draws its canvas at rows
    -576..-96. The helper must refuse it - a test that passes this
    input has armed nothing.
    """
    ox, oy = canvas_screen_origin(
        *OFF_FRAME_CANVAS,
        {"scaling": 1, "pan": 0.0, "tilt": OFF_FRAME_TILT}, *FRAME)
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


def test_pin_path_accepts_the_same_graphic_placed_right():
    """No false alarm: the pin's own transform passes the pin's intent."""
    from library.tools.overlay_intent import resolve as resolve_intent

    segment = {"tight_box": {"width": OFF_FRAME_CANVAS[0],
                             "height": OFF_FRAME_CANVAS[1],
                             "placement": {"scaling": 1, "pan": 0.0,
                                           "tilt": OFF_FRAME_TILT}}}
    intent = {"mg_reel30_first": {"canvas_centre": [540.0, 312.0],
                                  "scaling": 1}}
    draw_intent = draw_intent_for_segment(
        segment, kind="motion graphic",
        segment_id="mg_reel30_first", intent=intent, frame_wh=FRAME)
    placement, provenance = resolve_intent(
        "motion graphic", "mg_reel30_first",
        {"scaling": 1, "pan": 0.0, "tilt": OFF_FRAME_TILT}, intent,
        canvas=OFF_FRAME_CANVAS, frame=FRAME)
    assert provenance == "declared"
    reason = _intent_reason(
        draw_intent, placement,
        {"Pan": placement["pan"], "Tilt": placement["tilt"]})
    assert reason == "", f"a correct pin placement must pass, got: {reason}"


def test_pin_wins_over_the_caption_row():
    """A pinned caption is judged by the pin, never by the row.

    Reel 09 pinned all 22 captions at one centre; the row later moved.
    Judging the pin by the row would false-alarm on exactly the
    corrections pins exist to keep.
    """
    segment = {"tight_box": {"width": 904, "height": 480,
                             "placement": {"scaling": 1, "pan": 0.0,
                                           "tilt": -1700.0}}}
    intent = {"caption": {"canvas_centre": [540.0, 1395.0], "scaling": 1}}
    draw_intent = draw_intent_for_segment(
        segment, kind="caption", segment_id="sub_x", intent=intent,
        frame_wh=FRAME)
    assert draw_intent is not None
    box = draw_intent["intent_box"]
    assert ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0) == pytest.approx(
        (540.0, 1395.0))


def test_unpinned_motion_graphics_ride_as_before():
    """No per-graphic declaration exists at placement time for these,
    so the helper says nothing rather than guessing - a wrong intent
    box false-alarms on correct output, which is worse than no check.
    """
    segment = {"tight_box": {"width": 724, "height": 480,
                             "placement": {"scaling": 1, "pan": 0.0,
                                           "tilt": 100.0}}}
    assert draw_intent_for_segment(
        segment, kind="explainer", segment_id="vox_r1_0", intent={},
        frame_wh=FRAME) is None
    assert draw_intent_for_segment(
        segment, kind="semantic visual", segment_id="sem_r1_0",
        intent=None, frame_wh=FRAME) is None


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


def test_caption_row_path_passes_a_correct_caption(tmp_path):
    """The row re-derived from today's declarations accepts the placement
    the same arithmetic computed - no false alarm on correct output."""
    project = _project_with_caption_row(tmp_path)
    segment = _caption_segment(tmp_path)
    draw_intent = caption_draw_intent(
        segment, frame_wh=FRAME, project_folder=project)
    assert draw_intent is not None, (
        "a structural caption with render props must verify")
    # The placement the declarations imply passes: no false alarm on
    # correct output. (The record's own tilt is illustrative only.)
    intended = constant_caption_box({
        "width": FRAME[0], "height": FRAME[1],
        "style": {"safeArea": {"top": 120, "right": 120,
                               "bottom": 321, "left": 90},
                  "captionMaxWidth": 840, "position": "bottom"},
        "subtitles": [{"text": ""}]})
    assert (intended.width, intended.height) == (904, 480)
    intended_reason = _intent_reason(
        draw_intent, dict(intended.placement),
        {"Pan": intended.placement["pan"],
         "Tilt": intended.placement["tilt"]})
    assert intended_reason == "", \
        f"correct row placement refused: {intended_reason}"


def test_caption_row_path_refuses_a_stale_sidecar_placement(tmp_path):
    """Reel 28's shape: 18 tight captions placed under a superseded row
    draw ~220px below the declared one. The row path refuses them."""
    project = _project_with_caption_row(tmp_path)
    segment = _caption_segment(tmp_path)
    draw_intent = caption_draw_intent(
        segment, frame_wh=FRAME, project_folder=project)
    assert draw_intent is not None
    intended = constant_caption_box({
        "width": FRAME[0], "height": FRAME[1],
        "style": {"safeArea": {"top": 120, "right": 120,
                               "bottom": 321, "left": 90},
                  "captionMaxWidth": 840, "position": "bottom"},
        "subtitles": [{"text": ""}]})
    stale_tilt = intended.placement["tilt"] - 220.0 * (FRAME[1] / 480.0)
    reason = _intent_reason(
        draw_intent, {"scaling": 1, "pan": 0.0, "tilt": stale_tilt},
        {"Pan": 0.0, "Tilt": stale_tilt})
    assert reason, "a placement 220px off the declared row must be refused"
    assert "off by" in reason


def test_caption_row_path_follows_a_declared_row(tmp_path):
    """The reel override moves the intent: the check judges against
    today's declaration, never a hardcoded row."""
    import json as _json

    project = _project_with_caption_row(tmp_path)
    external = os.path.join(project, "external")
    os.makedirs(external, exist_ok=True)
    with open(os.path.join(external, "reel_caption_row.json"), "w",
              encoding="utf-8") as handle:
        _json.dump({"version": 1,
                    "rows": [{"reel": "reel-a", "caption_row": 0.72,
                              "reason": "test"}]},
                   handle)
    segment = _caption_segment(tmp_path)
    plain = caption_draw_intent(segment, frame_wh=FRAME,
                                project_folder=project)
    per_reel = caption_draw_intent(segment, frame_wh=FRAME,
                                   project_folder=project,
                                   reel_name="reel-a")
    assert plain is not None and per_reel is not None
    assert plain["intent_box"] != per_reel["intent_box"], (
        "the reel override must move the intent, or it is decoration")


def test_caption_skips_without_props_or_with_legacy_canvas(tmp_path):
    """No anchor, no verdict; a non-structural canvas is hung
    differently, so no rect rather than a wrong one."""
    project = _project_with_caption_row(tmp_path)
    no_props = {"overlay_path": "", "frames": {},
                "tight_box": {"width": 904, "height": 480,
                              "placement": {"scaling": 1, "pan": 0.0,
                                            "tilt": -1700.0}}}
    assert caption_draw_intent(no_props, frame_wh=FRAME,
                               project_folder=project) is None
    legacy = _caption_segment(tmp_path, canvas=(840, 200), tilt=-5000.0)
    assert caption_draw_intent(legacy, frame_wh=FRAME,
                               project_folder=project) is None


def test_full_canvas_segments_take_no_intent():
    assert draw_intent_for_segment(
        {"tight_box": None}, kind="caption", segment_id="s",
        intent={}, frame_wh=FRAME) is None
    assert draw_intent_for_segment(
        {}, kind="caption", segment_id="s", intent={}, frame_wh=FRAME
    ) is None


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


def test_matching_target_label_tier_survives_a_rerender():
    """The 1186 tier, through this seam: the digest id died in a
    re-render, the placing label did not, and the pin still binds."""
    intent = {"vox_r1_00": {"canvas_centre": [540.0, 312.0], "scaling": 1}}
    key, target = matching_target(intent, "explainer", "mg_proj_c43f73d8",
                                  "vox_r1_00")
    assert key == "vox_r1_00"
    assert target["canvas_centre"] == [540.0, 312.0]
    # An exact pin still binds where the placing carries no pin.
    intent["mg_proj_c43f73d8"] = {"canvas_centre": [1.0, 2.0],
                                  "scaling": 1}
    key, _target = matching_target(intent, "explainer", "mg_proj_c43f73d8",
                                   "vox_r9_99")
    assert key == "mg_proj_c43f73d8"


def test_matching_target_label_collision_refuses():
    """An exact pin and a label pin both naming one live segment RAISE
    rather than being guessed between - the 1186 collision rule, intact
    through this seam."""
    from library.tools.overlay_intent import OverlayIntentError

    intent = {"vox_r1_00": {"canvas_centre": [540.0, 312.0], "scaling": 1},
              "other_key": {"canvas_centre": [1.0, 2.0], "scaling": 1}}
    with pytest.raises(OverlayIntentError):
        matching_target(intent, "explainer", "vox_r1_00", "other_key")


def test_label_pin_refuses_the_off_frame_tilt_after_rerender():
    """The pixel incident under 1186's world: the graphic re-rendered
    under a new digest, the label pin still binds, Tilt 5184 still
    refused."""
    segment = {"tight_box": {"width": OFF_FRAME_CANVAS[0],
                             "height": OFF_FRAME_CANVAS[1],
                             "placement": {"scaling": 1, "pan": 0.0,
                                           "tilt": OFF_FRAME_TILT}}}
    intent = {"vox_r1_00": {"canvas_centre": [540.0, 312.0], "scaling": 1}}
    draw_intent = draw_intent_for_segment(
        segment, kind="explainer", segment_id="mg_proj_newdigest",
        placement_label="vox_r1_00", intent=intent, frame_wh=FRAME)
    assert draw_intent is not None, (
        "a label pin must arm the check after a re-render kills the id")
    reason = _intent_reason(
        draw_intent, {"scaling": 1, "pan": 0.0, "tilt": OFF_FRAME_TILT},
        {"Pan": 0.0, "Tilt": OFF_FRAME_TILT})
    assert reason and "ENTIRELY OUTSIDE THE FRAME" in reason


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
                                 full_wh=FRAME)
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
        full_wh=FRAME)
    assert not report["passed"]
    assert not report["values"]["passed"]
    finding = report["values"]["findings"][0]
    assert finding["stored"]["tilt"] == -7680.0
    assert finding["expected"]["tilt"] == -7929.0


def test_sweep_passes_a_matching_store():
    timeline = _timeline_with({3: [_Item(10, {"Scaling": 1, "Pan": 0.0,
                                              "Tilt": -7929.0})]})
    report = sweep_reel_overlays(
        timeline, [_sweep_entry("sub_r09_x", REEL09_COMPUTED)], intent={},
        full_wh=FRAME)
    assert report["passed"] and report["values"]["checked"] == 1


def test_sweep_never_fails_the_build():
    """An unreadable timeline degrades to loud skips; the reel stands.

    A clip nothing could read is SKIPPED, never passed - and never a
    build failure either.
    """
    class _Broken:
        def GetItemListInTrack(self, kind, index):
            raise RuntimeError("no Resolve here")

    report = sweep_reel_overlays(
        _Broken(), [_sweep_entry("sub_r09_x", REEL09_CLAMPED)], intent={},
        full_wh=FRAME)
    assert report["passed"] is True
    assert report["values"]["checked"] == 0
    assert report["values"]["skipped"][0]["label"] == "sub_r09_x"


def test_sweep_reports_unavailable_rather_than_raising():
    """What the sweep cannot run at all is said once; the reel stands."""
    report = sweep_reel_overlays(
        _timeline_with({}), [_sweep_entry("sub_r09_x", REEL09_CLAMPED)],
        intent={}, full_wh=None)
    assert report["passed"] is True
    assert "unavailable" in report


def test_sweep_pixel_half_runs_where_a_still_reaches(tmp_path):
    """Frames-container captions decode their first PNG; mov clips skip
    the pixel half loudly (the placement-time check already judges
    their held geometry)."""
    from PIL import Image

    frames = tmp_path / "seg_frames"
    frames.mkdir()
    image = Image.new("RGBA", (200, 120), (0, 0, 0, 0))
    pixels = image.load()
    for y in range(30, 90):
        for x in range(40, 160):
            pixels[x, y] = (255, 255, 255, 255)
    image.save(str(frames / "frame-00.png"))
    image.save(str(frames / "frame-01.png"))
    from library.tools.tight_box import placement_for_box

    placement = placement_for_box(200, 120, 540.0, 960.0, *FRAME)
    entry = _sweep_entry("sub_frames", placement, canvas=(200, 120),
                         computed=placement)
    entry["frames_dir"] = str(frames)
    timeline = _timeline_with({3: [_Item(10, {"Scaling": 1,
                                              "Pan": placement["pan"],
                                              "Tilt": placement["tilt"]})]})
    report = sweep_reel_overlays(timeline, [entry], intent={},
                                 full_wh=FRAME)
    assert report["passed"]
    assert report["pixels"]["checked"] == 1
    # And a mov-only clip skips pixels without failing values.
    mov_entry = _sweep_entry("sub_mov", placement, track=4, frame=20,
                             canvas=(200, 120), computed=placement)
    mov_entry["overlay_path"] = "/nowhere/sub_mov.mov"
    timeline2 = _timeline_with({4: [_Item(20, {"Scaling": 1,
                                               "Pan": placement["pan"],
                                               "Tilt": placement["tilt"]})]})
    report2 = sweep_reel_overlays(timeline2, [mov_entry], intent={},
                                  full_wh=FRAME)
    assert report2["passed"]
    assert report2["pixels"]["checked"] == 0
    assert len(report2["pixels"]["skipped"]) == 1


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


# ── The wiring itself, pinned so it cannot drift back to zero ─────────

def _library_source(relative):
    path = os.path.join(PROJECT_ROOT, *relative.split("/"))
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def test_master_captions_supply_draw_intent():
    source = _library_source(
        "library/steps/step_6_01_render/resolve_build_timeline.py")
    assert "draw_intent=draw_intent_for_segment(" in source
    assert "draw_intent_for_segment,\n" in source


def test_reel_placers_supply_draw_intent_and_collect_sweep_records():
    source = _library_source("library/tools/reel_build.py")
    assert source.count("draw_intent=draw_intent_for_segment(") >= 1
    assert source.count("draw_intent=_draw_intent_for_segment(") >= 1
    assert "sweep_out" in source
    assert "sweep_reel_overlays(" in source
    assert 'build_record["overlay_sweep"]' in source


def test_overlay_verify_has_a_production_caller():
    source = _library_source("library/tools/reel_build.py")
    assert "from library.tools.overlay_verify import" in source
