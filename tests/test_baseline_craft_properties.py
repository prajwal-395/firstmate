"""Six baseline-craft properties, as checks the pipeline runs every build.

Sourced from `data/vep-craft-reference-decomposition/report.md`, which
measured them on the one video the captain judged - project 001's
`Pipeline_Edit.mp4` and its compiled manifest.  Every number asserted
below is either a value that report measured or a value the pipeline
itself already declares; nothing here is a taste threshold.

    P1  the picture fills the delivery frame, and one video has one
        geometry                                               GATES
    P2  somewhere in the frame there is colour                  reports
    P3  where someone speaks, the speech is above the bed       reports
    P4  deliverable at platform loudness without clipping       GATES
    P6  no caption card flashes                                 GATES
    P7  no discretionary effect applied to everything           GATES

P2 and P3 report a number and do not fail the build: P2's floor is an
open captain decision, and P3 cannot pass until the mix has a delivery
route, which is also open.  A gate that must fail is worse than no gate,
so the tests here assert that they DO NOT fail - and that promoting them
is one boolean, which the gate-flip tests exercise directly.

P5 (caption safe area) is deliberately absent: it belongs to
`library/tools/safe_area.py` and its own lane.
"""

import json
from unittest.mock import patch

import numpy as np
import pytest

from library.tools import render_qa
from library.tools.manifest_validator import (
    MIN_CAPTION_DISPLAY_SECONDS,
    SOFT_CAPTION_DISPLAY_SECONDS,
    _check_no_effect_on_everything,
    _check_no_flash_captions,
)
from library.tools.render_qa import (
    LIT_LUMA_THRESHOLD,
    analyze_color_histogram,
    measure_chroma_presence,
    measure_frame_occupancy,
    measure_lufs,
    measure_speech_above_bed,
)

W, H = 108, 192  # 1080x1920 in tenths - the same aspect, a hundredth of the pixels


# ─── frame builders ──────────────────────────────────────────

def _gray_frame(lit_fraction: float):
    """A frame whose middle `lit_fraction` of rows is lit, rest black.

    The shape project 001 shipped: a picture band floating in bars.
    """
    frame = np.zeros((1, H, W), dtype=np.uint8)
    band = round(H * lit_fraction)
    top = (H - band) // 2
    frame[0, top:top + band, :] = 180
    return frame


def _dark_textured_picture(h: int = H, w: int = W):
    """A frame that is genuinely dark AND genuinely full-bleed.

    This is the case the old darkness-only method got wrong, and it is
    built to project 001's own numbers.  Its 50.0s frame is a car
    interior: the top 100 rows measure min/max/mean luma 0/23/4.3 with a
    minimum within-row standard deviation of 3.95 and a median of 5.28,
    and boosting them 4x shows a headliner, a window with light across
    it, and the top of the subject's cap.  This returns 0/27/6.6 with a
    row std of 3.80 to 5.47 - the same shape, from a sine along each row,
    a gentle gradient down the frame, and a diagonal highlight standing
    in for the window.

    EVERY row of it is below `LIT_LUMA_THRESHOLD`, so the old method
    measured this picture at 0% occupancy.  Not one row is flat, so it is
    all picture.
    """
    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    luma = (np.sin(x / 5.0) + 1.0) * 3.2
    luma += (y / h) * 3.0
    luma += np.clip(18.0 - np.abs(x - 0.62 * w - 0.22 * y) * 1.7, 0.0, None)
    return np.clip(luma, 0, 255)


def _frame(luma):
    return np.round(luma).astype(np.uint8)[None, :, :]


def _dark_frame():
    """The whole delivery frame, dark and textured, edge to edge."""
    return _frame(_dark_textured_picture())


def _shot_with_a_dark_top(dark_rows: int = 80):
    """001's 50.0s shape: a dark textured top, then lit picture.

    The dark rows run from the frame edge - so contiguity alone would
    still call them a bar - and they are picture because they have
    structure.
    """
    luma = _dark_textured_picture()
    luma[dark_rows:, :] += 130.0
    return _frame(np.clip(luma, 0, 255))


def _lit_frame_with_an_interior_black_band(top: int, bottom: int):
    """A lit picture carrying a FLAT black band in its middle.

    The other half of 001's 50.0s frame: 506 of its 621 dark rows were
    interior, not against an edge.  This band is as flat as a real bar,
    so only contiguity from the frame boundary separates them.
    """
    frame = np.full((1, H, W), 170, dtype=np.uint8)
    frame[0, top:bottom, :] = 0
    return frame


def _letterboxed_frame(picture_rows: int, speckle: int = 0):
    """A real letterbox: flat bars from both edges around a picture band.

    `speckle` puts one lifted pixel every `speckle` columns into the
    bars, because an encoded bar is not always pristine.  Its shape is
    measured, not invented: on a 1080x608 picture padded into 1080x1920
    and re-encoded, the maximum bar row mean is 0.000 at crf 18 and 23
    and 0.006 at crf 30 - and 0.000 again with noise added and the
    picture lanczos-scaled before the pad.  Project 001's own shipped
    master is the loosest real bar measured: row means from 0.00 to 0.14
    and within-row standard deviations to 0.35, which is a handful of
    pixels sitting one level off black.  A bar is not a slightly grey
    field; it carries no light.
    """
    luma = np.zeros((H, W), dtype=np.float32)
    if speckle:
        luma[:, ::speckle] = 1.0
    top = (H - picture_rows) // 2
    luma[top:top + picture_rows, :] = _dark_textured_picture(picture_rows, W) + 150.0
    return _frame(np.clip(luma, 0, 255))


def _yuv_frame(chroma: float, accent_rows: int = 0, accent_chroma: float = 0.0):
    """A lit yuv444p frame at a uniform chroma, optionally with an accent band."""
    y = np.full((H, W), 180, dtype=np.uint8)
    # S = sqrt((U-128)^2 + (V-128)^2); put it all in U so S == |U-128|.
    u = np.full((H, W), 128 + chroma, dtype=np.uint8)
    v = np.full((H, W), 128, dtype=np.uint8)
    if accent_rows:
        u[:accent_rows, :] = 128 + accent_chroma
    return np.stack([y, u, v])


def _spans(intents):
    """One one-second FramingSpan per entry, matching sample_fps=1.0.

    The helpers below sample at 1 Hz, so frame `i` is at second `i` and a
    list of intents lays out over the timeline one-for-one.
    """
    if intents is None:
        return None
    return [render_qa.FramingSpan(float(i), float(i + 1), float(v))
            for i, v in enumerate(intents)]


def _occupancy(fractions, framing_intents=None, framing_spans=None, **kwargs):
    return _occupancy_of([_gray_frame(f) for f in fractions],
                         framing_intents=framing_intents,
                         framing_spans=framing_spans, **kwargs)


