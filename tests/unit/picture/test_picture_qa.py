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
from __future__ import annotations
import json
from unittest.mock import patch
import numpy as np
import pytest
from library.tools import render_qa
from tests.promotion_test_helpers import install_measured_draw_gain_probe
from library.tools.manifest_validator import (
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
from library.tools.render_qa import (
    _bar_rows,
)
import os
import sys
from unittest.mock import MagicMock
from library.tools.segment_renderer import (
    render_segment
)
from library.tools import proof_scope
from library.tools.reel_conformance_verifier import (
    FindingClass,
    PlannedPlacement,
    ReelPlan,
    ReelTimeline,
    TimelineItem,
    check_plan_describes_timeline,
    verify_reel,
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

    def test_a_declared_letterbox_is_exempt_from_the_fill_floor(self):
        """A series that declares bars gets them - the synthetic cinematic
        copy does.

        There is no channel-wide number for how much frame a deliberately
        inset picture should occupy, so inventing one here would answer a
        brand decision. The consistency half still applies.
        """
        result = _occupancy([0.34] * 10, framing_intents=[0.0] * 10)
        assert result.passed, result.detail
        assert result.threshold["fill_floor_applies"] is False
        assert result.threshold["consistency_applies"] is True

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


    # ── black is not the same thing as dark (issue #221) ──
    # The check called a row "lit" above a luma threshold and read every
    # other row as bar, which fails any render carrying a dim shot - most
    # of them.  These are the cases that separates, in both directions.

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


# ═══ P2: somewhere in the frame there is colour ═══

class TestP2ChromaPresence:

    def test_it_reports_a_number_with_no_floor_declared(self):
        result = _chroma([_yuv_frame(27.0)] * 5)
        assert result.passed
        assert result.threshold["chroma_floor"] is None
        assert result.value["median_p99_chroma"] == pytest.approx(27.0, abs=0.5)
        assert "reporting only" in result.detail

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
                patch('os.path.exists', return_value=True), \
                patch('os.path.getsize', return_value=2048):
            return analyze_color_histogram("dummy.mp4")

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
                patch('os.path.exists', return_value=True), \
                patch('os.path.getsize', return_value=2048):
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

    def test_word_gap_recovery_is_measured_outside_speech_separation(self):
        """Music rising in word gaps must not lower the measured speech margin."""
        speech = np.zeros(4 * SR, dtype=np.float32)
        speech[:SR] = _tone(1.0, 220.0, 1.0)
        speech[3 * SR:] = _tone(1.0, 220.0, 1.0)
        music = _tone(4.0, 55.0, 1.0)
        word_gain = 10 ** (-18.0 / 20.0)
        gap_gain = 10 ** (-14.0 / 20.0)
        music_gain = np.full(4 * SR, word_gain, dtype=np.float32)
        music_gain[SR:3 * SR] = gap_gain
        mix = speech + music * music_gain
        automation = [{
            "spine_block_position": 1,
            "timeline_start": 0.0, "timeline_end": 4.0,
            "music_behavior": "background",
            "target_level_db": -18.0,
            "separation_target_db": 18.0,
            "word_intervals": [[0.0, 1.0], [3.0, 4.0]],
            "word_gap_level_db": -14.0,
            "word_gap_release_ms": 0.0,
        }]

        result = _speech_above_bed(
            mix, music, automation, SPEECH_BLOCK)
        window = result.value["windows"][0]

        assert window["measurement_scope"] == "spoken_words"
        assert window["margin_db"] == pytest.approx(18.0, abs=0.1)
        assert window["whole_block_margin_db"] < 18.0
        assert window["word_gap_recovery_db"] == pytest.approx(4.0, abs=0.1)
        assert window["required_gap_recovery_db"] == 4.0
        assert window["meets_plan"] is True

    def test_a_bed_over_the_voice_is_measured_but_does_not_fail_the_build(self):
        """001's shape. The mix has no delivery route, so this cannot pass
        whatever anyone configures - which is exactly why it reports."""
        mix, music = _mix_fixture(2.0)
        result = _speech_above_bed(mix, music, BACKGROUND_WINDOW, SPEECH_BLOCK)
        assert result.passed
        assert result.value["failing"] == 1
        assert "REPORTED ONLY" in result.detail
        assert render_qa.SPEECH_ABOVE_BED_GATES is False

    def test_the_target_is_the_plans_number_not_a_convention(self):
        mix, music = _mix_fixture(10.0)
        prominent = [dict(BACKGROUND_WINDOW[0], music_behavior="prominent",
                          target_level_db=-6)]
        result = _speech_above_bed(mix, music, prominent, SPEECH_BLOCK)
        window = result.value["windows"][0]
        assert window["required_margin_db"] == 6.0
        assert window["meets_plan"] is True

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

# ═══ P4: deliverable at platform loudness without clipping ═══

def _loudnorm(input_i, input_tp, **kwargs):
    from unittest.mock import MagicMock
    payload = f'{{\n"input_i": "{input_i}",\n"input_tp": "{input_tp}"\n}}'
    with patch('subprocess.run', return_value=MagicMock(stderr=payload,
                                                        returncode=0)):
        return measure_lufs("master.mp4", **kwargs)


class TestP4LoudnessAndPeak:


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

    def test_it_is_not_a_floor_and_never_becomes_one(self):
        """The 2026-08-20 ruling removed the creative floors outright. A
        piece with no VFX at all, or with two, is not a defect."""
        assert _check_no_effect_on_everything(_vfx_manifest([])) == []
        assert _check_no_effect_on_everything(
            _vfx_manifest([ZOOM_IN, ZOOM_OUT])) == []

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


# --------------------------------------------------------------------------
# From test_dark_picture_is_not_a_bar.py
#
# A dark picture is not a black bar, and a composition is not a conform.
#
# A bar carries no light at all (`BAR_ROW_MAX_LUMA`), so a flat graded
# shadow is picture; a picture inset in black on all four sides is a
# composition, never a conform; a near-black fade carries no geometry.
# Measurements behind each bound:
# `docs/RULE_EVIDENCE.md#a-dark-picture-is-not-a-black-bar`.

W_2, H_2 = 1080, 1920

# The reference's own numbers at 1121.0s: a row mean of 8.56 to 9.33 and
# a within-row standard deviation of 0.88 to 1.97.
SHADOW_MEAN = 9.0
SHADOW_ROWS = 640


def _shadow_rows(rows: int, width: int = W_2):
    """Dark picture that is FLAT enough to have fooled the variance half."""
    y, x = np.mgrid[0:rows, 0:width].astype(np.float32)
    # +/- one luma level of structure, which is all an 8-bit shadow has.
    return SHADOW_MEAN + np.sin(x / 7.0) + np.sin(y / 11.0) * 0.4


def _dark_topped_picture():
    """A full-bleed frame whose top is a graded shadow. All picture."""
    luma = np.full((H_2, W_2), 120.0, dtype=np.float32)
    luma[:SHADOW_ROWS] = _shadow_rows(SHADOW_ROWS)
    return np.round(luma).astype(np.uint8)[None, :, :]


def _flat_black_bars(picture_rows: int, level: float = 0.0):
    """A conform letterbox: black bars, picture spanning the full width."""
    luma = np.full((H_2, W_2), level, dtype=np.float32)
    top = (H_2 - picture_rows) // 2
    luma[top:top + picture_rows] = 120.0 + np.sin(
        np.mgrid[0:picture_rows, 0:W_2][1] / 7.0) * 20.0
    return np.round(luma).astype(np.uint8)[None, :, :]


def _inset_in_black(picture_rows: int, picture_cols: int):
    """A composition: a picture inset in black on all four sides."""
    luma = np.zeros((H_2, W_2), dtype=np.float32)
    top = (H_2 - picture_rows) // 2
    left = (W_2 - picture_cols) // 2
    luma[top:top + picture_rows, left:left + picture_cols] = (
        120.0 + np.sin(np.mgrid[0:picture_rows, 0:picture_cols][1] / 7.0) * 20.0)
    return np.round(luma).astype(np.uint8)[None, :, :]


def _measure(frames, spans=None, **kwargs):
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(render_qa, "_probe_video_size", lambda p: (W_2, H_2))
        mp.setattr(render_qa, "_stream_raw_frames",
                   lambda *a, **k: iter(frames))
        return measure_frame_occupancy("master.mp4", framing_spans=spans,
                                       **kwargs)


# ── 1. a graded shadow is picture ──


def test_darkness_alone_would_still_eat_the_shadow():
    """Why the level bound and not a wider variance bound.

    Run the same rows under the threshold the walk used to take and they
    are consumed; under the level a bar really measures they are not.
    """
    shadow = _shadow_rows(SHADOW_ROWS)
    means, stds = shadow.mean(axis=1), shadow.std(axis=1)
    assert _bar_rows(means, stds, max_luma=LIT_LUMA_THRESHOLD) == SHADOW_ROWS
    assert _bar_rows(means, stds) == 0


# ── the bar itself is unchanged ──


# ── 2. a composition is not a conform ──

def test_a_picture_inset_in_black_on_four_sides_is_counted_out():
    lit = np.full((1, H_2, W_2), 140, dtype=np.uint8)
    result = _measure([lit] * 8 + [_inset_in_black(900, 600)] * 4)
    assert result.passed, result.detail
    assert result.value["inset_frames_skipped"] == 4
    assert result.value["frames_sampled"] == 8
    assert result.value["spread"] == 0.0
    assert "composition and not a conform" in result.detail


# ── a frame with no picture at all ──

def test_a_frame_that_is_black_but_not_zero_carries_no_geometry():
    """The black test is the pipeline's own, not the bar walk by proxy.

    A fade held at luma 2 is black to any viewer and to `blackdetect`,
    and the level bound means the bar walk no longer eats it - so the
    black predicate has to be asked directly or the fade reads as a full
    frame of picture and moves the spread.
    """
    lit = np.full((1, H_2, W_2), 140, dtype=np.uint8)
    nearly_black = np.full((1, H_2, W_2), 2, dtype=np.uint8)
    result = _measure([lit] * 8 + [nearly_black] * 3)
    assert result.passed, result.detail
    assert result.value["black_frames_skipped"] == 3
    assert result.value["frames_sampled"] == 8


# --------------------------------------------------------------------------
# From test_perceptual_qa.py
#
# The perceptual quality gate (Q8), and the four constraints on it.
#
# Every gate in this pipeline is technical, and none would catch a
# letterboxed edit with the subject's head cropped off. This one watches the
# render. It is OBSERVATION ONLY and must stay that way until there is
# evidence about its false-positive rate.
#
# The model itself is not exercised here - loading a 12B model in CI is not
# sensible. What is exercised: the parsing, the variance discipline, and the
# wiring that stopped any of it running at all.

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools.perceptual_qa import (
    dimension_variance,
    parse_verdict,
)

# The three verdicts the model actually returned on the calibration
# frames, copied verbatim including the code fence it uses about half the
# time. Fabricating cleaner replies would only prove the fixture.
REAL_LETTERBOX = '''```json
{"fills_frame": true, "black_bars": "top_and_bottom", "main_subject_fully_visible": true, "what_is_wrong": "The video contains large black bars at the top and bottom, failing to fill the screen."}
```'''
REAL_SUBJECT_CUT = '''```json
{"fills_frame": true, "black_bars": "none", "main_subject_fully_visible": false, "what_is_wrong": "The main subject is cut off by the left edge of the frame."}
```'''
REAL_CLEAN = '''{"fills_frame": true, "black_bars": "none", "main_subject_fully_visible": true, "what_is_wrong": ""}'''


# ─────────────────────────────────────────────────────────
# Constraint 1: structured, so verdicts compare
# ─────────────────────────────────────────────────────────

def test_a_fenced_verdict_parses_and_prose_is_a_parse_failure():
    """The model fences its JSON about half the time."""
    v = parse_verdict(REAL_LETTERBOX, frame=30)
    assert not v.parse_error
    assert v.answers["black_bars"] == "top_and_bottom"
    v = parse_verdict("The frame looks quite nice to me.", frame=1)
    assert v.parse_error
    assert not v.clean, "an unparseable verdict must not read as a pass"


# ─────────────────────────────────────────────────────────
# Constraint 2: what is wrong and why, never a score
# ─────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────
# Constraint 3: every dimension must discriminate
# ─────────────────────────────────────────────────────────

def _real_verdicts():
    return [parse_verdict(r, frame=i * 30) for i, r in enumerate(
        (REAL_LETTERBOX, REAL_SUBJECT_CUT, REAL_CLEAN))]


def test_the_kept_dimensions_discriminate_on_real_verdicts():
    variance = dimension_variance(_real_verdicts())
    for key in ("black_bars", "main_subject_fully_visible", "what_is_wrong"):
        assert variance.get(key, 0) > 1, (
            f"{key} gave the same answer on every frame, so it ranks nothing")


def test_findings_only_fire_on_the_bad_frames():
    letterbox, cut, clean = _real_verdicts()
    assert any(f.dimension == "black_bars" for f in letterbox.findings)
    assert any(f.dimension == "main_subject_fully_visible" for f in cut.findings)
    assert not clean.findings, "the correct frame must produce no findings"


def test_an_unanswered_dimension_does_not_read_as_clean():
    """The vacuous-gate guard.

    Measured on real footage: asked about a letterboxed frame the model
    replied `main__subject_fully_visible` - two underscores. The dimension
    silently vanished from the verdict and the frame read clean.
    """
    garbled = ('{"black_bars": "top_and_bottom", '
               '"main__subject_fully_visible": true, '
               '"text_legible": true, "what_is_wrong": "bars"}')
    v = parse_verdict(garbled, frame=0)
    assert not v.parse_error
    assert "main_subject_fully_visible" in v.unanswered
    assert not v.clean, "a question the model did not answer is not a pass"


# ─────────────────────────────────────────────────────────
# Constraint 4: observation only
# ─────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────
# The wiring that stopped any of this running
# ─────────────────────────────────────────────────────────

def test_the_router_finds_clips_where_they_actually_live():
    """plan_qa_checks read manifest["clips"], which has never existed.

    Every render reported zero frame grabs, and that read as a clean
    visual QA pass rather than an absent one.
    """
    from library.tools.visual_qa_router import plan_qa_checks

    manifest = {
        "project": {"frame_rate": 30},
        "tracks": {
            "V1": {"clips": [
                {"label": "a", "source_file": "a.mov", "source_in": 0.0, "source_out": 2.0,
                 "timeline_in_frame": 0, "timeline_out_frame": 60},
                {"label": "b", "source_file": "b.mov", "source_in": 0.0, "source_out": 2.0,
                 "timeline_in_frame": 60, "timeline_out_frame": 120},
            ]},
            "V2": {"clips": [
                {"label": "c", "source_file": "c.mov", "source_in": 0.0, "source_out": 2.0,
                 "timeline_in_frame": 30, "timeline_out_frame": 90},
            ]},
        },
    }
    plan = plan_qa_checks(manifest, phase="post_build")
    assert len(plan.frame_grabs) == 3, (
        "the router must plan a frame grab per placed clip; it read a "
        "top-level 'clips' key that compile_manifest has never written")


# ─────────────────────────────────────────────────────────
# The hang: a fixed key-name bug switched on a dormant path
# (history: docs/evidence/perceptual_qa.md)
# ─────────────────────────────────────────────────────────

def test_perceptual_observation_is_off_by_default(monkeypatch):
    from library.tools.visual_qa_router import (
        PERCEPTUAL_QA_ENV, perceptual_qa_enabled, run_perceptual_observation)
    monkeypatch.delenv(PERCEPTUAL_QA_ENV, raising=False)
    assert perceptual_qa_enabled() is False
    manifest = {"tracks": {"V1": {"clips": [
        {"timeline_in_frame": 0, "timeline_out_frame": 60}]}}}
    # Must not touch Resolve or the model when it is off.
    assert run_perceptual_observation(None, None, None, manifest) is None


def test_importing_the_router_does_not_pull_in_the_vision_model():
    """A module that costs gigabytes to import poisons every consumer.

    `vision_model` imports mlx_vlm; the router must not drag it in just
    because something wanted `plan_qa_checks`.
    """
    import subprocess
    probe = (
        "import sys;"
        "import library.tools.visual_qa_router as r;"
        "print('mlx_vlm' in sys.modules, 'library.tools.vision_model' in sys.modules)"
    )
    out = subprocess.run([sys.executable, "-c", probe], cwd=PROJECT_ROOT,
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-500:]
    assert out.stdout.strip() == "False False", (
        f"importing visual_qa_router pulled in the model stack: {out.stdout!r}")


# --------------------------------------------------------------------------
# From test_visual_qa.py

# --- Mocks ---
@pytest.fixture
def mock_resolve():
    resolve = MagicMock()
    resolve.GetCurrentPage.return_value = "edit"
    return resolve

@pytest.fixture
def mock_timeline():
    tl = MagicMock()
    tl.GetUniqueId.return_value = "test-timeline-uid"
    tl.GetName.return_value = "Test Timeline"
    return tl

@pytest.fixture
def mock_project(mock_timeline):
    project = MagicMock()
    project.IsRenderingInProgress.side_effect = [True, False]
    project.GetCurrentTimeline.return_value = mock_timeline
    project.AddRenderJob.return_value = "job-1"
    # `render_segment` reads the queue back before starting, because
    # Resolve ignores MarkIn/MarkOut unless SelectAllFrames is False
    # and a "single frame" then renders the whole timeline. A bare
    # MagicMock returns a Mock here, which is not a list of dicts - so
    # the queue is spelled out rather than auto-specced.
    project.GetRenderJobList.return_value = [
        {"JobId": "job-1", "MarkIn": 0, "MarkOut": 10}]
    return project


# --- 1. segment_renderer.py tests ---


@patch("os.makedirs")
@patch("library.tools.segment_renderer._find_rendered_file", return_value="/tmp/qa_segment_0_10.mov")
@patch("os.path.getsize", return_value=100) # Small size
def test_render_segment_small_file(mock_size, mock_find, mock_makedirs, mock_resolve, mock_project, mock_timeline):
    res = render_segment(mock_resolve, mock_project, mock_timeline, 0, 10, output_dir="/tmp")
    assert res.success is False
    assert "too small" in res.error


# --- 2. visual_qa_router.py tests ---


# --- 3. qa_feedback_loop.py tests ---


# --- 4. visual_qa_prompts.py tests ---


# --------------------------------------------------------------------------
# From test_proof_scope.py
#
# Proof in proportion to the edit, where the build reads it.
#
# The 2026-09-11 round earned its 35-still, full-census, double-build
# proof: it was a brand new mechanism. As a standing requirement for
# every small edit that burden costs 10-15 minutes a round. The rule:
# a new mechanism earns the full burden; a re-run of an established
# one earns stills for the regions that actually changed, and a census
# only when something disagrees. The kind never scales - stills are
# real pixels at every level; only how much is proven does.
#
# Proven here as the build reads it: `scope_for_build` maps the
# mechanical inputs (plan newness off provenance, declarations
# attached, the pre-build census verdict) to a scope, and the rebuild
# prints it and records it on its record. It is guidance, never a
# gate - nothing here can fail a build.

REELS = ["Reel 01 - hook (final)", "Reel 02 - promise (final)"]


def _scope(**over):
    base = {"reels": REELS, "plan_is_new": False,
            "shared_declarations": [], "disagreement_reported": False,
            "reels_without_prior_proof": []}
    base.update(over)
    return proof_scope.scope_for_build(**base)


def test_a_new_plan_earns_the_full_burden():
    scope = _scope(plan_is_new=True)

    assert scope["level"] == "FULL"
    assert scope["stills"] == REELS
    assert scope["census"] == "full"
    assert any("no recorded build" in reason
               for reason in scope["reasons"])


def test_a_rerun_owes_stills_only_for_unproven_reels():
    scope = _scope(reels_without_prior_proof=[REELS[1]])

    assert scope["level"] == "REDUCED"
    assert scope["stills"] == [REELS[1]]
    assert scope["census"] == "touched-only"

    # A fully proven re-run owes no post-build census at all.
    scope = _scope()
    assert scope["level"] == "REDUCED"
    assert scope["stills"] == []
    assert scope["census"] == "pre-build-only"
    text = proof_scope.render_scope(scope)
    assert "no post-build census" in text or "pre-build report" in text


def test_plan_newness_reads_provenance_not_memory(tmp_path):
    """`build_inputs` compares the plan hash against the provenance
    record: same hash means a re-run, whatever anyone remembers."""
    review = tmp_path / "review"
    review.mkdir()
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"reels": REELS}), encoding="utf-8")
    from library.tools.plan_provenance import (
        plan_content_hash)

    content_hash = plan_content_hash(str(plan))
    (review / "plan_provenance.json").write_text(json.dumps(
        {"plan_content_hash": content_hash,
         "built_reels": REELS,
         "caption_hashes": {REELS[0]: "abc"}}), encoding="utf-8")

    inputs = proof_scope.build_inputs(str(review), str(plan), REELS)

    assert inputs["plan_is_new"] is False
    assert inputs["reels_without_prior_proof"] == [REELS[1]]

    plan.write_text(json.dumps({"reels": REELS + ["more"]}),
                    encoding="utf-8")
    assert proof_scope.build_inputs(
        str(review), str(plan), REELS)["plan_is_new"] is True


