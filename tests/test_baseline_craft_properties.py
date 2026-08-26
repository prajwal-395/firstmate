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


def _yuv_frame(chroma: float, accent_rows: int = 0, accent_chroma: float = 0.0):
    """A lit yuv444p frame at a uniform chroma, optionally with an accent band."""
    y = np.full((H, W), 180, dtype=np.uint8)
    # S = sqrt((U-128)^2 + (V-128)^2); put it all in U so S == |U-128|.
    u = np.full((H, W), 128 + chroma, dtype=np.uint8)
    v = np.full((H, W), 128, dtype=np.uint8)
    if accent_rows:
        u[:accent_rows, :] = 128 + accent_chroma
    return np.stack([y, u, v])


def _occupancy(fractions, framing_intents=None, **kwargs):
    frames = [_gray_frame(f) for f in fractions]
    with patch.object(render_qa, "_probe_video_size", return_value=(W, H)), \
            patch.object(render_qa, "_stream_raw_frames", return_value=iter(frames)):
        return measure_frame_occupancy("master.mp4", framing_intents=framing_intents,
                                       sample_fps=1.0, **kwargs)


def _chroma(frames, **kwargs):
    with patch.object(render_qa, "_probe_video_size", return_value=(W, H)), \
            patch.object(render_qa, "_stream_raw_frames", return_value=iter(frames)):
        return measure_chroma_presence("master.mp4", sample_fps=1.0, **kwargs)


# ═══ P1: the picture fills the frame, and one video has one geometry ═══

class TestP1FrameOccupancy:

    def test_a_filled_frame_with_one_geometry_passes(self):
        result = _occupancy([1.0] * 10)
        assert result.passed, result.detail
        assert result.value["median_lit_fraction"] == 1.0
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
        assert "changes size within the video" in result.detail
        # and specifically NOT via the fill floor
        assert "letterboxed" not in result.detail

    def test_both_001_failures_are_reported_together(self):
        """001 fails twice, for different reasons, and both must show."""
        result = _occupancy([608 / 1920] * 8 + [1.0] * 2)
        assert not result.passed
        assert "letterboxed and nothing asked for bars" in result.detail
        assert "changes size within the video" in result.detail

    def test_a_declared_letterbox_is_exempt_from_the_fill_floor(self):
        """A series that declares bars gets them - cinematic_narrative does.

        There is no channel-wide number for how much frame a deliberately
        inset picture should occupy, so inventing one here would answer a
        brand decision. The consistency half still applies.
        """
        result = _occupancy([0.34] * 10, framing_intents=[0.0] * 8)
        assert result.passed, result.detail
        assert result.threshold["fill_floor_applies"] is False
        assert result.threshold["consistency_applies"] is True

    def test_a_declared_letterbox_still_owes_one_geometry(self):
        result = _occupancy([0.34] * 6 + [0.9] * 4, framing_intents=[0.0] * 8)
        assert not result.passed
        assert "changes size within the video" in result.detail

    def test_clips_declaring_different_intents_may_change_geometry(self):
        """A spine block may declare its own framing - then variation is asked for."""
        result = _occupancy([1.0] * 6 + [0.34] * 4, framing_intents=[1.0, 1.0, 0.0])
        assert result.passed, result.detail
        assert result.threshold["consistency_applies"] is False

    def test_the_lit_threshold_is_the_pipelines_own_black(self):
        assert LIT_LUMA_THRESHOLD == 12.0

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
        result = measure_speech_above_bed("master.mp4", "music.wav", [])
        assert result.passed
        assert "nothing to measure" in result.detail


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
        from library.steps.step_6_02_validate_output import step as validate

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
        from library.steps.step_6_02_validate_output import step as validate

        manifest = {
            "tracks": {
                "V1": {"clips": [{"framing_intent": 1.0}, {"framing_intent": 1.0}]},
                "V2": {"clips": [{"framing_intent": 0.0}]},
                "A2": {"clips": [{"source_file": __file__}]},
            },
            "audio_mix": {"music_automation": [{"timeline_start": 0.0}]},
        }
        assert validate._declared_framing_intents(manifest) == [1.0, 1.0, 0.0]
        music, automation = validate._music_bed(manifest)
        assert music == __file__
        assert len(automation) == 1

    def test_a_missing_music_file_disables_p3_rather_than_guessing(self):
        from library.steps.step_6_02_validate_output import step as validate

        music, automation = validate._music_bed(
            {"tracks": {"A2": {"clips": [{"source_file": "/nope/absent.wav"}]}},
             "audio_mix": {"music_automation": [{"timeline_start": 0.0}]}})
        assert music is None and automation == []


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