def _occupancy_of(frames, framing_intents=None, framing_spans=None, **kwargs):
    """Measure occupancy over frames the caller built pixel by pixel.

    `framing_intents` is the convenience form: one intent per sampled
    second, in order. `framing_spans` is the real parameter, for the
    cases that need a span to cover more than one sample.
    """
    if framing_spans is None:
        framing_spans = _spans(framing_intents)
    with patch.object(render_qa, "_probe_video_size", return_value=(W, H)), \
            patch.object(render_qa, "_stream_raw_frames", return_value=iter(frames)):
        return measure_frame_occupancy("master.mp4", framing_spans=framing_spans,
                                       sample_fps=1.0, **kwargs)


def _dark_row_fraction(frame):
    """What the OLD darkness-only method would have measured on a frame."""
    row_mean = frame[0].astype(np.float32).mean(axis=1)
    return float((row_mean >= LIT_LUMA_THRESHOLD).sum()) / frame.shape[1]


def _chroma(frames, **kwargs):
    with patch.object(render_qa, "_probe_video_size", return_value=(W, H)), \
            patch.object(render_qa, "_stream_raw_frames", return_value=iter(frames)):
        return measure_chroma_presence("master.mp4", sample_fps=1.0, **kwargs)


# ═══ P1: the picture fills the frame, and one video has one geometry ═══

