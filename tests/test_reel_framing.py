"""The picture a built reel puts on the frame, and the gate that reads it.

Every number in this file is either arithmetic or a measurement recorded
elsewhere in the repository.  The two that are measurements:

- **rows 656..1264** for 3840x2160 fitted into 1080x1920.  Independently
  measured by ``render_qa``'s occupancy pass on project 001's real export
  and recorded in ``library/tools/framing_intent.py`` as rows 656..1263,
  608 of 1920 rows.  A different project, different source files,
  different resolution, and the arithmetic here lands on the same top row
  and one below on the bottom - 607.5 rounded.
- **the twenty harvest reels** of the GEO Podcast field test, read off
  Resolve on 2026-09-06: 376 video items across 49 timelines, every one
  of them ``ZoomX=ZoomY=1.0, Pan=Tilt=0, Crop*=0`` on a 1080x1920
  ``scaleToFit`` timeline.  ``_HARVEST`` below is that transform.

AGENTS.md 10.4: a gate that cannot fail is worse than no gate, and one
that fails correct output is the same defect from the other side.  Both
directions are pinned here, on the geometry the captain's reels really
carry.
"""

import pytest

from library.tools.framing_intent import DEFAULT_FRAMING_INTENT, FILL, LETTERBOX
from library.tools.reel_conformance_verifier import (
    FindingClass,
    TimelineItem,
    check_delivered_framing,
)
from library.tools.reel_framing import (
    PIXEL,
    ReelFramingError,
    declared_picture,
    delivered_picture,
    disagreement,
    display_size,
    max_zoom,
)

# The delivery frame every reel in this engine is built into.
FRAME_W, FRAME_H = 1080, 1920

# The podcast's source: 3840x2160 MXF, measured by step 1.02.
SRC_W, SRC_H = 3840, 2160

# What Resolve reports for an item nobody has touched, which is what all
# 376 items of the field test carry.
_HARVEST = {"ZoomX": 1.0, "ZoomY": 1.0, "Pan": 0.0, "Tilt": 0.0,
            "CropLeft": 0.0, "CropRight": 0.0,
            "CropTop": 0.0, "CropBottom": 0.0}


def _item(transform=None, source="/footage/LC4930.MXF", track=1):
    return TimelineItem(
        track_type="video", track_index=track, start_frame=0, end_frame=100,
        duration_frames=100, source_start_frame=0, source_end_frame=100,
        source_file=source, speaker="Craig", name="LC4930.MXF",
        transform=dict(_HARVEST if transform is None else transform))


_SIZES = {"/footage/LC4930.MXF": {"width": SRC_W, "height": SRC_H,
                                  "rotation": 0}}


# ── The geometry ─────────────────────────────────────────────────────