# ── The wiring: the build prints it and records it ───────────────

@pytest.fixture
def build_project(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "project.yaml").write_text(
        'resolve: {project_name: "Mock Project", '
        'timeline_name: "Master"}', encoding="utf-8")
    review = root / "pipeline_output" / "review"
    review.mkdir(parents=True)
    (review / "reel_proposals_v2.json").write_text("[]", encoding="utf-8")
    scratch = root / "pipeline_output" / "scratch" / "timeline_transcript"
    scratch.mkdir(parents=True)
    (scratch / "transcript.json").write_text(
        '{"segments": []}', encoding="utf-8")
    return root


def test_the_build_records_and_prints_its_proof_scope(
        build_project, capsys, monkeypatch):
    """A fresh project has no provenance, so the plan is new and the
    record carries a FULL scope the run also printed."""
    from library.tools.reel_build import rebuild_reels_in_project

    class _FakeTimeline:
        _seq = 0

        def __init__(self, name):
            self._name = name
            _FakeTimeline._seq += 1
            self._uid = f"fake-timeline-{_FakeTimeline._seq}"

        def GetName(self):
            return self._name

        def GetUniqueId(self):
            # `resolve_lock.assert_current_timeline` reads the cursor
            # back by this after setting it, so a double without an
            # identity cannot satisfy the guard.
            return self._uid

    class _FakeProject:
        def __init__(self, names):
            self.timelines = [_FakeTimeline(name) for name in names]
            pool = MagicMock()
            pool.CreateEmptyTimeline.side_effect = self._create
            pool.DeleteTimelines.side_effect = self._delete
            self._pool = pool

        def _create(self, name):
            timeline = _FakeTimeline(name)
            self.timelines.append(timeline)
            return timeline

        def _delete(self, timelines):
            for timeline in timelines:
                self.timelines.remove(timeline)
            return True

        def GetMediaPool(self):
            return self._pool

        def GetName(self):
            return "Mock Project"

        def GetTimelineCount(self):
            return len(self.timelines)

        def GetTimelineByIndex(self, index):
            return self.timelines[index - 1]

        # A Resolve project HAS a cursor. `assert_current_timeline`
        # READS it before setting it - what it was on arrival is the
        # only evidence a foreign writer moved it - so a double that
        # cannot be pointed anywhere cannot model the guard. None until
        # something sets it: a project nobody has pointed anywhere has
        # no cursor, and inventing one hands the entry-unit guard a
        # timeline nobody opened.
        def GetCurrentTimeline(self):
            return getattr(self, "_current", None)

        def SetCurrentTimeline(self, timeline):
            self._current = timeline
            return True

    resolve_project = _FakeProject(["Master"])
    install_measured_draw_gain_probe(monkeypatch)
    moment = MagicMock()
    moment.approval = "approved"
    moment.number = 1
    moment.timeline_name = "Reel 01 - hook"
    moment.timeline_start = 0.0
    moment.timeline_end = 10.0

    def _place(**place_kwargs):
        resolve_project.GetMediaPool().CreateEmptyTimeline(
            place_kwargs.get("timeline_name"))
        return {"track_plan": {"video_tracks": [], "audio_tracks": [],
                               "material": {}}}

    with patch("library.tools.reel_build.build_reel_timeline",
               side_effect=_place), \
            patch("library.tools.reel_build.reel_subtitle_segments",
                  return_value=[]), \
            patch("library.tools.reel_build._connect_resolve"), \
            patch("library.tools.reel_build.resolve_project_exactly",
                  return_value=resolve_project), \
            patch("library.tools.reel_proposal.read_proposal",
                  return_value=[moment]), \
            patch("library.tools.timeline_ingest.snapshot_timeline"):
        record = rebuild_reels_in_project(
            str(build_project), organise=False, verify=False)

    assert record["proof_scope"]["level"] == "FULL"
    assert record["proof_scope"]["stills"] == ["Reel 01 - hook"]
    out = capsys.readouterr().out
    assert "Proof owed by this build: FULL" in out