class TestP1FrameOccupancy:

    def test_a_filled_frame_with_one_geometry_passes(self):
        result = _occupancy([1.0] * 10)
        assert result.passed, result.detail
        assert result.value["median_picture_fraction"] == 1.0
        assert result.value["spread"] == 0.0

    def test_a_letterboxed_picture_fails_when_nothing_declared_bars(self):
        """Project 001's shape: 608 picture rows of 1920, and no declaration.

        Nothing declaring an intent resolves to FILL, so this is the frame
        the removed letterbox heuristic produced and nothing noticed.
        """
        result = _occupancy([608 / 1920] * 10)
        assert not result.passed
        assert result.severity == "error"
        assert "letterboxed and nothing asked for bars" in result.detail

    def test_geometry_changing_inside_one_video_fails_on_its_own(self):
        """The subtler of the two 001 failures, and it must not fall out.

        These frames pass the fill floor - the median is a full frame - and
        still fail, because the picture jumps size mid-video. A viewer
        cannot name "framing intent" but can see that.
        """
        result = _occupancy([1.0] * 6 + [0.80] * 4)
        assert not result.passed
        assert "changes size within one declared framing" in result.detail
        # and specifically NOT via the fill floor
        assert "letterboxed" not in result.detail

    def test_both_001_failures_are_reported_together(self):
        """001 fails twice, for different reasons, and both must show."""
        result = _occupancy([608 / 1920] * 8 + [1.0] * 2)
        assert not result.passed
        assert "letterboxed and nothing asked for bars" in result.detail
        assert "changes size within one declared framing" in result.detail

    def test_a_declared_letterbox_is_exempt_from_the_fill_floor(self):
        """A series that declares bars gets them - cinematic_narrative does.

        There is no channel-wide number for how much frame a deliberately
        inset picture should occupy, so inventing one here would answer a
        brand decision. The consistency half still applies.
        """
        result = _occupancy([0.34] * 10, framing_intents=[0.0] * 10)
        assert result.passed, result.detail
        assert result.threshold["fill_floor_applies"] is False
        assert result.threshold["consistency_applies"] is True

    def test_a_declared_letterbox_still_owes_one_geometry(self):
        result = _occupancy([0.34] * 6 + [0.9] * 4, framing_intents=[0.0] * 10)
        assert not result.passed
        assert "changes size within one declared framing" in result.detail

    def test_a_declared_per_clip_framing_may_change_geometry(self):
        """Two declared framings, each delivered where it was declared.

        Project 001's shape: landscape A-roll letterboxes and portrait
        cutaways cannot, so the video changes size on purpose. It passes
        because the manifest says where each geometry belongs, not
        because the check stopped looking.
        """
        result = _occupancy([1.0] * 6 + [0.34] * 4,
                            framing_intents=[1.0] * 6 + [0.0] * 4)
        assert result.passed, result.detail
        assert result.threshold["consistency_applies"] is True
        assert result.value["declared_framing_intents"] == [0.0, 1.0]
        assert result.value["by_declared_framing"]["0"]["frames_sampled"] == 4
        assert result.value["by_declared_framing"]["1"]["frames_sampled"] == 6

    def test_a_per_clip_declaration_still_owes_one_geometry_per_framing(self):
        """The half a mixed declaration used to switch off entirely.

        Six FILL samples, and the four LETTERBOX ones disagree with each
        other. Nothing declared that, and the old rule - consistency off
        as soon as more than one intent appears - reported a clean pass.
        """
        result = _occupancy([1.0] * 6 + [0.34, 0.34, 0.9, 0.9],
                            framing_intents=[1.0] * 6 + [0.0] * 4)
        assert not result.passed
        assert "within one declared framing (0)" in result.detail
        assert "one framing has one geometry" in result.detail

    def test_the_fill_floor_applies_to_the_fill_half_of_a_mixed_video(self):
        """A letterboxed stretch does not excuse a barred FILL stretch."""
        result = _occupancy([0.34] * 6 + [0.34] * 4,
                            framing_intents=[1.0] * 6 + [0.0] * 4)
        assert not result.passed
        assert "declares FILL" in result.detail
        assert "letterboxed and nothing asked for bars" in result.detail

    def test_a_sample_outside_every_span_is_reported_not_folded_in(self):
        result = _occupancy([1.0] * 6,
                            framing_spans=[render_qa.FramingSpan(0.0, 3.0, 1.0)])
        assert result.passed, result.detail
        assert result.value["unattributed_samples"] == 3
        assert "outside every declared span" in result.detail

    def test_spans_that_miss_the_render_entirely_are_an_error(self):
        result = _occupancy([1.0] * 4,
                            framing_spans=[render_qa.FramingSpan(90.0, 99.0, 1.0)])
        assert not result.passed
        assert result.severity == "error"
        assert "different timelines" in result.detail

    def test_the_later_span_wins_an_overlap_so_v2_covers_v1(self):
        """V2 is laid down after V1 and is what the viewer sees."""
        result = _occupancy(
            [1.0, 1.0, 0.34, 1.0],
            framing_spans=[render_qa.FramingSpan(0.0, 4.0, 1.0),
                           render_qa.FramingSpan(2.0, 3.0, 0.0)])
        assert result.passed, result.detail
        assert result.value["by_declared_framing"]["0"]["frames_sampled"] == 1
        assert result.value["by_declared_framing"]["1"]["frames_sampled"] == 3

    def test_the_lit_threshold_is_the_pipelines_own_black(self):
        assert LIT_LUMA_THRESHOLD == 12.0

    # ── black is not the same thing as dark (issue #221) ──
    # The check called a row "lit" above a luma threshold and read every
    # other row as bar, which fails any render carrying a dim shot - most
    # of them.  These are the cases that separates, in both directions.

    def test_a_dark_but_full_bleed_frame_is_not_letterboxed(self):
        """The exact defect: a dark picture read as a frame full of bars.

        Every row of this frame is below the lit threshold, so the old
        method measured it at 0% occupancy and failed the render. Not one
        row is flat, so all of it is picture.
        """
        frames = [_dark_frame()] * 10
        assert _dark_row_fraction(frames[0]) == 0.0, \
            "the fixture must be genuinely dark, or it proves nothing"
        result = _occupancy_of(frames)
        assert result.passed, result.detail
        assert result.value["median_picture_fraction"] == 1.0
        assert result.value["max_top_bar_rows"] == 0
        assert result.value["max_bottom_bar_rows"] == 0

    def test_a_dark_shot_among_lit_ones_does_not_read_as_a_geometry_change(self):
        """001's whole failure, in the shape it actually took.

        A master that is lit for most of its length and dark at 50s used
        to report the picture changing size by 31 points of frame height.
        Nothing moved; one shot was in a car at night.
        """
        frames = [_gray_frame(1.0)] * 8 + [_dark_frame()] * 2 + \
                 [_shot_with_a_dark_top()] * 2
        result = _occupancy_of(frames)
        assert result.passed, result.detail
        assert result.value["spread"] == 0.0

    def test_a_dark_region_inside_the_picture_is_not_a_bar(self):
        """Contiguity from the frame edge, on its own.

        This band is as flat as any real bar - it IS black - and it is in
        the middle of a lit picture, where a letterbox bar cannot be. 506
        of the 621 dark rows in 001's 50.0s frame were interior like this.
        """
        frames = [_lit_frame_with_an_interior_black_band(60, 130)] * 10
        assert _dark_row_fraction(frames[0]) < 0.65, \
            "the fixture must look letterboxed to a darkness-only method"
        result = _occupancy_of(frames)
        assert result.passed, result.detail
        assert result.value["median_picture_fraction"] == 1.0

    def test_a_genuine_letterbox_still_fails(self):
        """The defect the gate exists for, measured the new way.

        Project 001's own shape - 608 picture rows of 1920 - built as
        real flat bars rather than as an absence of light.
        """
        frames = [_letterboxed_frame(round(H * 608 / 1920))] * 10
        result = _occupancy_of(frames)
        assert not result.passed
        assert result.severity == "error"
        assert "letterboxed and nothing asked for bars" in result.detail
        assert result.value["median_picture_fraction"] < 0.35
        assert result.value["max_top_bar_rows"] > 0
        assert result.value["max_bottom_bar_rows"] > 0

    def test_a_letterbox_appearing_mid_video_still_fails_one_geometry(self):
        """The subtler half: the picture must not change size mid-cut.

        Half the frames fill and half are barred, so the fill floor's
        median is borderline; the consistency half carries this on its own.
        """
        frames = [_gray_frame(1.0)] * 6 + [_letterboxed_frame(round(H * 0.6))] * 4
        result = _occupancy_of(frames)
        assert not result.passed
        assert "changes size within one declared framing" in result.detail

    def test_an_imperfectly_encoded_bar_is_still_a_bar(self):
        """A bar is not always pristine, and the bounds are measured for that.

        Project 001's shipped master is the loosest real bar measured:
        row means to 0.14, within-row standard deviations to 0.35. A bar
        speckled with pixels one level off black must not read as
        picture.
        """
        frames = [_letterboxed_frame(round(H * 0.32), speckle=8)] * 10
        bar_row = frames[0][0, 0, :].astype(np.float32)
        assert 0.0 < bar_row.mean() <= 0.14, "the fixture must be a real bar"
        result = _occupancy_of(frames)
        assert not result.passed
        assert "letterboxed and nothing asked for bars" in result.detail

    def test_the_bar_bounds_sit_between_a_real_bar_and_a_dark_picture(self):
        """Every bound is measured, and the gap each sits in is real.

        Level - below: real encoded bars measure a maximum row mean of
        0.000 at crf 18 and 23, 0.006 at crf 30, and 0.14 on 001's own
        shipped master. Above: the dark PICTURE that trips this measures
        8.56 to 9.33 on the captain's craft reference at 1121.0s.

        Variance - below: every row of a real encoded bar, 0.0 to 2.9.
        Above: every dark PICTURE row this gate used to fail, 3.80 and up
        here and 3.95 on 001's own master. That gap is real on our
        footage and CLOSES on the reference, whose dark picture rows
        measure 0.88 to 1.97 - which is why the level bound is the half
        that carries it there.

        Widening either bound past the picture side is how the false
        failure comes back.
        """
        assert render_qa.BAR_ROW_MAX_LUMA == 1.0
        assert render_qa.BAR_ROW_MAX_STD == 2.0
        assert render_qa.BAR_ROW_MAX_STEP == 2.0
        dark = _dark_textured_picture()
        assert dark.std(axis=1).min() > render_qa.BAR_ROW_MAX_STD
        assert dark.mean(axis=1).max() < LIT_LUMA_THRESHOLD

    def test_a_dark_row_is_only_a_bar_while_the_run_holds(self):
        """`_bar_rows` stops at the first row that is picture.

        Three ways a row leaves the bar, and each must end the walk where
        it occurs rather than skipping past it.
        """
        flat = np.zeros(20, dtype=np.float32)
        assert render_qa._bar_rows(flat, flat) == 20
        lit = flat.copy(); lit[7] = 200.0                    # a lit row
        assert render_qa._bar_rows(lit, flat) == 7
        textured = flat.copy(); textured[5] = 9.0            # structure along the row
        assert render_qa._bar_rows(flat, textured) == 5
        stepped = flat.copy(); stepped[11] = 9.0             # a step down the frame
        assert render_qa._bar_rows(stepped, flat) == 11

    def test_an_entirely_black_frame_carries_no_geometry(self):
        """A black frame has no picture whose size could have changed.

        `detect_black_frames` judges black against the beats the plan
        declared; entering it here as an occupancy of zero would report a
        declared fade as the picture changing size.
        """
        black = np.zeros((1, H, W), dtype=np.uint8)
        result = _occupancy_of([_gray_frame(1.0)] * 8 + [black] * 2)
        assert result.passed, result.detail
        assert result.value["black_frames_skipped"] == 2
        assert result.value["frames_sampled"] == 8

    def test_a_render_of_nothing_but_black_is_an_error_not_a_pass(self):
        black = np.zeros((1, H, W), dtype=np.uint8)
        result = _occupancy_of([black] * 5)
        assert not result.passed
        assert result.severity == "error"
        assert "entirely black" in result.detail

    def test_a_render_with_no_readable_frame_is_an_error_not_a_pass(self):
        with patch.object(render_qa, "_probe_video_size", return_value=(W, H)), \
                patch.object(render_qa, "_stream_raw_frames", return_value=iter([])):
            result = measure_frame_occupancy("master.mp4")
        assert not result.passed
        assert result.severity == "error"


