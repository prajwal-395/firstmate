"""Rung 7 on step 2.05 (K1: findings 13/14, PA3.2/PA2.2, CT1.3, SD3.2).

1. Finding 13 - blocks are not frame-aligned: the cursor accumulated
   rounded seconds while the frame fields rounded each edge
   independently, so a cut at frame 767 read as outside a block
   starting at 25.576s. The cursor runs in frames now and the seconds
   derive from it: every boundary sits exactly on a frame.
2. Finding 14 - no tail handle: a cut could not move past its block's
   end except by restating the spine. Blocks cut from media carry
   `head_handle_frames` / `tail_handle_frames` - the unplayed source
   on either side, measured off the catalog - so a trim knows what it
   may extend into. Unknown headroom reads as absent, never as zero.
3. PA3.2/PA2.2 - an ASL/cut-rate target or feel: `pacing` windows
   over block positions, validated here and measured by
   `compile_manifest` into `pacing_report` (report-only). The feel-word
   case keeps the requester's words instead of inventing an ASL (E3).
"""
import sys
from pathlib import Path
import pytest
import json
import os
import subprocess
import re


REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import library.steps.step_2_05_mesh_spine.post_bridge as mb

FPS = 30.0


def _passage(n, clip="clip_1", start=0.0, end=2.285):
    return {
        "clip_id": clip, "source_start": start, "source_end": end,
        "text": f"line {n}",
        "word_timestamps": [
            {"word": "line", "source_start": start,
             "source_end": start + 0.5},
            {"word": str(n), "source_start": end - 0.5,
             "source_end": end},
        ],
        "alignment_method": "whisperx_word_alignment",
        "duration_seconds": round(end - start, 3),
    }


def _spine(blocks, **over):
    base = {"structure": blocks, "frame_rate": FPS}
    base.update(over)
    return base


def _speech_block(pos, ref, duration):
    return {"position": pos, "block_type": "speech",
            "duration_seconds": duration,
            "content": {"passage_ref": ref}}


def _enrich(blocks, passages, data=None):
    seq = {"body_sequence": passages}
    return mb.enrich_spine(_spine(blocks), seq, {}, data or {})


def test_boundaries_sit_exactly_on_frames():
    """Finding 13: seconds derive from the frame cursor, never round."""
    out = _enrich(
        [_speech_block(1, 1, 2.285), _speech_block(2, 2, 3.133)],
        [_passage(1, end=2.285), _passage(2, start=10.0, end=13.133)])
    structure = out["audio_spine"]["structure"]
    for block in structure:
        # Stored seconds are millisecond-rounded; the frame fields are
        # exact, and neighbouring seconds are EQUAL (one cursor).
        assert block["timeline_start"] == pytest.approx(
            block["timeline_start_frame"] / FPS, abs=1e-3)
        assert block["timeline_end"] == pytest.approx(
            block["timeline_end_frame"] / FPS, abs=1e-3)
    assert structure[0]["timeline_end_frame"] == (
        structure[1]["timeline_start_frame"])
    assert structure[0]["timeline_end"] == structure[1]["timeline_start"]
    assert structure[1]["duration_frames"] == (
        structure[1]["timeline_end_frame"]
        - structure[1]["timeline_start_frame"])


def test_a_frame_cut_is_inside_the_block_it_addresses():
    """Finding 13's own shape: frame 767 inside block 4's span."""
    out = _enrich(
        [_speech_block(1, 1, 25.567), _speech_block(2, 2, 3.0)],
        [_passage(1, end=25.567), _passage(2, start=40.0, end=43.0)])
    structure = out["audio_spine"]["structure"]
    block4 = structure[1]
    assert block4["timeline_start_frame"] == 767
    assert block4["timeline_start"] == pytest.approx(767 / FPS, abs=1e-3)
    # Membership is a frame question, never a rounded-seconds one: the
    # millisecond rounding above can sit a ten-thousandth past the
    # frame's exact time, which is finding 13 all over again.
    assert (block4["timeline_start_frame"]
            <= 767 <= block4["timeline_end_frame"])