class TestDeliveredPicture:

    def test_untouched_landscape_delivers_the_measured_strip(self):
        """The number `render_qa` measured on a real export, from metadata."""
        picture = delivered_picture(SRC_W, SRC_H, FRAME_W, FRAME_H, _HARVEST)
        assert picture.rect == (0, 656, 1080, 1264)
        assert picture.framing_intent == LETTERBOX
        assert round(picture.covered_fraction, 4) == 0.3167
        assert not picture.stretched
        assert not picture.crop_unread

    def test_absent_transform_reads_as_the_identity_one(self):
        """`_apply_conform` returns without setting anything for a
        letterbox, so "no properties" and "identity properties" are one
        state by the renderer's own reckoning."""
        assert (delivered_picture(SRC_W, SRC_H, FRAME_W, FRAME_H, None).rect
                == delivered_picture(SRC_W, SRC_H, FRAME_W, FRAME_H,
                                     _HARVEST).rect)

    def test_fill_zoom_covers_the_whole_frame(self):
        ceiling = max_zoom(SRC_W, SRC_H, FRAME_W, FRAME_H)
        picture = delivered_picture(SRC_W, SRC_H, FRAME_W, FRAME_H,
                                    {"ZoomX": ceiling, "ZoomY": ceiling})
        assert picture.covered_fraction == pytest.approx(1.0, abs=1e-3)
        assert picture.framing_intent == pytest.approx(FILL)
        assert picture.left < 0 and picture.right > FRAME_W

    @pytest.mark.parametrize("intent", [0.0, 0.25, 0.5, 0.75, 1.0])
    def test_the_intent_survives_a_round_trip(self, intent):
        """`declared_picture` runs `_conform_fields`' formula forwards and
        `delivered_picture` runs it backwards; they must agree, or a
        disagreement between them would be two spellings of one geometry
        rather than a real difference in the picture."""
        declared = declared_picture(SRC_W, SRC_H, FRAME_W, FRAME_H, intent)
        assert declared.framing_intent == pytest.approx(intent, abs=1e-6)

    def test_pan_moves_the_picture_and_not_its_size(self):
        # History gain: this pins the 2026-09-11 read (see HISTORY_GAIN
        # in test_tight_box.py); under today's gain Pan 120 moves 240.
        centred = delivered_picture(SRC_W, SRC_H, FRAME_W, FRAME_H,
                                    {"ZoomX": 2.0, "ZoomY": 2.0},
                                    draw_gain=1.0)
        panned = delivered_picture(SRC_W, SRC_H, FRAME_W, FRAME_H,
                                   {"ZoomX": 2.0, "ZoomY": 2.0, "Pan": 120.0},
                                   draw_gain=1.0)
        assert panned.left - centred.left == 120
        assert (panned.right - panned.left) == (centred.right - centred.left)

    def test_zoom_beyond_fill_is_read_back_as_a_crop_factor(self):
        ceiling = max_zoom(SRC_W, SRC_H, FRAME_W, FRAME_H)
        picture = delivered_picture(SRC_W, SRC_H, FRAME_W, FRAME_H,
                                    {"ZoomX": ceiling * 1.3,
                                     "ZoomY": ceiling * 1.3})
        assert picture.framing_intent == FILL
        assert picture.crop_factor == pytest.approx(1.3)

    def test_a_rotated_source_is_read_at_its_display_size(self):
        assert display_size(1920, 1080, rotation=90) == (1080, 1920)
        picture = delivered_picture(1920, 1080, FRAME_W, FRAME_H,
                                    _HARVEST, rotation=90)
        assert picture.covered_fraction == pytest.approx(1.0)
        assert picture.framing_intent == FILL

    def test_a_source_that_already_covers_fills_at_every_intent(self):
        """`source_covers_frame`: there are no bars to give, so LETTERBOX
        and FILL are the same picture and neither is a defect."""
        for intent in (LETTERBOX, 0.5, FILL):
            declared = declared_picture(1080, 1920, FRAME_W, FRAME_H, intent)
            assert declared.framing_intent == FILL
            assert declared.covered_fraction == pytest.approx(1.0)

    def test_a_source_with_no_dimensions_refuses(self):
        with pytest.raises(ReelFramingError):
            delivered_picture(0, 0, FRAME_W, FRAME_H, _HARVEST)


# ── The comparison ───────────────────────────────────────────────────

class TestDisagreement:

    def test_the_harvest_geometry_agrees_with_a_letterbox_declaration(self):
        delivered = delivered_picture(SRC_W, SRC_H, FRAME_W, FRAME_H, _HARVEST)
        declared = declared_picture(SRC_W, SRC_H, FRAME_W, FRAME_H, LETTERBOX)
        assert disagreement(delivered, declared) is None

    def test_the_harvest_geometry_disagrees_with_the_engine_default(self):
        delivered = delivered_picture(SRC_W, SRC_H, FRAME_W, FRAME_H, _HARVEST)
        declared = declared_picture(SRC_W, SRC_H, FRAME_W, FRAME_H,
                                    DEFAULT_FRAMING_INTENT)
        why = disagreement(delivered, declared)
        assert why is not None
        assert "31.6" in why and "100.00%" in why

    def test_one_pixel_is_the_same_picture_and_two_is_not(self):
        """The only tolerance is the resolution of the medium: one real
        number rounded by two rules can land a pixel apart.

        The tolerance is in PIXELS, and a Tilt unit is not a pixel: on
        this geometry one unit draws `(2160/1920) * (1080/3840)` =
        0.3164 px, so a pixel of movement is Tilt 3.16 and two pixels
        is Tilt 6.32.  Spelling these as `PIXEL` and `PIXEL + 1` was
        the picture path's own version of the defect - it asked for
        two pixels and moved two thirds of one.
        """
        # History gain throughout: the 2026-09-11 read (one unit drew
        # 0.3164 px then; 0.6328 under today's).
        one_pixel_of_tilt = 1.0 / ((2160 / 1920) * (1080 / 3840))
        assert one_pixel_of_tilt == pytest.approx(3.1605, abs=0.001)
        base = delivered_picture(SRC_W, SRC_H, FRAME_W, FRAME_H, _HARVEST,
                                 draw_gain=1.0)
        # NEGATIVE, because positive Tilt moves the picture UP - measured,
        # and the other half of what this path had wrong: it added Tilt to
        # the centre, so every vertical aim went the wrong way as well as
        # 3.16x short.
        near = delivered_picture(SRC_W, SRC_H, FRAME_W, FRAME_H,
                                 dict(_HARVEST, Tilt=-one_pixel_of_tilt),
                                 draw_gain=1.0)
        far = delivered_picture(SRC_W, SRC_H, FRAME_W, FRAME_H,
                                dict(_HARVEST, Tilt=-2 * one_pixel_of_tilt),
                                draw_gain=1.0)
        assert near.top == base.top + PIXEL
        assert far.top == base.top + 2 * PIXEL
        assert disagreement(near, base) is None
        assert disagreement(far, base) is not None

    def test_a_stretch_is_named_as_a_stretch(self):
        delivered = delivered_picture(SRC_W, SRC_H, FRAME_W, FRAME_H,
                                      dict(_HARVEST, ZoomX=2.0, ZoomY=1.0))
        assert delivered.stretched
        why = disagreement(delivered,
                           declared_picture(SRC_W, SRC_H, FRAME_W, FRAME_H,
                                            LETTERBOX))
        assert "stretched" in why

    def test_a_non_zero_crop_is_refused_rather_than_assumed(self):
        """AGENTS.md 5: judge a Resolve call by what it RETURNS. Every
        Crop* in the field test reads back 0.0, so their units have never
        been observed and are not guessed at here."""
        delivered = delivered_picture(SRC_W, SRC_H, FRAME_W, FRAME_H,
                                      dict(_HARVEST, CropLeft=100.0))
        assert delivered.crop_unread
        why = disagreement(delivered,
                           declared_picture(SRC_W, SRC_H, FRAME_W, FRAME_H,
                                            LETTERBOX))
        assert "crop" in why and "will not assume" in why