# ═══ P2: somewhere in the frame there is colour ═══

class TestP2ChromaPresence:

    def test_it_reports_a_number_with_no_floor_declared(self):
        result = _chroma([_yuv_frame(27.0)] * 5)
        assert result.passed
        assert result.threshold["chroma_floor"] is None
        assert result.value["median_p99_chroma"] == pytest.approx(27.0, abs=0.5)
        assert "reporting only" in result.detail

    def test_a_grey_video_under_a_supplied_floor_does_not_fail_the_build(self):
        """The whole point of measuring before gating.

        The floor is an open captain decision, so the number is reported
        and the build survives. `meets_floor` carries the verdict for
        whoever eventually rules on it.
        """
        result = _chroma([_yuv_frame(27.0)] * 5, chroma_floor=60.0)
        assert result.passed
        assert result.value["meets_floor"] is False
        assert "REPORTED ONLY" in result.detail
        assert render_qa.CHROMA_PRESENCE_GATES is False

    def test_promoting_it_to_a_gate_is_one_boolean(self):
        result = _chroma([_yuv_frame(27.0)] * 5, chroma_floor=60.0, gate=True)
        assert not result.passed
        assert result.severity == "error"

    def test_the_percentile_sees_an_accent_a_mean_would_miss(self):
        """The reason the statistic is p99 and not a mean.

        A near-grey frame with one hot accent is a legitimate look - the
        captain's Punch Card reference is exactly that, a grey industrial
        gym scoring 3.5 mean chroma and 61.5 at the 99th percentile.
        """
        frame = _yuv_frame(2.0, accent_rows=int(H * 0.02), accent_chroma=90.0)
        mean_chroma = float(np.abs(frame[1].astype(float) - 128).mean())
        assert mean_chroma < 10  # would have failed the removed min_sat floor
        result = _chroma([frame] * 5, chroma_floor=60.0)
        assert result.value["meets_floor"] is True

    def test_letterbox_bars_cannot_dilute_the_answer(self):
        """The second thing wrong with the removed gate: it read whole frames.

        On 001 that meant 68% bar. Only LIT pixels count here.
        """
        frame = _yuv_frame(70.0)
        frame[0, : int(H * 0.7), :] = 0  # black bars over most of the frame
        result = _chroma([frame] * 5, chroma_floor=60.0)
        assert result.value["meets_floor"] is True

    def test_a_render_with_no_lit_pixel_reports_rather_than_failing(self):
        frame = _yuv_frame(50.0)
        frame[0, :, :] = 0
        result = _chroma([frame] * 3)
        assert result.passed
        assert result.severity == "warning"


class TestP2ReplacedTheMeanSaturationGate:
    """P2 REPLACES `min_sat: 10`; it does not sit beside it.

    Two checks answering the same question with different statistics is
    one check too many, and these two disagree: the captain's own two
    reference frames differ 9.3x in mean saturation, so the shipped floor
    would have rejected one of them.
    """

    @staticmethod
    def _histogram(satavg):
        def side_effect(cmd, **kwargs):
            from unittest.mock import MagicMock
            if 'format=duration' in cmd:
                return MagicMock(stdout='10.0\n', returncode=0)
            if any('signalstats' in str(arg) for arg in cmd):
                return MagicMock(stdout=json.dumps({"frames": [{"tags": {
                    "lavfi.signalstats.YAVG": "128",
                    "lavfi.signalstats.SATAVG": str(satavg)}}]}), returncode=0)
            return MagicMock(returncode=0)

        with patch('subprocess.run', side_effect=side_effect), \
                patch('os.path.exists', return_value=True):
            return analyze_color_histogram("dummy.mp4")

    def test_the_saturation_floor_is_gone_from_the_histogram_threshold(self):
        assert "min_sat" not in self._histogram(50).threshold

    def test_the_punch_card_reference_is_no_longer_rejected(self):
        """3.5 mean saturation - the captain's own selected reference."""
        result = self._histogram(3.5)
        assert result.passed, result.detail
        assert "saturation" not in result.detail.lower()

    def test_the_histogram_still_judges_exposure(self):
        def side_effect(cmd, **kwargs):
            from unittest.mock import MagicMock
            if 'format=duration' in cmd:
                return MagicMock(stdout='10.0\n', returncode=0)
            if any('signalstats' in str(arg) for arg in cmd):
                return MagicMock(stdout=json.dumps({"frames": [{"tags": {
                    "lavfi.signalstats.YAVG": "4",
                    "lavfi.signalstats.SATAVG": "50"}}]}), returncode=0)
            return MagicMock(returncode=0)

        with patch('subprocess.run', side_effect=side_effect), \
                patch('os.path.exists', return_value=True):
            result = analyze_color_histogram("dummy.mp4")
        assert not result.passed
        assert "dark" in result.detail


# ═══ P3: where someone speaks, the speech is above the bed ═══

SR = 4800  # a tenth of 48 kHz; the fit is scale-free


def _tone(seconds, freq, amplitude, phase=0.0):
    t = np.arange(int(seconds * SR)) / SR
    return (amplitude * np.sin(2 * np.pi * freq * t + phase)).astype(np.float32)


def _mix_fixture(music_db_under_speech):
    """A 4-second master: speech at 0 dB, music `n` dB under it."""
    speech = _tone(4.0, 220.0, 1.0)
    music = _tone(4.0, 55.0, 1.0)
    gain = 10 ** (-music_db_under_speech / 20.0)
    return (speech + gain * music).astype(np.float32), music


def _speech_above_bed(mix, music, automation, spine_blocks, **kwargs):
    with patch.object(render_qa, "_decode_mono", side_effect=[mix, music]):
        return measure_speech_above_bed("master.mp4", "music.wav", automation,
                                        kwargs.pop("music_offset_seconds", 0.0),
                                        spine_blocks=spine_blocks,
                                        sample_rate=SR, **kwargs)


BACKGROUND_WINDOW = [{"spine_block_position": 1, "timeline_start": 0.0,
                      "timeline_end": 4.0, "music_behavior": "background",
                      "target_level_db": -18}]
SPEECH_BLOCK = [{"position": 1, "block_type": "speech",
                 "music_behavior": "background"}]


