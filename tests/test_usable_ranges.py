"""Usable ranges must be genuinely measured, not a constant.

The v3 vision pipeline unconditionally set `usable_ranges = [[0, duration]]`
for every clip.  This module tests the replacement: a deterministic
measurement from temporal-index signals (motion energy, speech regions,
face presence) that classifies ranges as usable or unusable and records
HOW it knows via `usable_ranges_method` and `usable_ranges_signals`.

Tests use synthetic data shaped like the 17 real clips in the reference
project, plus boundary cases the reference data does not cover.
"""

import sys
from unittest.mock import MagicMock

import pytest

mlx_mock = MagicMock()
mlx_mock.load.return_value = (MagicMock(), MagicMock())
mlx_mock.generate.return_value = MagicMock(text="[]")
mlx_prompt_utils = MagicMock()
mlx_prompt_utils.apply_chat_template.return_value = "prompt"
mlx_mock.prompt_utils = mlx_prompt_utils


from unittest.mock import patch
@pytest.fixture(autouse=True)
def mock_mlx_functions():
    with patch("library.tools.analysis.vision_pipeline_v3.load", mlx_mock.load), \
         patch("library.tools.analysis.vision_pipeline_v3.generate", mlx_mock.generate), \
         patch("library.tools.analysis.vision_pipeline_v3.apply_chat_template", mlx_prompt_utils.apply_chat_template):
        yield

from library.tools.analysis.vision_pipeline_v3 import (
    _complement_ranges,
    _compute_usable_ranges,
    compute_deterministic_assessment,
)


# Motion levels in the absolute units step 1.04 measures (mean absolute
# frame difference, 0-1), taken from the reference project's raw footage:
# a locked-off shot sits near CALM, ordinary handheld near HANDHELD, and
# only a whip or a fumble reaches WHIP.
CALM = 0.02
HANDHELD = 0.06
WHIP = 0.20


# ── Helper: build temporal indices shaped like the reference project ──

def _make_temporal_index(
    duration,
    motion_values,
    speech_regions=None,
    face_values=None,
    motion_sr=30,
    face_sr=5,
    with_motion_scale=True,
):
    """Build a minimal temporal-index dict for testing.

    `motion_values` are absolute mean absolute frame differences, the
    units step 1.04 measures in.  They are stored the way step 1.04
    stores them - normalized to the clip's own peak, with that peak
    carried alongside as `peak_mean_abs_diff` - so a test that hands in
    a flat curve gets the same peaks-at-1.0 normalization real footage
    gets.  `with_motion_scale=False` reproduces a temporal index written
    before that key existed.
    """
    peak = max(motion_values) if motion_values else 0.0
    normalized = [
        round(v / peak, 3) if peak > 0 else 0.0 for v in motion_values
    ]
    motion = {
        "sample_rate_hz": motion_sr,
        "values": normalized,
        "high_motion_times": [
            i / motion_sr for i, v in enumerate(normalized) if v > 0.5
        ],
    }
    if with_motion_scale:
        motion["peak_mean_abs_diff"] = round(peak, 5)
    idx = {
        "duration": duration,
        "motion_energy": motion,
        "speech_regions": speech_regions or [],
        "scene_boundaries": [{"timestamp": 0.0}],
        "energy_curve": {"sample_rate_hz": motion_sr, "values": [0.0] * len(motion_values)},
        "audio_events": [],
    }
    if face_values is not None:
        idx["face_presence"] = {
            "sample_rate_hz": face_sr,
            "values": face_values,
        }
    return idx


# ═══════════════════════════════════════════════════════════════════════
#  _complement_ranges
# ═══════════════════════════════════════════════════════════════════════