def test_handles_measure_unplayed_source_in_frames():
    """Finding 14: 20 s of media, playing 4.083-7.216, leaves both."""
    out = _enrich(
        [_speech_block(1, 1, 3.133)],
        [_passage(1, start=4.083, end=7.216)],
        data={"clip_catalog": [
            {"clip_id": "clip_1", "duration_seconds": 20.0}]})
    (block,) = out["audio_spine"]["structure"]
    assert block["head_handle_frames"] == round(4.083 * FPS)
    assert block["tail_handle_frames"] == round((20.0 - 7.216) * FPS)


def test_pacing_windows_ride_the_spine():
    for window in (
            # PA3.2's shape: 2.5 s ASL over blocks 1-2, validated.
            {"start_block": 1, "end_block": 2, "asl_seconds": 2.5},
            # PA2.2 says faster cuts: the feel rides verbatim, no ASL
            # invented.
            {"start_block": 1, "end_block": 2,
             "feel": "faster cuts as it builds"}):
        out = mb.enrich_spine(
            _spine([_speech_block(1, 1, 2.285), _speech_block(2, 2, 3.133)],
                   pacing=[dict(window)]),
            {"body_sequence": [_passage(1, end=2.285),
                               _passage(2, start=10.0, end=13.133)]},
            {}, {})
        assert out["audio_spine"]["pacing"] == [window]


def test_pacing_window_refuses_a_number_and_feel_for_the_same_stretch():
    """A window must not turn E3's two representations into two targets."""
    with pytest.raises(ValueError, match="exactly one"):
        mb.enrich_spine(
            _spine([_speech_block(1, 1, 2.285)], pacing=[{
                "start_block": 1, "end_block": 1,
                "asl_seconds": 2.5, "feel": "faster"}]),
            {"body_sequence": [_passage(1, end=2.285)]}, {}, {})


def test_caption_word_limit_survives_and_refuses_non_counts():
    """Finding 31's requested count must reach plan_subtitles unchanged;
    a word ceiling is a positive whole-word count, never a default."""
    out = mb.enrich_spine(
        _spine([_speech_block(1, 1, 2.285)], max_words=2),
        {"body_sequence": [_passage(1, end=2.285)]}, {}, {})
    assert out["audio_spine"]["max_words"] == 2
    for invalid in (2.5, True, 0):
        with pytest.raises(ValueError, match="max_words"):
            mb.enrich_spine(
                _spine([_speech_block(1, 1, 2.285)],
                       max_words=invalid),
                {"body_sequence": [_passage(1, end=2.285)]}, {}, {})


# --------------------------------------------------------------------------
# From test_mesh_spine_duration_zone.py
#
# Tests for the mesh_spine pre-bridge that resolves the duration zone.
#
# The zone was judged by the post-bridge but never shown to the model.
# The pre-bridge now puts the resolved (min, target, max) into context
# alongside a legend, following the MEASUREMENT_LEGEND pattern.

BRIDGE = REPO / "library" / "steps" / "step_2_05_mesh_spine" / "bridge.py"


