"""The subject survives being cropped into the delivery frame.

`test_subject_framing.py` covers aiming the crop at the subject.  This
covers the thing aiming could never fix: a crop that is NARROWER than the
subject.  Filling a 1080x1920 frame from 1920x1080 source keeps 31.6% of
the source width, project 001's speaker measures 37.7% of it, and the
delivered frame therefore cut his face on the most important line of the
edit with every gate in the pipeline passing.

Four layers, and each can go quiet on its own:

1. `subject_framing.subject_box` - reducing the 5Hz face track to a
   position AND a width, and refusing when the footage cannot support one.
2. `compile_manifest._conform_fields` - deciding whether the crop holds
   the subject, and switching to the backdrop composition when it does not.
3. `fusion.comp_builder.build_effect_comp` - drawing that composition.
   A parameter nothing reads produces a comp without the effect and no
   warning (AGENTS.md section 10.2).
4. The two checks: `manifest_validator` on the plan, `render_qa` on the
   render.
"""
import os
import sys
from unittest.mock import patch

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (PROJECT_ROOT,
           os.path.join(PROJECT_ROOT, "library"),
           os.path.join(PROJECT_ROOT, "library", "steps",
                        "step_5_04_compile_manifest")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from library.tools import render_qa
from library.tools.manifest_validator import validate_manifest_semantics
from library.tools.fusion.comp_builder import build_effect_comp
from library.tools.subject_framing import (
    MIN_SAMPLES,
    SUBJECT_HEADROOM,
    subject_box,
)
from library.steps.step_5_04_compile_manifest.step import _conform_fields

RATE = 5
VERTICAL = (1080, 1920)
LANDSCAPE = {"clip_001": {"width": 1920, "height": 1080}}
PORTRAIT = {"clip_001": {"width": 1080, "height": 1920}}
FIT = 1080 / 1920

# 001's own numbers, measured on IMG_1818 at t=44.0s of the master.
SUBJECT_001 = 0.3766
CENTRE_001 = 0.3926


def track(centers, widths, rate=RATE):
    return {"sample_rate_hz": rate,
            "face_center_x": list(centers),
            "face_width": list(widths)}


def conform(**kw):
    return _conform_fields(LANDSCAPE, "clip_001", VERTICAL, **kw)


# ─────────────────────────────────────────────────────────
# 1. Reducing the track to a box
# ─────────────────────────────────────────────────────────

class TestSubjectBox:

    def test_position_and_width_together(self):
        box = subject_box(track([0.4] * 10, [0.3] * 10), 0.0, 2.0)
        assert box.center_x == pytest.approx(0.4)
        assert box.width == pytest.approx(0.3)

    def test_width_is_a_high_percentile_not_the_median(self):
        """The frame that matters is the one they lean closest on."""
        widths = [0.20] * 8 + [0.40] * 2
        box = subject_box(track([0.5] * 10, widths), 0.0, 2.0)
        assert box.width > 0.20, "a median window crops the closest frame"

    def test_required_crop_carries_headroom_on_both_sides(self):
        box = subject_box(track([0.5] * 10, [0.30] * 10), 0.0, 2.0)
        assert box.required_crop_width() == pytest.approx(
            0.30 * (1 + 2 * SUBJECT_HEADROOM))

    def test_an_index_with_no_widths_answers_none(self):
        """Every run before this measurement existed. None, not a guess."""
        old = {"sample_rate_hz": RATE, "face_center_x": [0.4] * 10}
        assert subject_box(old, 0.0, 2.0) is None

    def test_too_few_measured_widths_answers_none(self):
        widths = [None] * 10
        widths[0] = 0.3
        assert subject_box(track([0.4] * 10, widths), 0.0, 2.0) is None

    def test_enough_widths_answers(self):
        widths = [None] * 10
        for i in range(MIN_SAMPLES):
            widths[i] = 0.3
        assert subject_box(track([0.4] * 10, widths), 0.0, 2.0) is not None

    def test_an_implausible_width_is_not_a_face(self):
        """A box covering the whole frame would collapse the conform."""
        assert subject_box(track([0.5] * 10, [0.95] * 10), 0.0, 2.0) is None

    def test_only_the_clips_own_range_is_read(self):
        widths = [0.20] * 10 + [0.45] * 10
        box = subject_box(track([0.5] * 20, widths), 0.0, 1.8)
        assert box.width == pytest.approx(0.20)


# ─────────────────────────────────────────────────────────
# 2. The conform decision
# ─────────────────────────────────────────────────────────

class TestTheConformHoldsTheSubject:

    def test_a_subject_that_fits_keeps_the_plain_crop(self):
        """Nothing changes for footage the fill crop can already hold."""
        got = conform(framing_intent=1.0, subject_center_x=0.30,
                      subject_width=0.20)
        assert "framing_backdrop" not in got
        assert got["framing_pan_x"] > 0
        assert got["fill_zoom"] == pytest.approx(3.1605, abs=1e-3)

    def test_001s_own_subject_does_not_fit_and_switches_route(self):
        got = conform(framing_intent=1.0, subject_center_x=CENTRE_001,
                      subject_width=SUBJECT_001)
        assert "framing_backdrop" in got, (
            "a 37.7% subject cannot survive a 31.6% crop")
        assert "framing_pan_x" not in got, (
            "the backdrop shows the whole width - there is nothing to aim, "
            "and a pan slides the composition out of the frame")

    def test_the_visible_width_really_holds_the_subject(self):
        got = conform(framing_intent=1.0, subject_center_x=CENTRE_001,
                      subject_width=SUBJECT_001)
        visible = got["framing_backdrop"]["visible_source_width"]
        assert visible >= SUBJECT_001 * (1 + 2 * SUBJECT_HEADROOM) - 1e-4

    def test_the_composition_centres_the_subject_in_the_frame(self):
        """The arithmetic, checked rather than assumed.

        Resolve crops the central ``1 / fill_zoom`` column of the comp
        canvas.  The subject must land in the middle of THAT column.
        """
        got = conform(framing_intent=1.0, subject_center_x=CENTRE_001,
                      subject_width=SUBJECT_001)
        bd = got["framing_backdrop"]
        # Where the subject sits along the canvas after the comp's
        # Transform, in canvas-width units.
        placed = bd["picture_center_x"] + (CENTRE_001 - 0.5) * bd["picture_scale"]
        assert placed == pytest.approx(0.5, abs=1e-3)

    def test_the_picture_always_covers_the_cropped_column(self):
        """However extreme the subject, no bare frame edge slides in."""
        for cx in (0.06, 0.5, 0.94):
            got = conform(framing_intent=1.0, subject_center_x=cx,
                          subject_width=0.60)
            bd = got["framing_backdrop"]
            column = 1.0 / got["fill_zoom"]
            for scale, centre in ((bd["picture_scale"], bd["picture_center_x"]),
                                  (bd["backdrop_scale"], bd["backdrop_center_x"])):
                left = centre - scale / 2.0
                right = centre + scale / 2.0
                assert left <= 0.5 - column / 2.0 + 1e-3
                assert right >= 0.5 + column / 2.0 - 1e-3

    def test_an_explicit_pan_still_outranks_the_measurement(self):
        """A per-clip creative choice says "aim here" and keeps the crop."""
        got = conform(framing_intent=1.0, framing_pan_x=0.5,
                      subject_center_x=CENTRE_001, subject_width=SUBJECT_001)
        assert "framing_backdrop" not in got
        assert got["framing_pan_x"] > 0
        assert got["subject_safe_zoom"] < got["fill_zoom"], (
            "the measurement is still recorded, so the plan check can "
            "report what the creative choice cost")

    def test_a_declared_letterbox_is_left_alone(self):
        got = conform(framing_intent=0.0, subject_center_x=CENTRE_001,
                      subject_width=SUBJECT_001)
        assert got == {"needs_conform": False, "framing_intent": 0.0,
                       "framing_delivered": 0.0}

    def test_portrait_source_is_never_width_limited(self):
        """A vertical clip in a vertical frame crops height, not width."""
        got = _conform_fields(PORTRAIT, "clip_001", (1080, 1080),
                              framing_intent=1.0, subject_center_x=0.4,
                              subject_width=0.9)
        assert "framing_backdrop" not in got
        assert "subject_safe_zoom" not in got

    def test_no_measurement_changes_nothing(self):
        """Every project whose index predates `face_width`."""
        with_none = conform(framing_intent=1.0, subject_center_x=CENTRE_001)
        assert "framing_backdrop" not in with_none
        assert "subject_width" not in with_none


# ─────────────────────────────────────────────────────────
# 3. The comp that draws it
# ─────────────────────────────────────────────────────────

def backdrop_effects(**over):
    effects = {
        "backdrop_picture_scale": 0.6463,
        "backdrop_picture_center_x": 0.5694,
        "backdrop_scale": 1.0,
        "backdrop_center_x": 0.6074,
        "vignette": False,
    }
    effects.update(over)
    return effects


class TestTheBackdropReachesThePicture:

    def test_it_draws_nodes(self):
        comp = build_effect_comp(backdrop_effects(), clip_dur=300,
                                 source_res=(1920, 1080))
        assert "Blur {" in comp
        assert comp.count("Transform {") >= 2
        assert "Merge {" in comp

    def test_both_branches_come_from_the_same_media_in(self):
        """One frame, shown twice - not a second clip."""
        comp = build_effect_comp(backdrop_effects(), clip_dur=300,
                                 source_res=(1920, 1080))
        assert comp.count('SourceOp = "MediaIn1"') == 2

    def test_the_geometry_reaches_the_serialized_comp(self):
        comp = build_effect_comp(backdrop_effects(), clip_dur=300,
                                 source_res=(1920, 1080))
        assert "0.6463" in comp and "0.5694" in comp and "0.6074" in comp

    def test_the_backdrop_is_upstream_of_the_ken_burns_drift(self):
        """The drift must move the composed picture, not the plate."""
        comp = build_effect_comp(
            backdrop_effects(zoom_start=1.0, zoom_mid=1.015, zoom_end=1.03),
            clip_dur=300, source_res=(1920, 1080))
        assert 'SourceOp = "Merge1"' in comp, (
            "the drift Transform must take the Merge as its input")

    def test_a_comp_without_the_parameter_draws_no_backdrop(self):
        comp = build_effect_comp({"vignette": False, "zoom_start": 1.0,
                                  "zoom_mid": 1.0, "zoom_end": 1.0},
                                 clip_dur=300, source_res=(1920, 1080))
        assert "Blur {" not in comp

    def test_the_conforms_own_output_is_what_the_builder_reads(self):
        """The two halves are joined by compile_manifest; this asserts the
        names really match rather than trusting that they do."""
        got = conform(framing_intent=1.0, subject_center_x=CENTRE_001,
                      subject_width=SUBJECT_001)
        bd = got["framing_backdrop"]
        comp = build_effect_comp({
            "backdrop_picture_scale": bd["picture_scale"],
            "backdrop_picture_center_x": bd["picture_center_x"],
            "backdrop_scale": bd["backdrop_scale"],
            "backdrop_center_x": bd["backdrop_center_x"],
            "vignette": False,
        }, clip_dur=300, source_res=(1920, 1080))
        assert "Blur {" in comp
        assert str(bd["picture_scale"]) in comp


# ─────────────────────────────────────────────────────────
# 4a. The plan check - the half that carries the verdict
# ─────────────────────────────────────────────────────────

def manifest_with(clip):
    base = {"clip_id": "clip_001", "label": "speech_15_seg0",
            "source_file": "/x/a.mov", "source_in": 1.0, "source_out": 3.0,
            "timeline_in": 0.0, "timeline_out": 2.0}
    base.update(clip)
    return {"tracks": {"V1": {"clips": [base]}, "V2": {"clips": []}}}


def subject_errors(manifest):
    return [e for e in validate_manifest_semantics(manifest)
            if "crops the subject" in e]


class TestThePlanCheck:

    def test_a_crop_narrower_than_the_subject_fails(self):
        errors = subject_errors(manifest_with({
            "needs_conform": True, "fill_zoom": 3.1605,
            "subject_width": SUBJECT_001, "framing_pan_x": 366.59,
        }))
        assert errors, "001's shipped geometry must not pass"
        assert "speech_15_seg0" in errors[0]

    def test_the_backdrop_route_passes(self):
        assert not subject_errors(manifest_with({
            "needs_conform": True, "fill_zoom": 3.1605,
            "subject_width": SUBJECT_001,
            "framing_backdrop": {"visible_source_width": 0.4896,
                                 "picture_scale": 0.6463,
                                 "picture_center_x": 0.5694,
                                 "backdrop_scale": 1.0,
                                 "backdrop_center_x": 0.6074},
        }))

    def test_a_crop_wide_enough_passes(self):
        assert not subject_errors(manifest_with({
            "needs_conform": True, "fill_zoom": 2.0,
            "subject_width": 0.20,
        }))

    def test_an_unmeasured_clip_is_not_judged(self):
        assert not subject_errors(manifest_with({
            "needs_conform": True, "fill_zoom": 3.1605,
        }))

    def test_the_compilers_own_output_passes_its_own_check(self):
        clip = {"label": "speech_15_seg0", "source_file": "/x/a.mov",
                "source_in": 1.0, "source_out": 3.0,
                "timeline_in": 0.0, "timeline_out": 2.0}
        clip.update(conform(framing_intent=1.0, subject_center_x=CENTRE_001,
                            subject_width=SUBJECT_001))
        assert not subject_errors(manifest_with(clip))


# ─────────────────────────────────────────────────────────
# 4b. The render check - the backstop
# ─────────────────────────────────────────────────────────

class FakeCascade:
    """A cascade that finds exactly the box the test wants it to."""

    def __init__(self, box):
        self.box = box

    def empty(self):
        return False

    def detectMultiScale(self, *_a, **_kw):
        return [] if self.box is None else [self.box]


def face_intact_with(box, w=480, h=854, frames=6):
    import numpy as np
    stream = iter([np.zeros((1, h, w), dtype=np.uint8) for _ in range(frames)])
    with patch.object(render_qa, "_probe_video_size", return_value=(1080, 1920)), \
            patch.object(render_qa, "_stream_raw_frames", return_value=stream), \
            patch.object(render_qa, "load_face_cascade",
                         return_value=FakeCascade(box)):
        return render_qa.measure_face_intact("/x/master.mp4")


class TestTheRenderCheck:

    def test_a_face_against_the_frame_edge_fails(self):
        result = face_intact_with((0, 200, 300, 300))
        assert result.passed is False
        assert result.severity == "error"
        assert "left" in result.value["examples"][0]["edges"]

    def test_a_face_clear_of_every_edge_passes(self):
        result = face_intact_with((90, 200, 300, 300))
        assert result.passed is True
        assert result.value["cropped_frames"] == 0

    def test_a_render_with_no_face_passes_and_says_so(self):
        result = face_intact_with(None)
        assert result.passed is True
        assert result.value["face_frames"] == 0
        assert "nothing to crop" in result.detail

    def test_a_detection_too_small_to_be_the_speaker_is_ignored(self):
        """A passer-by at the frame edge must not fail a correct render."""
        result = face_intact_with((0, 200, 40, 40))
        assert result.passed is True
        assert result.value["face_frames"] == 0

    def test_step_6_02_turns_the_fault_into_a_failed_framing_check(self):
        """A measurement nothing reads is not a gate."""
        source = os.path.join(
            PROJECT_ROOT, "library", "steps", "step_6_02_validate_output",
            "step.py")
        with open(source, encoding="utf-8") as handle:
            body = handle.read()
        marker = 'r.metric == "face_intact"'
        assert marker in body
        branch = body[body.index(marker):body.index(marker) + 900]
        branch = branch[:branch.index("elif r.metric", 10)]
        assert 'framing_check["pass"] = False' in branch
        assert 'framing_check["issues"].append(r.detail)' in branch