class TestComplementRanges:



    def test_single_unusable_in_middle(self):
        unusable = [{"start": 3.0, "end": 5.0, "reason": "test"}]
        result = _complement_ranges(unusable, 10.0)
        assert result == [[0, 3.0], [5.0, 10.0]]




    def test_sliver_gap_dropped(self):
        """A gap too short to cut to is not offered as usable footage."""
        unusable = [
            {"start": 0.0, "end": 4.0, "reason": "a"},
            {"start": 4.033, "end": 10.0, "reason": "a"},
        ]
        assert _complement_ranges(unusable, 10.0) == []

    def test_gap_at_minimum_length_kept(self):
        unusable = [
            {"start": 0.0, "end": 4.0, "reason": "a"},
            {"start": 4.5, "end": 10.0, "reason": "a"},
        ]
        assert _complement_ranges(unusable, 10.0) == [[4.0, 4.5]]


# ═══════════════════════════════════════════════════════════════════════
#  _compute_usable_ranges - unmeasured paths
# ═══════════════════════════════════════════════════════════════════════

class TestUnmeasured:
    def test_no_temporal_index(self):
        """Nothing measured means NO usable range, not the whole clip.

        `[[0, duration]]` beside `usable_ranges_method: "unmeasured"` is
        one field contradicting the three next to it, and it is what the
        B-roll selector read when it cut project 001's first interjection
        out of a whip pan.
        """
        usable, unusable, method, signals = _compute_usable_ranges(
            None, 10.0, "unknown")
        assert usable == []
        assert unusable == []
        assert method == "unmeasured"
        assert signals == []

    def test_sparse_motion_data(self):
        """Less than a second of motion data -> unmeasured."""
        idx = _make_temporal_index(
            duration=0.5,
            motion_values=[CALM] * 15,  # 15 < 30 samples at 30Hz
        )
        usable, unusable, method, signals = _compute_usable_ranges(
            idx, 0.5, "unknown")
        assert method == "unmeasured"
        assert signals == []

    def test_sparse_guard_tracks_the_declared_sample_rate(self):
        """The guard is 1s of data, not 30 samples."""
        idx = _make_temporal_index(
            duration=2.0,
            motion_values=[CALM] * 20,  # 2s at 10Hz
            motion_sr=10,
        )
        _, _, method, signals = _compute_usable_ranges(idx, 2.0, "unknown")
        assert method == "deterministic_v1"
        assert signals == ["motion_energy"]

    def test_empty_temporal_index(self):
        usable, unusable, method, signals = _compute_usable_ranges(
            {}, 5.0, "unknown")
        assert method == "unmeasured"

    def test_motion_without_absolute_scale_is_not_measured(self):
        """A normalized curve alone cannot answer "how much motion is this".

        Older temporal indices carry only the per-clip normalized curve,
        where every clip peaks at 1.0.  Thresholding that would call a
        locked-off shot's sensor noise a camera whip, so the motion rules
        report nothing instead.
        """
        idx = _make_temporal_index(
            duration=10.0,
            motion_values=[WHIP] * 300,
            with_motion_scale=False,
        )
        usable, unusable, method, signals = _compute_usable_ranges(
            idx, 10.0, "unknown")

        assert method == "unmeasured"
        assert signals == []
        assert unusable == []
        assert usable == []

    def test_zero_duration_reports_nothing_usable(self):
        usable, unusable, method, signals = _compute_usable_ranges(
            None, 0, "unknown")
        assert usable == []
        assert method == "unmeasured"


# ═══════════════════════════════════════════════════════════════════════
#  Rule 1: Sustained high motion
# ═══════════════════════════════════════════════════════════════════════