class TestP3SpeechAboveBed:

    def test_a_correctly_ducked_bed_meets_the_plans_own_margin(self):
        mix, music = _mix_fixture(20.0)
        result = _speech_above_bed(mix, music, BACKGROUND_WINDOW, SPEECH_BLOCK)
        window = result.value["windows"][0]
        assert window["required_margin_db"] == 18.0
        assert window["margin_db"] >= 18.0
        assert window["meets_plan"] is True
        assert result.value["failing"] == 0

    def test_a_bed_over_the_voice_is_measured_but_does_not_fail_the_build(self):
        """001's shape. The mix has no delivery route, so this cannot pass
        whatever anyone configures - which is exactly why it reports."""
        mix, music = _mix_fixture(2.0)
        result = _speech_above_bed(mix, music, BACKGROUND_WINDOW, SPEECH_BLOCK)
        assert result.passed
        assert result.value["failing"] == 1
        assert "REPORTED ONLY" in result.detail
        assert render_qa.SPEECH_ABOVE_BED_GATES is False

    def test_promoting_it_to_a_gate_is_one_boolean(self):
        mix, music = _mix_fixture(2.0)
        result = _speech_above_bed(mix, music, BACKGROUND_WINDOW, SPEECH_BLOCK,
                                   gate=True)
        assert not result.passed
        assert result.severity == "error"

    def test_the_target_is_the_plans_number_not_a_convention(self):
        mix, music = _mix_fixture(10.0)
        prominent = [dict(BACKGROUND_WINDOW[0], music_behavior="prominent",
                          target_level_db=-6)]
        result = _speech_above_bed(mix, music, prominent, SPEECH_BLOCK)
        window = result.value["windows"][0]
        assert window["required_margin_db"] == 6.0
        assert window["meets_plan"] is True

    def test_a_non_speech_block_is_measured_but_not_judged(self):
        """A pacing beat has no voice to be above."""
        mix, music = _mix_fixture(2.0)
        result = _speech_above_bed(
            mix, music, BACKGROUND_WINDOW,
            [{"position": 1, "block_type": "transition_slot"}])
        assert result.value["judged"] == 0
        assert result.value["windows"][0]["judged"] is False
        assert result.value["windows"][0]["music_in_mix_db"] is not None

    def test_the_plan_is_read_from_music_automation_not_from_spine_blocks(self):
        """`_spine_blocks` names a behaviour but carries no dB, and P3
        judges against the plan's own number. The two halves agree on the
        vocabulary now (see tests/test_music_behavior_vocabulary.py); the
        one that carries the LEVEL is the one read here."""
        mix, music = _mix_fixture(20.0)
        result = _speech_above_bed(
            mix, music, BACKGROUND_WINDOW,
            [{"position": 1, "block_type": "speech",
              "music_behavior": "silent"}])
        assert result.value["windows"][0]["music_behavior"] == "background"
        assert result.value["windows"][0]["required_margin_db"] == 18.0

    def test_a_silent_window_is_judged_on_what_the_viewer_can_hear(self):
        """The gain column says whether the automation ran; the contribution
        column says whether the ear can tell. Enforce on contribution."""
        speech = _tone(8.0, 220.0, 1.0)
        music = _tone(8.0, 55.0, 1.0)
        loud = 10 ** (-6 / 20.0)
        mix = np.concatenate([
            (speech[:4 * SR] + loud * music[:4 * SR]),
            (speech[4 * SR:] + loud * music[4 * SR:]),
        ]).astype(np.float32)
        automation = [
            {"spine_block_position": 1, "timeline_start": 0.0,
             "timeline_end": 4.0, "music_behavior": "background",
             "target_level_db": -18},
            {"spine_block_position": 2, "timeline_start": 4.0,
             "timeline_end": 8.0, "music_behavior": "silent",
             "target_level_db": -96},
        ]
        blocks = [{"position": 1, "block_type": "speech"},
                  {"position": 2, "block_type": "speech"}]
        result = _speech_above_bed(mix, music, automation, blocks)
        silent = result.value["windows"][1]
        # The music is exactly as loud in the "silent" window as in the
        # background one, which is the 001 defect.
        assert silent["meets_plan"] is False
        assert result.value["silent_reference_db"] is not None

    def test_no_music_plan_means_nothing_to_measure(self):
        result = measure_speech_above_bed("master.mp4", "music.wav", [], 0.0)
        assert result.passed
        assert "nothing to measure" in result.detail

    # ── the bed does not start at the head of its own file ──
    #
    # Step 2.04 chooses which SECTION of the track plays and the A2 clip
    # carries it as `source_in`, so timeline second t is music file
    # second t + offset.  P3 sliced the music at the TIMELINE time, so on
    # project 001 - whose bed plays from 60.0 s - it fitted the wrong
    # minute of the track against the master: every correlation fell to
    # |r| <= 0.03, the fitted bed collapsed to -53..-128 dB and 0 of 8
    # speech windows meeting their target were reported as 8 of 8.
    # A gate that cannot fail.
    #
    # The fixture is the shape of the defect rather than a copy of 001:
    # the master carries the SECOND half of the music file, so fitting
    # from 0 correlates two unrelated stretches.

    def _offset_fixture(self, music_db_under_speech):
        """An 8 s music file whose SECOND 4 s are what the master carries."""
        speech = _tone(4.0, 220.0, 1.0)
        early = _tone(4.0, 55.0, 1.0)               # never used in the mix
        played = _tone(4.0, 350.0, 1.0, phase=0.7)  # the section that plays
        music = np.concatenate([early, played]).astype(np.float32)
        gain = 10 ** (-music_db_under_speech / 20.0)
        mix = (speech + gain * played).astype(np.float32)
        return mix, music

    def test_the_bed_is_fitted_at_the_section_that_actually_plays(self):
        mix, music = self._offset_fixture(2.0)   # a bed 2 dB under the voice
        result = _speech_above_bed(mix, music, BACKGROUND_WINDOW, SPEECH_BLOCK,
                                   music_offset_seconds=4.0)
        window = result.value["windows"][0]
        assert abs(window["correlation"]) > 0.5, (
            "the fit must find the music that is really in the master")
        assert window["margin_db"] == pytest.approx(2.0, abs=1.0)
        assert window["meets_plan"] is False
        assert result.value["music_offset_seconds"] == 4.0, (
            "the offset the margin was fitted at must be on the record")

    def test_fitting_from_zero_would_have_reported_a_bed_that_is_not_there(self):
        """The defect, kept executable: the same master read at offset 0."""
        mix, music = self._offset_fixture(2.0)
        result = _speech_above_bed(mix, music, BACKGROUND_WINDOW, SPEECH_BLOCK,
                                   music_offset_seconds=0.0)
        wrong = result.value["windows"][0]
        assert abs(wrong["correlation"]) < 0.1
        assert wrong["margin_db"] > 20.0, (
            "a bed fitted against the wrong minute reads as almost absent")
        assert wrong["meets_plan"] is True, (
            "which is how a failing mix passed: this is the reading the "
            "required argument exists to stop being the default")

    def test_the_offset_is_required_and_has_no_default(self):
        """A default of 'no offset' is the value that is silently wrong -
        the same reason `beat_grid.beat_positions` requires its own
        (AGENTS.md 10.5)."""
        import inspect
        params = inspect.signature(measure_speech_above_bed).parameters
        assert params["music_offset_seconds"].default is inspect.Parameter.empty

    def test_p3_does_not_run_when_the_offset_is_unknown(self):
        """`run_full_render_qa` has the file and the plan but no offset:
        it must decline to measure rather than assume the head of the file."""
        import inspect
        from library.tools.render_qa import run_full_render_qa
        src = inspect.getsource(run_full_render_qa)
        assert "music_offset_seconds is not None" in src