# --------------------------------------------------------------------------
# From test_plan_mismatch_scopes_to_picture.py
#
# PLAN-MISMATCH scopes to picture: reel 13's one-frame refusal.
#
# The plan check compares the picture extent (V1/V2), never the whole
# timeline: a caption rounding one frame past exact picture is not a
# different plan, while a dropped or lengthened picture clip still refuses.
# History and verdict: `docs/evidence/plan_mismatch_scope.md`.

FPS = 24000 / 1001  # the reel timeline rate, exact

# The reel-13 shape, reduced to one range: a 79.354s span whose
# per-range framing and whose closing caption's per-edge rounding land
# on different integers. The numbers below ARE the R5 report's numbers.
RANGE_START = 10.0
RANGE_END = 89.354
REEL_END_SECONDS = RANGE_END - RANGE_START  # 79.354
PLAN_KEEP_RANGES = ((RANGE_START, RANGE_END),)
PLANNED_FRAMES = 1902
CAPTION_RECORD_END = 1903


def _picture(start_frame: int, end_frame: int) -> TimelineItem:
    return TimelineItem(
        track_type="video", track_index=1,
        start_frame=start_frame, end_frame=end_frame,
        duration_frames=end_frame - start_frame,
        source_start_frame=0, source_end_frame=end_frame - start_frame,
        source_file="/m/a.MXF", speaker="Akshita", name="clip",
    )