class TestRule1SustainedHighMotion:
    def test_whipping_clip_mostly_unusable(self):
        """A 3.567s clip held at whip-grade motion throughout is unusable."""
        motion = [WHIP] * 106
        idx = _make_temporal_index(duration=3.567, motion_values=motion)
        usable, unusable, method, signals = _compute_usable_ranges(
            idx, 3.567, "scenery")

        assert method == "deterministic_v1"
        assert "motion_energy" in signals
        assert len(unusable) >= 1
        assert unusable[0]["reason"] == "sustained_high_motion"
        # The whole clip should be flagged, leaving little or nothing usable
        total_unusable = sum(r["end"] - r["start"] for r in unusable)
        assert total_unusable > 2.5  # >70% of 3.567s

    def test_low_motion_clip_all_usable(self):
        """A quiet clip is entirely usable."""
        motion = [CALM] * 300  # 10s at 30Hz
        idx = _make_temporal_index(duration=10.0, motion_values=motion)
        usable, unusable, method, signals = _compute_usable_ranges(
            idx, 10.0, "scenery")

        assert method == "deterministic_v1"
        assert usable == [[0, 10.0]]
        assert unusable == []

    def test_flat_clip_is_usable_however_its_curve_normalizes(self):
        """A locked-off shot's sensor noise is not camera handling.

        Its normalized curve rides near 1.0 for the whole clip because
        the clip's own peak is tiny; the measurement must read the
        absolute scale and leave the clip alone.
        """
        motion = [0.004 + (0.001 if i % 3 else 0) for i in range(300)]
        idx = _make_temporal_index(duration=10.0, motion_values=motion)
        assert max(idx["motion_energy"]["values"]) == 1.0

        usable, unusable, method, signals = _compute_usable_ranges(
            idx, 10.0, "scenery")

        assert method == "deterministic_v1"
        assert unusable == []
        assert usable == [[0, 10.0]]

    def test_brief_spike_not_flagged(self):
        """High motion for less than 1s is not flagged (intentional gesture)."""
        motion = [CALM] * 300
        # Spike at 5s for 0.5s (15 samples) - below 1s threshold
        motion[150:165] = [WHIP] * 15
        idx = _make_temporal_index(duration=10.0, motion_values=motion)
        usable, unusable, method, signals = _compute_usable_ranges(
            idx, 10.0, "unknown")

        assert method == "deterministic_v1"
        assert unusable == []
        assert usable == [[0, 10.0]]

    def test_sustained_spike_flagged(self):
        """High motion for >1s is flagged."""
        motion = [CALM] * 300
        # Spike at 3s for 2s (60 samples)
        motion[90:150] = [WHIP] * 60
        idx = _make_temporal_index(duration=10.0, motion_values=motion)
        usable, unusable, method, signals = _compute_usable_ranges(
            idx, 10.0, "unknown")

        assert len(unusable) == 1
        assert unusable[0]["reason"] == "sustained_high_motion"
        assert unusable[0]["start"] == 3.0
        assert unusable[0]["end"] == 5.0
        assert usable == [[0, 3.0], [5.0, 10.0]]

    def test_runs_split_by_one_calm_sample_yield_no_sliver(self):
        """Two whips a frame apart do not advertise a 0.03s usable range."""
        motion = [CALM] * 300
        motion[60:120] = [WHIP] * 60
        motion[121:181] = [WHIP] * 60
        idx = _make_temporal_index(duration=10.0, motion_values=motion)

        usable, unusable, method, signals = _compute_usable_ranges(
            idx, 10.0, "unknown")

        assert all(r[1] - r[0] >= 0.5 for r in usable), usable
        assert usable == [[0, 2.0], [6.033, 10.0]]


# ═══════════════════════════════════════════════════════════════════════
#  Rule 2: Dead head/tail
# ═══════════════════════════════════════════════════════════════════════