# ═══ P4: deliverable at platform loudness without clipping ═══

def _loudnorm(input_i, input_tp, **kwargs):
    from unittest.mock import MagicMock
    payload = f'{{\n"input_i": "{input_i}",\n"input_tp": "{input_tp}"\n}}'
    with patch('subprocess.run', return_value=MagicMock(stderr=payload,
                                                        returncode=0)):
        return measure_lufs("master.mp4", **kwargs)


class TestP4LoudnessAndPeak:

    def test_a_master_at_target_and_under_the_ceiling_passes(self):
        result = _loudnorm(-14.2, -1.4)
        assert result.passed
        assert result.value["lufs_passed"] and result.value["true_peak_passed"]

    def test_a_clipping_master_fails_even_when_loudness_is_perfect(self):
        """The peak half must carry the verdict on its own."""
        result = _loudnorm(-14.0, 1.85)
        assert not result.passed
        assert result.severity == "error"
        assert result.value["lufs_passed"] is True
        assert result.value["true_peak_passed"] is False
        assert "over the -1.0 dBTP ceiling" in result.detail

    def test_project_001s_master_fails_on_both_halves(self):
        """The exact regression. The old code set `severity = "warning"`
        for the peak ONLY when the LUFS check had already passed - and
        001's had not, so a +1.85 dBTP master was printed inside a detail
        string and dropped entirely."""
        result = _loudnorm(-17.47, 1.85)
        assert not result.passed
        assert result.value["lufs_passed"] is False
        assert result.value["true_peak_passed"] is False
        assert "-17.47" in result.detail and "+1.85" in result.detail

    def test_the_ceiling_is_the_manifests_own_declared_limiter(self):
        assert render_qa.DEFAULT_TRUE_PEAK_CEILING_DBTP == -1.0

    def test_the_loudness_tolerance_is_one_db(self):
        """At +/-2 a master can sit 2 dB quiet and pass, and no platform
        turns a quiet file up."""
        assert render_qa.DEFAULT_LUFS_TOLERANCE == 1.0
        assert not _loudnorm(-15.6, -2.0).passed
        assert _loudnorm(-14.9, -2.0).passed


# ═══ P6: no caption card flashes ═══

def _subtitles(durations):
    subs, t = [], 0.0
    for i, d in enumerate(durations):
        subs.append({"id": f"sub_{i:03d}", "text": "word",
                     "timeline_start": round(t, 3),
                     "timeline_end": round(t + d, 3)})
        t += d
    return {"subtitles": subs}


class TestP6NoFlashingCaptions:

    def test_a_card_under_half_a_second_fails(self):
        errors = _check_no_flash_captions(_subtitles([1.0, 0.182, 1.0]))
        assert len(errors) == 1
        assert "0.182s" in errors[0]
        assert "5.5 frames" in errors[0]

    def test_cards_at_the_planners_own_floor_pass(self):
        assert _check_no_flash_captions(_subtitles([0.7] * 10)) == []

    def test_the_soft_floor_is_counted_and_does_not_fail_on_its_own(self):
        """0.7s is the planner's declared display floor; only 0.5s fails,
        so a card between the two is reported inside the message rather
        than becoming a second verdict."""
        assert _check_no_flash_captions(_subtitles([0.6] * 10)) == []
        errors = _check_no_flash_captions(_subtitles([0.6] * 9 + [0.4]))
        assert "9 more sit under the 0.7s display floor" in errors[0]

    def test_both_thresholds_are_the_pipelines_own(self):
        assert MIN_CAPTION_DISPLAY_SECONDS == 0.5
        assert SOFT_CAPTION_DISPLAY_SECONDS == 0.7

    def test_a_manifest_with_no_subtitles_is_not_a_failure(self):
        assert _check_no_flash_captions({}) == []

    def test_a_card_that_ends_with_its_block_is_reported_not_failed(self):
        """The one case grouping cannot reach.

        A card is on screen until the next card's first word; the last
        card of a block has no next word, so it leaves when the block
        does. On 001 block 2 is "today is march 25th, 2026.", its final
        word is spoken for 0.21s, and the next block's captions begin in
        the same frame - no partition of those words, at any width, makes
        that card longer.
        """
        manifest = {
            "subtitles": [
                {"id": "sub_001", "text": "today is march",
                 "spine_block_position": 2,
                 "timeline_start": 5.40, "timeline_end": 6.64},
                {"id": "sub_002", "text": "25th,", "spine_block_position": 2,
                 "timeline_start": 6.64, "timeline_end": 8.17},
                {"id": "sub_003", "text": "2026.", "spine_block_position": 2,
                 "timeline_start": 8.17, "timeline_end": 8.38},
            ],
            "_spine_blocks": [
                {"position": 2, "timeline_start": 5.40, "timeline_end": 8.38},
            ],
        }
        assert _check_no_flash_captions(manifest) == []

    def test_a_runt_in_the_middle_of_a_block_still_fails(self):
        """The exemption is the narrowest one provable from the manifest.
        A short card with another card after it inside the same block was
        cut short by the GROUPING, and that is fixable."""
        manifest = {
            "subtitles": [
                {"id": "sub_001", "text": "today is march",
                 "spine_block_position": 2,
                 "timeline_start": 5.40, "timeline_end": 6.64},
                {"id": "sub_002", "text": "25th,", "spine_block_position": 2,
                 "timeline_start": 6.64, "timeline_end": 6.85},
                {"id": "sub_003", "text": "2026.", "spine_block_position": 2,
                 "timeline_start": 6.85, "timeline_end": 8.38},
            ],
            "_spine_blocks": [
                {"position": 2, "timeline_start": 5.40, "timeline_end": 8.38},
            ],
        }
        errors = _check_no_flash_captions(manifest)
        assert len(errors) == 1
        assert "sub_002" in errors[0]

    def test_the_exemption_needs_the_block_to_vouch_for_it(self):
        """Without `_spine_blocks` nothing can prove a card ends with its
        block, so nothing is exempted - the check does not degrade to
        trusting the plan."""
        manifest = {
            "subtitles": [
                {"id": "sub_001", "text": "a", "spine_block_position": 2,
                 "timeline_start": 0.0, "timeline_end": 1.0},
                {"id": "sub_002", "text": "b", "spine_block_position": 2,
                 "timeline_start": 1.0, "timeline_end": 1.21},
            ],
        }
        errors = _check_no_flash_captions(manifest)
        assert len(errors) == 1 and "sub_002" in errors[0]

    def test_the_exempted_cards_are_still_named_in_the_message(self):
        """Reported, not silently dropped: a build that has both kinds
        says so."""
        manifest = {
            "subtitles": [
                {"id": "sub_001", "text": "a", "spine_block_position": 2,
                 "timeline_start": 0.0, "timeline_end": 1.0},
                {"id": "sub_002", "text": "b", "spine_block_position": 2,
                 "timeline_start": 1.0, "timeline_end": 1.21},
                {"id": "sub_003", "text": "c", "spine_block_position": 2,
                 "timeline_start": 1.21, "timeline_end": 1.40},
            ],
            "_spine_blocks": [
                {"position": 2, "timeline_start": 0.0, "timeline_end": 1.40},
            ],
        }
        errors = _check_no_flash_captions(manifest)
        assert len(errors) == 1
        assert "sub_002" in errors[0]
        assert "end with their spine block" in errors[0]