def _run_bridge(data: dict) -> dict:
    """Run the bridge as a subprocess, the way run_pipeline does."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, str(BRIDGE)],
        input=json.dumps(data),
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 0, f"bridge failed: {proc.stderr}"
    return json.loads(proc.stdout)


class TestDurationZoneReachesBridge:
    """The pre-bridge resolves the zone the post-bridge judges against."""

    def test_brand_template_zone(self):
        """Brand template declares min/max -> zone is derived from that."""
        data = {
            "brand_template": {
                "content": {
                    "target_duration_seconds": {"min": 30, "max": 90}
                }
            }
        }
        out = _run_bridge(data)
        zone = out["duration_zone"]
        assert zone["minimum_seconds"] == 30.0
        assert zone["maximum_seconds"] == 90.0
        assert zone["target_seconds"] == 60.0  # average of min/max

    def test_project_config_takes_precedence_over_brand(self):
        """project_config.target_duration_seconds wins over brand template."""
        data = {
            "project_config": {"target_duration_seconds": 120},
            "brand_template": {
                "content": {
                    "target_duration_seconds": {"min": 30, "max": 90}
                }
            },
        }
        out = _run_bridge(data)
        zone = out["duration_zone"]
        # 120 * 0.9 = 108, 120 * 1.1 = 132
        assert zone["minimum_seconds"] == 108.0
        assert zone["target_seconds"] == 120.0
        assert zone["maximum_seconds"] == 132.0


# --------------------------------------------------------------------------
# From test_spine_passage_coverage.py
#
# Step 2.05's content-loss arithmetic belongs to the script.
#
# The handoff asks the model for "No content loss: Every speech passage
# from body_sequence appears in exactly one speech block" - set coverage
# over small integers. The post-bridge already refused DANGLING refs; the
# other two clerical failures (a passage no block names, a passage two
# speech blocks name) had no check. `validate_passage_coverage` in
# `library/tools/spine_contract.py` owns both, and `enrich_spine` runs
# it before emitting.
#
# A hook reusing a body passage is neither failure: the handoff permits
# it explicitly. Proven below in both directions.

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from library.steps.step_2_05_mesh_spine.post_bridge import (  # noqa: E402
    enrich_spine,
)
from library.tools.spine_contract import (  # noqa: E402
    SpineContractError,
    validate_passage_coverage,
)


def _passage_2(clip_id, start, end, text):
    return {
        "clip_id": clip_id,
        "source_start": start,
        "source_end": end,
        "text": text,
        "alignment_method": "whisperx",
        "word_timestamps": [
            {"word": text.split()[0], "source_start": start,
             "source_end": start + 1.0}
        ],
    }


def _speech_sequence(n):
    return {
        "body_sequence": [
            _passage_2(f"clip_{i:03d}", float(i * 10), float(i * 10 + 2),
                     f"Passage number {i}")
            for i in range(1, n + 1)
        ],
    }


def _spine_2(refs):
    """`refs`: list of (block_type, passage_ref)."""
    return {
        "structure": [
            {"position": i, "block_type": kind,
             "content": {"passage_ref": ref}}
            for i, (kind, ref) in enumerate(refs, start=1)
        ],
    }


# ── The contract function, both directions ───────────────────────────

def test_a_passage_no_block_names_is_content_loss():
    with pytest.raises(SpineContractError) as excinfo:
        validate_passage_coverage(
            _spine_2([("speech", 1)])["structure"], 2)
    assert "[2]" in str(excinfo.value)
    assert "content loss" in str(excinfo.value)


def test_a_passage_in_two_speech_blocks_is_a_repeat():
    with pytest.raises(SpineContractError) as excinfo:
        validate_passage_coverage(
            _spine_2([("speech", 1), ("speech", 1)])["structure"], 1)
    assert "[1]" in str(excinfo.value)
    assert "twice" in str(excinfo.value)


def test_a_hook_covers_but_does_not_double():
    """Hook on 2, speech on 2, body of 2: passage 1 is still lost, and
    the error must say loss, not doubling."""
    with pytest.raises(SpineContractError) as excinfo:
        validate_passage_coverage(
            _spine_2([("hook", 2), ("speech", 2)])["structure"], 2)
    message = str(excinfo.value)
    assert "content loss" in message
    assert "twice" not in message


# ── Wired into enrich_spine, not defined beside it ───────────────────

def test_enrich_spine_refuses_a_dropped_passage_and_accepts_hook_reuse():
    with pytest.raises(SpineContractError):
        enrich_spine(_spine_2([("speech", 1)]), _speech_sequence(2), {}, {})
    result = enrich_spine(
        _spine_2([("hook", 2), ("speech", 1), ("speech", 2)]),
        _speech_sequence(2), {}, {})
    # Each block resolves its passage_ref BY ORDERING into the passage's
    # own clip.
    assert [b["clip_id"] for b in result["audio_spine"]["structure"]] == [
        "clip_002", "clip_001", "clip_002"]


# --------------------------------------------------------------------------
# From test_passage_engagement.py
#
# A missing measurement must never resolve to a value that reads as a
#
# With the engagement judgement absent, the rank is ABSENT (None) - never a
# plausible number - and every reader says it has no basis.
# History: `docs/evidence/passage_engagement.md`.

if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.tools.passage_engagement import (
    NO_ENGAGEMENT_BASIS,
    engagement_basis,
    engagement_rank,
    is_unjudged,
    unjudged_summary,
)
from library.steps.step_5_03_creative_cohesion.step import (
    review_creative_cohesion,
)


# ── No scorer exists to read a missing measurement ────────────────────

class TestNoScorerSubstitutesANumber:
    def test_energy_rms_is_read_nowhere(self):
        """It is emitted nowhere either: `analyze_prosody` produces
        pitch_stats, pitch_contour_10ms, voice_quality, speaking_rate and
        intensity_contour_50ms, and none of them has a key by that name.

        Prose may name it - a withdrawal has to say what it withdrew, so
        comments are stripped before matching and passage_engagement.py,
        which is the record itself, is exempt. What must not come back is
        a READ: a subscript or a `.get`, which is the shape that turned an
        absent dependency into a number."""
        record = REPO / "library" / "tools" / "passage_engagement.py"
        read = re.compile(
            r"""\.get\(\s*["']energy_rms["']|\[\s*["']energy_rms["']\s*\]""")
        hits = [
            f"{p.relative_to(REPO)}:{i}"
            for p in (REPO / "library").rglob("*.py") if p != record
            for i, line in enumerate(p.read_text(errors="ignore").splitlines(), 1)
            if read.search(line.split("#", 1)[0])
        ]
        assert not hits, f"energy_rms is read again at {hits}"


# ── Step 5.03 says it has no basis ────────────────────────────────────

def _inputs(speech_sequence):
    return {
        "creative_direction": {"target_energy": "high"},
        "transition_spec": [],
        "sfx_spec": [],
        "color_grade_spec": {},
        "speech_sequence": speech_sequence,
        "audio_spine": {"structure": [
            {"block_type": "speech", "position": 0,
             "timeline_start": 0.0, "timeline_end": 55.0},
        ]},
    }


def test_a_sequence_with_no_ranks_states_the_absence_and_finds_nothing():
    """No judgement is not a bad judgement, and the review says which."""
    speech = {"body_sequence": [{"text": "a"}, {"text": "b"}]}
    review = review_creative_cohesion(_inputs(speech))
    joined = " ".join(review["warnings"])
    assert NO_ENGAGEMENT_BASIS in joined
    assert "ranked strongest" not in joined
    assert review["adjustments"] == []


# ── An unjudgeable passage reads as unjudged, not as a low score ──────

# The shape step 2.02's handoff asks for when the model cannot place a
# passage: rank null, and the reason in `basis`.
UNJUDGED = {
    "clip_id": "clip_004",
    "text": "uh so anyway",
    "engagement": {
        "rank": None,
        "basis": "half of this passage is wind noise over the mic, so I "
                 "cannot tell whether it holds a viewer",
    },
}

JUDGED = {
    "clip_id": "clip_012",
    "text": "it just matters that it gets posted",
    "engagement": {"rank": 1,
                   "basis": "the line the whole piece is built on"},
}


class TestAnUnjudgedPassageIsNotALowScore:
    """The failure this guards is the one that produced the 49s: a
    passage nobody could judge being read as a passage that judged badly.
    An absent judgement must stay absent through every reader, and the
    REASON must survive so the absence can be stated rather than blanked.
    """

    def test_declining_to_judge_is_not_the_same_as_never_being_asked(self):
        """Both read as absent to `engagement_rank`, and only one has
        something to report. A passage with no key was never asked."""
        assert is_unjudged(UNJUDGED) is True
        assert is_unjudged({"text": "never asked"}) is False
        assert is_unjudged(JUDGED) is False

    def test_the_summary_names_how_many_and_why(self):
        line = unjudged_summary([JUDGED, UNJUDGED, JUDGED])
        assert line.startswith("1 of 3 passage(s) were not judged")
        assert "wind noise" in line
        assert "wind noise" in engagement_basis(UNJUDGED)

    def test_an_absent_or_malformed_rank_is_none_never_zero_or_last(self):
        """N is a rank; None is the absence of one. A reader that cannot
        tell them apart put `Hook engagement (0)` into project 001's
        cohesion review."""
        for passage in ({}, {"engagement": "high"}, None, UNJUDGED):
            assert engagement_rank(passage) is None, passage
        for bad in ("1", 1.5, True, 0, -3, None):
            assert engagement_rank({"engagement": {"rank": bad}}) is None, bad