class TestRule2IsAROllOnly:
    """The rule reads "outside all speech" as dead.

    Right for a talking head, where the head and tail are the operator
    raising the phone.  Backwards for a cutaway, which plays `video_only`
    so its audio is never heard - it emptied IMG_1819, the shot 001's edit
    closes on, and left IMG_1813 at 12%.  Gated like Rule 3 below.
    """

    # IMG_1819's shape: 4.7s of B-roll carrying one 0.36s speech region.
    SPEECH = [{"start": 0.893, "end": 1.254, "text": "look at me."}]

    def _run(self, content_type, soft=None):
        idx = _make_temporal_index(
            duration=4.7, motion_values=[HANDHELD] * 141,
            speech_regions=self.SPEECH)
        return _compute_usable_ranges(idx, 4.7, content_type, soft)

    def test_broll_keeps_its_silent_picture(self):
        """A cutaway is chosen BECAUSE it is not someone talking."""
        usable, unusable, _, _ = self._run("scenery")

        assert not {r["reason"] for r in unusable} & {
            "high_motion_head", "high_motion_tail"}
        assert usable, "IMG_1819 is the shot the finished edit closes on"

    def test_aroll_still_loses_its_dead_head_and_tail(self):
        """Gated, not deleted. It still fires where it belongs."""
        _, unusable, _, signals = self._run("person_talking_to_camera")

        assert {r["reason"] for r in unusable} >= {
            "high_motion_head", "high_motion_tail"}
        assert "speech_regions" in signals

    def test_a_soft_picture_is_still_excluded(self):
        """Un-fencing what was wrongly fenced must not un-fence the dashboard.

        IMG_1811's opening 2s is the window the captain marked.  It is
        excluded by `soft_picture` alone, and gating the speech-shaped rule
        must not bring it back.
        """
        soft = [{"start": 0.0, "end": 2.0, "reason": "soft_picture"}]

        usable, unusable, _, _ = self._run("scenery", soft)

        assert soft[0] in unusable
        assert not any(s <= 0.0 and 2.0 <= e for s, e in usable)


class TestRule2DeadHeadTail:
    def test_high_motion_head_before_speech(self):
        """Head region with high motion and no speech is flagged."""
        motion = [0.0] * 300
        # Head is [0, first_speech=3.0] = 90 samples at 30Hz.
        # Its mean must clear the head/tail threshold for the rule to fire.
        motion[:90] = [HANDHELD] * 90
        speech = [{"start": 3.0, "end": 8.0, "text": "hello"}]
        idx = _make_temporal_index(
            duration=10.0, motion_values=motion, speech_regions=speech)

        # A-roll: the rule reasons about a speaker, so it only applies to a
        # clip that has one.  See TestRule2IsAROllOnly below.
        usable, unusable, method, signals = _compute_usable_ranges(
            idx, 10.0, "person_talking_to_camera")

        head_flags = [r for r in unusable if r["reason"] == "high_motion_head"]
        assert len(head_flags) == 1
        assert head_flags[0]["start"] == 0.0
        assert head_flags[0]["end"] == 3.0
        assert "speech_regions" in signals

    def test_high_motion_tail_after_speech(self):
        """Tail region with high motion and no speech is flagged."""
        motion = [0.0] * 300
        # Tail is [last_speech=7.0, duration=10.0] = samples 210 to 300.
        # Its mean must clear the head/tail threshold for the rule to fire.
        motion[210:] = [HANDHELD] * 90
        speech = [{"start": 1.0, "end": 7.0, "text": "hello"}]
        idx = _make_temporal_index(
            duration=10.0, motion_values=motion, speech_regions=speech)

        usable, unusable, method, signals = _compute_usable_ranges(
            idx, 10.0, "person_talking_to_camera")

        tail_flags = [r for r in unusable if r["reason"] == "high_motion_tail"]
        assert len(tail_flags) == 1
        assert tail_flags[0]["start"] == 7.0
        assert tail_flags[0]["end"] == 10.0

    def test_no_speech_no_dead_head_tail(self):
        """Without speech regions, Rule 2 does not fire."""
        motion = [HANDHELD] * 300
        idx = _make_temporal_index(duration=10.0, motion_values=motion)

        usable, unusable, method, signals = _compute_usable_ranges(
            idx, 10.0, "unknown")

        head_tail = [r for r in unusable
                     if r["reason"] in ("high_motion_head", "high_motion_tail")]
        assert head_tail == []
        assert "speech_regions" not in signals

    def test_short_head_not_flagged(self):
        """Head shorter than 0.5s is not flagged (too brief for button-press)."""
        motion = [HANDHELD] * 300
        speech = [{"start": 0.3, "end": 8.0, "text": "hello"}]
        idx = _make_temporal_index(
            duration=10.0, motion_values=motion, speech_regions=speech)

        usable, unusable, method, signals = _compute_usable_ranges(
            idx, 10.0, "unknown")

        head_flags = [r for r in unusable if r["reason"] == "high_motion_head"]
        assert head_flags == []

    def test_low_motion_head_not_flagged(self):
        """A calm head is not flagged even though it holds no speech."""
        motion = [CALM] * 300
        speech = [{"start": 3.0, "end": 8.0, "text": "hello"}]
        idx = _make_temporal_index(
            duration=10.0, motion_values=motion, speech_regions=speech)

        # A-roll: the rule reasons about a speaker, so it only applies to a
        # clip that has one.  See TestRule2IsAROllOnly below.
        usable, unusable, method, signals = _compute_usable_ranges(
            idx, 10.0, "person_talking_to_camera")

        head_flags = [r for r in unusable if r["reason"] == "high_motion_head"]
        assert head_flags == []