def _caption(start_frame: int, end_frame: int) -> TimelineItem:
    return TimelineItem(
        track_type="video", track_index=3,
        start_frame=start_frame, end_frame=end_frame,
        duration_frames=end_frame - start_frame,
        source_start_frame=0, source_end_frame=end_frame - start_frame,
        source_file="/s/seg.mov", speaker="Akshita", name="seg",
    )


def _plan() -> ReelPlan:
    return ReelPlan(
        reel_name="Reel 13 - the-accounting-firm",
        reel_number=13,
        plan_seconds=REEL_END_SECONDS,
        plan_frames=REEL_END_SECONDS * FPS,
        span_start=RANGE_START,
        span_end=RANGE_END,
        placements=(PlannedPlacement(
            track_index=1, speaker="Akshita", record_seconds=0.0,
            source_in=0.0, source_out=REEL_END_SECONDS,
            source_file="/m/a.MXF"),),
        captions=(),
        # The fixture carries no caption reference set, so the caption
        # layer is REFUSED (NO_REFERENCE) rather than graded - these
        # tests own the length gate only, and a second gate's noise
        # must not decide them.
        captions_unavailable="fixture carries no caption reference set",
        keep_ranges=PLAN_KEEP_RANGES,
    )


def _error_classes(result) -> set:
    return {f.finding_class for f in result.findings
            if f.severity == "error"}