# ═══ P7: no discretionary effect applied to everything ═══

def _vfx_manifest(effects, clips=8):
    return {
        "tracks": {"V1": {"clips": [{"label": f"c{i}"} for i in range(clips)]}},
        "vfx": [{"vfx_id": f"vfx_{i:03d}", "effect_type": e, "params": p}
                for i, (e, p) in enumerate(effects)],
    }


ZOOM_IN = ("slow_zoom_in", {"zoom_start": 1.0, "zoom_end": 1.03})
ZOOM_OUT = ("slow_zoom_out", {"zoom_start": 1.03, "zoom_end": 1.0})


class TestP7NoEffectOnEverything:

    def test_one_family_on_every_clip_fires(self):
        """Project 001: slow_zoom on 8 of 8 clips, two parameter sets, one
        of them the other's mirror."""
        errors = _check_no_effect_on_everything(
            _vfx_manifest([ZOOM_IN] * 5 + [ZOOM_OUT] * 3))
        assert len(errors) == 1
        assert "slow_zoom" in errors[0]
        assert "covers all 8 V1 clips" in errors[0]

    def test_mirrored_directions_are_one_decision_not_two(self):
        """Otherwise a planner defeats P7 by alternating direction."""
        errors = _check_no_effect_on_everything(
            _vfx_manifest([ZOOM_IN, ZOOM_OUT] * 4))
        assert errors and "slow_zoom" in errors[0]

    def test_leaving_one_clip_alone_passes(self):
        """100% is the exact boundary between chosen and applied."""
        assert _check_no_effect_on_everything(
            _vfx_manifest([ZOOM_IN] * 4 + [ZOOM_OUT] * 3)) == []

    def test_genuinely_varied_parameters_pass(self):
        """An effect on every clip with a different value each time IS a
        decision, made many times."""
        effects = [("slow_zoom_in", {"zoom_end": 1.0 + i / 100})
                   for i in range(8)]
        assert _check_no_effect_on_everything(_vfx_manifest(effects)) == []

    def test_it_is_not_a_floor_and_never_becomes_one(self):
        """The 2026-08-20 ruling removed the creative floors outright. A
        piece with no VFX at all, or with two, is not a defect."""
        assert _check_no_effect_on_everything(_vfx_manifest([])) == []
        assert _check_no_effect_on_everything(
            _vfx_manifest([ZOOM_IN, ZOOM_OUT])) == []

    def test_a_video_too_short_to_have_a_pattern_is_left_alone(self):
        assert _check_no_effect_on_everything(
            _vfx_manifest([ZOOM_IN, ZOOM_OUT], clips=2)) == []

    def test_a_drawn_transition_on_every_cut_fires(self):
        manifest = {
            "tracks": {"V1": {"clips": [{"label": f"c{i}"} for i in range(6)]}},
            "transitions": [{"transition_id": f"t{i}",
                             "transition_type": "defocus",
                             "duration_frames": 10} for i in range(5)],
        }
        errors = _check_no_effect_on_everything(manifest)
        assert len(errors) == 1
        assert "defocus" in errors[0] and "covers all 5 planned cuts" in errors[0]

    def test_a_video_of_nothing_but_hard_cuts_is_not_an_effect(self):
        """`CUT_TYPES` draw NOTHING - the vocabulary says so - so every cut
        being a hard cut is the ABSENCE of an effect, and the transition
        handoff asks for exactly that ("hard cuts dominate"). Failing a
        restrained edit here would be the creative ceiling this check's own
        note forbids it from becoming."""
        for kind in ("hard_cut", "jump_cut", "match_cut"):
            manifest = {
                "tracks": {"V1": {"clips": [{"label": f"c{i}"}
                                            for i in range(6)]}},
                "transitions": [{"transition_id": f"t{i}",
                                 "transition_type": kind,
                                 "duration_frames": 0} for i in range(5)],
            }
            assert _check_no_effect_on_everything(manifest) == [], kind

    def test_the_denominator_is_the_transitions_planned_not_the_v1_gaps(self):
        """A spine with transition slots has more cut points than V1 has
        clips. Project 001 plans 13 transitions across 9 V1 clips, and
        counting a type against `len(v1_clips) - 1` declared its 8 hard
        cuts to be "all 8 of them" and failed the build."""
        types = (["hard_cut"] * 8 + ["defocus"] * 3 + ["jump_cut"] * 2)
        manifest = {
            "tracks": {"V1": {"clips": [{"label": f"c{i}"} for i in range(9)]}},
            "transitions": [{"transition_id": f"t{i}", "transition_type": k,
                             "duration_frames": 15 if k == "defocus" else 0}
                            for i, k in enumerate(types)],
        }
        assert _check_no_effect_on_everything(manifest) == []

    def test_001s_transition_mix_passes(self):
        """4 hard_cut, 3 defocus, 3 jump_cut over 7 cuts - no type at 100%.
        P7 must not fire on a video that varied its cuts."""
        types = ["hard_cut"] * 4 + ["defocus"] * 3 + ["jump_cut"] * 3
        manifest = {
            "tracks": {"V1": {"clips": [{"label": f"c{i}"} for i in range(8)]}},
            "transitions": [{"transition_id": f"t{i}", "transition_type": k,
                             "duration_frames": 10}
                            for i, k in enumerate(types)],
        }
        assert _check_no_effect_on_everything(manifest) == []


# ═══ the gates reach the build verdict ═══