# ═══════════════════════════════════════════════════════════════════════
#  Rule 3: Subject absence (A-roll clips only)
# ═══════════════════════════════════════════════════════════════════════

class TestRule3SubjectAbsence:
    def test_face_absent_on_aroll_clip(self):
        """A talking-head clip where face drops out for >2s is flagged."""
        motion = [CALM] * 300
        face = [0.9] * 50  # 10s at 5Hz
        # Face disappears from 4s to 7s (15 samples)
        face[20:35] = [0.0] * 15
        idx = _make_temporal_index(
            duration=10.0, motion_values=motion, face_values=face)

        usable, unusable, method, signals = _compute_usable_ranges(
            idx, 10.0, "person_talking_to_camera")

        absent = [r for r in unusable if r["reason"] == "subject_absent"]
        assert len(absent) == 1
        assert absent[0]["start"] == 4.0
        assert absent[0]["end"] == 7.0
        assert "face_presence" in signals

    def test_face_absent_ignored_for_scenery(self):
        """Scenery clips do not get subject-absence flags."""
        motion = [CALM] * 300
        face = [0.0] * 50  # Face absent the whole time
        idx = _make_temporal_index(
            duration=10.0, motion_values=motion, face_values=face)

        usable, unusable, method, signals = _compute_usable_ranges(
            idx, 10.0, "scenery")

        absent = [r for r in unusable if r["reason"] == "subject_absent"]
        assert absent == []
        assert "face_presence" not in signals

    def test_brief_face_drop_not_flagged(self):
        """Face absent for <2s is not flagged (natural movement)."""
        motion = [CALM] * 300
        face = [0.9] * 50
        # 1s absence (5 samples at 5Hz) - below 2s threshold
        face[25:30] = [0.0] * 5
        idx = _make_temporal_index(
            duration=10.0, motion_values=motion, face_values=face)

        usable, unusable, method, signals = _compute_usable_ranges(
            idx, 10.0, "person_talking_to_camera")

        absent = [r for r in unusable if r["reason"] == "subject_absent"]
        assert absent == []

    def test_no_face_data_skips_rule(self):
        """Without face_presence data, Rule 3 is skipped (older temporal index)."""
        motion = [CALM] * 300
        idx = _make_temporal_index(duration=10.0, motion_values=motion)
        # No face_values -> no face_presence key

        usable, unusable, method, signals = _compute_usable_ranges(
            idx, 10.0, "person_talking_to_camera")

        assert "face_presence" not in signals
        absent = [r for r in unusable if r["reason"] == "subject_absent"]
        assert absent == []


# ═══════════════════════════════════════════════════════════════════════
#  Honesty mechanism: method and signals
# ═══════════════════════════════════════════════════════════════════════