# ── F12, the gate ────────────────────────────────────────────────────

class TestF12:

    def test_it_passes_the_framing_the_project_declared(self):
        """The correct-output direction, on the geometry the captain's
        twenty harvest reels really carry."""
        findings = check_delivered_framing(
            "Reel 01 (harvest)", [_item()], FRAME_W, FRAME_H,
            source_sizes=_SIZES, declared_intent=LETTERBOX)
        assert findings == []

    def test_it_fails_the_same_reel_under_the_engine_default(self):
        """The failing direction. Nothing about the timeline changed -
        only what the project says it wanted."""
        findings = check_delivered_framing(
            "Reel 01 (harvest)", [_item()], FRAME_W, FRAME_H,
            source_sizes=_SIZES, declared_intent=DEFAULT_FRAMING_INTENT)
        assert [f.finding_class for f in findings] == [FindingClass.F12]
        assert findings[0].severity == "error"
        assert findings[0].detail["delivered"]["covered_fraction"] == 0.3167
        assert findings[0].detail["declared"]["covered_fraction"] == 1.0

    def test_many_items_of_one_shape_are_one_finding(self):
        """34 clips conforming the same way is one sentence, not 34."""
        findings = check_delivered_framing(
            "Reel 07 (harvest)", [_item() for _ in range(34)],
            FRAME_W, FRAME_H, source_sizes=_SIZES,
            declared_intent=DEFAULT_FRAMING_INTENT)
        assert len(findings) == 1
        assert findings[0].detail["items"] == 34
        assert "34 of 34" in findings[0].message

    def test_an_unresolved_declaration_warns_rather_than_passing(self):
        findings = check_delivered_framing(
            "Reel 01 (harvest)", [_item()], FRAME_W, FRAME_H,
            source_sizes=_SIZES, declared_intent=None)
        assert len(findings) == 1
        assert findings[0].severity == "warning"
        assert "cannot be compared" in findings[0].message

    def test_a_source_the_catalog_does_not_carry_warns_by_name(self):
        """Silence is not a pass: the caption gate expected zero cards,
        found 762 and passed. This says what it could not read."""
        findings = check_delivered_framing(
            "Reel 01 (harvest)", [_item(source="/footage/UNKNOWN.MXF")],
            FRAME_W, FRAME_H, source_sizes=_SIZES,
            declared_intent=DEFAULT_FRAMING_INTENT)
        assert len(findings) == 1
        assert findings[0].severity == "warning"
        assert "UNKNOWN.MXF" in findings[0].message

    def test_caption_cards_are_not_graded_as_footage(self):
        """V3 carries 1080x1920 overlays, which fill by construction.
        Grading them would report a defect on every reel that has them."""
        captions = [_item(source="/overlays/sub_x.mov", track=3)]
        findings = check_delivered_framing(
            "Reel 01 (harvest)", captions, FRAME_W, FRAME_H,
            source_sizes=_SIZES, declared_intent=DEFAULT_FRAMING_INTENT)
        assert findings == []

    def test_a_timeline_with_no_resolution_is_not_graded(self):
        """There is nothing for the picture to be a fraction OF, and F10
        already reports a timeline that does not say its own shape."""
        assert check_delivered_framing(
            "Reel 01 (harvest)", [_item()], 0, 0,
            source_sizes=_SIZES, declared_intent=FILL) == []