def test_a_caption_tail_past_exact_picture_does_not_refuse_the_plan():
    """The regression: picture tiles to the frame, caption rounds one
    past it, timeline carries 1903 - and the plan still describes the
    reel, so F4 must run rather than be refused.

    FAILS before the fix (the whole-timeline scope refuses with the R5
    numbers); PASSES after (the picture scope agrees at 1902).
    """
    timeline = ReelTimeline(
        reel_name="Reel 13 - the-accounting-firm",
        fps=FPS,
        total_frames=CAPTION_RECORD_END,
        picture_frames=PLANNED_FRAMES,
        video_items=(_picture(0, PLANNED_FRAMES),),
        audio_items=(),
        caption_items=(_caption(0, CAPTION_RECORD_END),),
    )
    result = verify_reel(_plan(), timeline)
    classes = _error_classes(result)
    assert FindingClass.PLAN_MISMATCH not in classes, (
        "picture tiles the plan exactly - a one-frame caption rounding "
        f"tail is not a different plan: "
        f"{[(f.finding_class, f.message) for f in result.errors]}")
    assert FindingClass.F4 not in classes, (
        "with the plan describing the timeline, F4 runs and the "
        "single placed clip matches the single placement")


def test_a_picture_a_frame_short_or_long_still_refuses():
    """The gate keeps its teeth both ways: the scoping moves which extent
    is graded, never how exact the grading is. +1 is the mixed-rate
    last-clip shape, the one alternative reading of reel 13's +1."""
    for delta in (-1, 1):
        findings = check_plan_describes_timeline(
            "Reel 13", PLAN_KEEP_RANGES, PLANNED_FRAMES + delta, FPS)
        assert len(findings) == 1
        assert findings[0].finding_class == FindingClass.PLAN_MISMATCH
        assert findings[0].detail["delta_frames"] == delta