class TestHonestyMechanism:


    def test_signals_list_tracks_what_was_used(self):
        """Signals list reflects which data contributed to the measurement."""
        motion = [CALM] * 300
        speech = [{"start": 1.0, "end": 8.0, "text": "hello"}]
        face = [0.9] * 50
        idx = _make_temporal_index(
            duration=10.0, motion_values=motion,
            speech_regions=speech, face_values=face)

        _, _, _, signals = _compute_usable_ranges(
            idx, 10.0, "person_talking_to_camera")
        assert "motion_energy" in signals
        assert "speech_regions" in signals
        assert "face_presence" in signals

    def test_signals_omits_absent_data(self):
        """Absent signals are not listed."""
        motion = [CALM] * 300
        idx = _make_temporal_index(duration=10.0, motion_values=motion)

        _, _, _, signals = _compute_usable_ranges(idx, 10.0, "unknown")
        assert "motion_energy" in signals
        assert "speech_regions" not in signals  # no speech regions
        assert "face_presence" not in signals   # no face data


# ═══════════════════════════════════════════════════════════════════════
#  Integration with compute_deterministic_assessment
# ═══════════════════════════════════════════════════════════════════════

class TestDeterministicAssessmentIntegration:



    def test_uses_temporal_index_duration_key(self):
        """Handles temporal indices with 'duration' key (older format)."""
        idx = _make_temporal_index(duration=5.0, motion_values=[CALM] * 150)
        # The fixture uses "duration", not "duration_s"
        assert "duration" in idx
        assert "duration_s" not in idx

        result = compute_deterministic_assessment(idx, "")
        assert result["usable_ranges"] == [[0, 5.0]]
        assert result["usable_ranges_method"] == "deterministic_v1"

    def test_explicit_duration_parameter_used(self):
        """When duration is passed explicitly, it is used for usable_ranges."""
        idx = _make_temporal_index(duration=5.0, motion_values=[CALM] * 150)
        # Pass a different duration via the parameter
        result = compute_deterministic_assessment(idx, "", duration=6.0)
        assert result["usable_ranges"] == [[0, 6.0]]


# ═══════════════════════════════════════════════════════════════════════
#  Reference project validation (synthetic data shaped like real clips)
# ═══════════════════════════════════════════════════════════════════════

class TestReferenceProjectShapes:
    """Test against curves measured from the reference project's raw footage.

    The statistics below come from running step 1.04's own frame-difference
    measurement over the source clips, before normalization: IMG_1806 spans
    0.024-0.060 across its whole length, IMG_1807 holds above 0.08 for over
    a second while it is whipped, and the talking-head clips sit around
    0.010-0.025 throughout.
    """


    def test_clip_002_like_whip_pan(self):
        """clip_002 (IMG_1807): a real whip, peaking at 0.359."""
        import random
        random.seed(7)
        motion = [0.050 + random.uniform(-0.02, 0.02) for _ in range(513)]
        motion[300:390] = [0.25] * 90  # 3s of whipping at 10-13s
        idx = _make_temporal_index(duration=17.1, motion_values=motion)

        usable, unusable, method, signals = _compute_usable_ranges(
            idx, 17.1, "scenery")

        assert method == "deterministic_v1"
        assert [r["reason"] for r in unusable] == ["sustained_high_motion"]
        assert unusable[0]["start"] == 10.0
        assert unusable[0]["end"] == 13.0
        assert usable == [[0, 10.0], [13.0, 17.1]]

    def test_clip_009_like_talking_head(self):
        """clip_009 (IMG_1814): 45.943s, low motion, speech at 30.72-42.66s.

        Should be mostly or entirely usable. The long pre-speech head
        should NOT be flagged because the motion is low.
        """
        motion = [0.012 + (0.002 if i % 5 == 0 else 0) for i in range(1377)]
        speech = [
            {"start": 30.72, "end": 42.657, "text": "today is..."},
            {"start": 43.6, "end": 44.1, "text": "okay"},
            {"start": 44.039, "end": 45.602, "text": "stop right there"},
        ]
        idx = _make_temporal_index(
            duration=45.943, motion_values=motion, speech_regions=speech)

        usable, unusable, method, signals = _compute_usable_ranges(
            idx, 45.943, "person_talking_to_camera")

        assert method == "deterministic_v1"
        # A calm head is not a fumble, however its curve normalizes
        head_flags = [r for r in unusable if r["reason"] == "high_motion_head"]
        assert head_flags == [], (
            f"Low-motion head should not be flagged: {head_flags}")
        # Most of the clip should remain usable
        total_usable = sum(r[1] - r[0] for r in usable)
        assert total_usable > 40.0