class TestTheGatesActuallyDecide:
    """A check that returns `passed=False` and is read by nobody is the
    `smart_reframe` failure mode: it prints a verdict on every run and
    changes nothing. These assert the wiring, not the measurement.
    """

    @staticmethod
    def _validate(qa_results):
        from library.steps.step_6_02_validate_output import bridge as validate

        manifest = {"project": {"resolution": [1080, 1920], "frame_rate": 30.0,
                                "duration_seconds": 10.0},
                    "tracks": {}, "subtitles": []}
        with patch.object(validate, "run_full_render_qa",
                          return_value=list(qa_results)), \
                patch.object(validate.os.path, "exists", return_value=True), \
                patch.object(validate.os.path, "getsize", return_value=5_000_000), \
                patch("builtins.open", create=True):
            return validate.validate_output(
                {"output_path": "/tmp/master.mp4"}, manifest)

    @staticmethod
    def _result(metric, passed, severity="error"):
        return render_qa.RenderQAResult(metric, passed, None, None, severity,
                                        f"{metric} says {passed}")

    def test_p1_failing_makes_the_render_undeliverable(self):
        verdict = self._validate([self._result("frame_occupancy", False)])
        assert verdict["distribution_ready"] is False
        assert verdict["checks"]["framing"]["pass"] is False
        assert verdict["critical_checks_passed"] is False

    def test_p4_failing_makes_the_render_undeliverable(self):
        verdict = self._validate([self._result("lufs", False)])
        assert verdict["distribution_ready"] is False
        assert verdict["checks"]["audio_levels"]["pass"] is False

    def test_p2_and_p3_never_touch_the_verdict(self):
        """They pass by construction today; if a future edit makes them
        report `passed=False`, that must still not fail a build until the
        boolean says so."""
        verdict = self._validate([
            self._result("chroma_presence", False, "warning"),
            self._result("speech_above_bed", False, "warning"),
        ])
        assert verdict["distribution_ready"] is True
        assert all(c["pass"] for c in verdict["checks"].values())

    def test_the_manifest_supplies_what_p1_and_p3_need(self):
        from library.steps.step_6_02_validate_output import bridge as validate

        manifest = {
            "project": {"frame_rate": 30.0},
            "tracks": {
                "V1": {"clips": [
                    {"framing_delivered": 1.0,
                     "timeline_in_frame": 0, "timeline_out_frame": 30},
                    {"framing_delivered": 1.0,
                     "timeline_in_frame": 30, "timeline_out_frame": 60}]},
                "V2": {"clips": [{"framing_delivered": 0.0,
                                  "timeline_in_frame": 60, "timeline_out_frame": 90}]},
                "A2": {"clips": [{"source_file": __file__}]},
            },
            "audio_mix": {"music_automation": [{"timeline_start": 0.0}]},
        }
        assert [(s.start, s.end, s.intent)
                for s in validate._framing_spans(manifest)] == [
            (0.0, 1.0, 1.0), (1.0, 2.0, 1.0), (2.0, 3.0, 0.0)]
        music, automation, offset = validate._music_bed(manifest)
        assert music == __file__
        assert len(automation) == 1
        assert offset == 0.0

    def test_framing_spans_follow_frames_not_drifted_seconds(self):
        """D4: the rendered timeline is frame-quantised, so the seconds
        the plan wrote drift from the cut by up to a frame. The spans
        must be built from `timeline_*_frame / fps`: the report's cut
        at frame 1650 is 55.000s exactly while the seconds say 55.001,
        and the sample at t=55.0 belongs to the cutaway starting there
        (half-open), not to the letterboxed V1 the drifted seconds
        still cover. Every expected value below is frame / fps, never
        a copied second."""
        from library.steps.step_6_02_validate_output import bridge as validate

        fps = 30.0
        v1_in, v1_out = 1364, 1650
        v2_in, v2_out = 1650, 1740
        manifest = {
            "project": {"frame_rate": fps},
            "tracks": {
                "V1": {"clips": [{"framing_delivered": 0.0,
                                  "timeline_in": 45.462,
                                  "timeline_out": 55.001,
                                  "timeline_in_frame": v1_in,
                                  "timeline_out_frame": v1_out}]},
                "V2": {"clips": [{"framing_delivered": 1.0,
                                  "timeline_in": 55.001,
                                  "timeline_out": 58.001,
                                  "timeline_in_frame": v2_in,
                                  "timeline_out_frame": v2_out}]},
            },
        }
        spans = validate._framing_spans(manifest)
        assert [(s.start, s.end, s.intent) for s in spans] == [
            (v1_in / fps, v1_out / fps, 0.0),
            (v2_in / fps, v2_out / fps, 1.0)]
        assert render_qa._intent_at(spans, v2_in / fps) == 1.0

    def test_a_missing_music_file_disables_p3_rather_than_guessing(self):
        from library.steps.step_6_02_validate_output import bridge as validate

        music, automation, offset = validate._music_bed(
            {"tracks": {"A2": {"clips": [{"source_file": "/nope/absent.wav"}]}},
             "audio_mix": {"music_automation": [{"timeline_start": 0.0}]}})
        assert music is None and automation == [] and offset is None


# ═══ where P4's verdict lives, and where it must not ═══

class TestP4DoesNotCollideWithTheBuildVerdict:
    """Two verdicts exist, and they answer different questions.

    `build_verification.derive_verification_verdict` (step 6.01) asks
    whether the TIMELINE was built as the manifest asked, off `timeline_qa`
    station reports. Under the captain's ruling its outcome is advisory:
    `results["success"]` is deliberately not gated on it (see
    `test_qa_failures_are_not_fatal_yet`).

    `render_qa` (step 6.02) asks whether the EXPORTED FILE is deliverable,
    and its outcome gates - `distribution_ready` False, exit code 1.

    P4 belongs to the second, and is the only opinion anywhere in the
    pipeline about delivered loudness or peak: `timeline_qa.verify_audio`
    says in its own docstring that it does not check levels, because
    nothing sets one. So the two do not decide the same thing twice.

    The hazard is that they COULD be coupled by accident: a
    `RenderQAResult` also carries `.passed`, so routing render QA into the
    station list would typecheck, run, and silently demote a clipping
    master from a failed build to an advisory note. These pin the
    boundary.
    """

    def test_no_render_qa_result_reaches_the_build_verdict(self):
        import os
        renderer = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "library", "steps", "step_6_01_render", "resolve_build_timeline.py")
        with open(renderer, encoding="utf-8") as fh:
            source = fh.read()
        assert "render_qa" not in source, (
            "resolve_build_timeline must not feed render QA into its station "
            "list: derive_verification_verdict's outcome is advisory, so a "
            "P4 failure routed there would stop failing the build")

    def test_the_timeline_audio_station_still_declines_to_judge_levels(self):
        from library.tools.timeline_qa import verify_audio
        assert "does NOT check per-track levels" in verify_audio.__doc__, (
            "if the timeline station starts judging levels there are two "
            "authorities on the same question and they can disagree")

    def test_the_two_verdicts_use_different_result_types(self):
        import os
        import sys
        step_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "library", "steps", "step_6_01_render")
        if step_dir not in sys.path:
            sys.path.insert(0, step_dir)
        from build_verification import derive_verification_verdict

        # A station report is not a RenderQAResult and vice versa; the
        # station verdict is computed only from the former.
        from types import SimpleNamespace
        assert derive_verification_verdict(
            [SimpleNamespace(passed=True), SimpleNamespace(passed=False)]) is False
        assert derive_verification_verdict([]) is True
        assert not hasattr(render_qa.RenderQAResult, "station")