# ═══════════════════════════════════════════════════════════════════════
#  Edge cases
# ═══════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    def test_multiple_rules_combine(self):
        """Rules 1, 2, and 3 can all fire on the same clip."""
        motion = [0.0] * 300
        # Rule 1: sustained high motion at 4-6s
        motion[120:180] = [WHIP] * 60
        # Rule 2: high motion head - head is [0, first_speech=2.0] = 60 samples
        motion[:60] = [HANDHELD] * 60
        speech = [{"start": 2.0, "end": 8.0, "text": "hello"}]
        # Rule 3: face absent at 7-9.5s
        face = [0.9] * 50
        face[35:48] = [0.0] * 13  # 2.6s absence
        idx = _make_temporal_index(
            duration=10.0, motion_values=motion,
            speech_regions=speech, face_values=face)

        usable, unusable, method, signals = _compute_usable_ranges(
            idx, 10.0, "person_talking_to_camera")

        reasons = {r["reason"] for r in unusable}
        assert "sustained_high_motion" in reasons
        assert "high_motion_head" in reasons
        assert "subject_absent" in reasons

    def test_overlapping_rules_leave_nothing_usable(self):
        """A clip every rule rejects reports no usable range at all."""
        motion = [WHIP] * 300  # high motion throughout
        speech = [{"start": 5.0, "end": 8.0, "text": "hello"}]
        idx = _make_temporal_index(
            duration=10.0, motion_values=motion, speech_regions=speech)

        usable, unusable, method, signals = _compute_usable_ranges(
            idx, 10.0, "person_talking_to_camera")

        assert usable == []
        assert {r["reason"] for r in unusable} == {
            "sustained_high_motion", "high_motion_head", "high_motion_tail"}

    def test_unusable_ranges_are_sorted_and_deduplicated(self):
        """Overlapping findings of the same reason read as one defect."""
        motion = [CALM] * 300
        motion[30:90] = [WHIP] * 60
        motion[180:240] = [WHIP] * 60
        speech = [{"start": 4.0, "end": 5.0, "text": "hello"}]
        idx = _make_temporal_index(
            duration=10.0, motion_values=motion, speech_regions=speech)

        _, unusable, _, _ = _compute_usable_ranges(idx, 10.0, "unknown")

        starts = [r["start"] for r in unusable]
        assert starts == sorted(starts)
        for reason in {r["reason"] for r in unusable}:
            same = [r for r in unusable if r["reason"] == reason]
            for a, b in zip(same, same[1:]):
                assert a["end"] < b["start"]

    def test_duration_clamping(self):
        """Unusable ranges are clamped to clip duration."""
        # 5s clip but motion runs to the end
        motion = [WHIP] * 150
        idx = _make_temporal_index(duration=5.0, motion_values=motion)

        usable, unusable, method, signals = _compute_usable_ranges(
            idx, 5.0, "unknown")

        for r in unusable:
            assert r["end"] <= 5.0

    def test_rounding(self):
        """All output values are rounded to 3 decimal places."""
        motion = [CALM] * 300
        speech = [{"start": 2.333333, "end": 8.666666, "text": "hello"}]
        motion[:70] = [HANDHELD] * 70  # head motion
        idx = _make_temporal_index(
            duration=10.123456, motion_values=motion, speech_regions=speech)

        usable, unusable, method, signals = _compute_usable_ranges(
            idx, 10.123456, "unknown")

        for r in usable:
            for v in r:
                assert v == round(v, 3)
        for r in unusable:
            assert r["start"] == round(r["start"], 3)
            assert r["end"] == round(r["end"], 3)
