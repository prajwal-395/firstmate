"""Speech owns the lower half of the zone; the upper half is non-speech's.

Findings 9 and 30, execution-frontier report 2026-09-24: step 2.02 held
SPEECH alone to the full target zone and step 2.05 then held the TOTAL
to the same zone, so nothing was left for b-roll, breaths or beats -
at a 30 s target 30.3 s of speech passed 2.02 and left 2.7 s for
everything else. And the too-short refusal said "Select fewer or
shorter passages", pointing exactly the wrong way.

The contract these tests pin:

* 2.02's speech ceiling is the declared TARGET, not the zone max: the
  band above the target is the room the breaths, intro/outro and
  music/picture blocks step 2.05 adds extend into.
* Each refusal direction names its own fix: too long cuts, too short
  extends. The too-short message never says "fewer or shorter".
* 2.05 still holds the TOTAL to the full zone - that half is unchanged.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import library.steps.step_2_02_speech_sequence.post_bridge as pb  # noqa: E402
from library.tools.duration_targets import (  # noqa: E402
    get_speech_duration_zone,
    get_target_duration_zone,
)


def _data(target):
    return {"project_config": {"target_duration_seconds": target}}


# ── The B6 shape: 30.3 s of speech at a 30 s target ─────────────────

def test_speech_past_the_target_is_refused_early_not_at_2_05():
    """30.3 s passed 2.02 under the old full-zone gate and died at 2.05
    holding a 3 s breath. Now 2.02 refuses it, where the model that
    chose the passages can still cut."""
    speech_zone = get_speech_duration_zone(_data(30))
    total_zone = get_target_duration_zone(_data(30))
    with pytest.raises(pb.SpeechDurationError) as excinfo:
        pb.refuse_out_of_zone_sequence(30.3, speech_zone, total_zone)
    message = str(excinfo.value)
    assert "30.3s" in message
    assert "fewer or shorter" in message
    assert "30.0-33.0s" in message


def test_speech_with_room_above_the_target_passes():
    """28 s at a 30 s target leaves the 30-33 s band for what extends
    the total. Nothing to refuse."""
    speech_zone = get_speech_duration_zone(_data(30))
    total_zone = get_target_duration_zone(_data(30))
    assert pb.refuse_out_of_zone_sequence(
        28.0, speech_zone, total_zone) is None


# ── Finding 30: the too-short refusal points the right way ──────────

def test_a_too_short_sequence_is_told_to_extend_not_cut():
    """40.1 s of speech for a 60 s target: the old message said
    "Select fewer or shorter passages" when the sequence was too
    SHORT. The refusal now says MORE or LONGER."""
    speech_zone = get_speech_duration_zone(_data(60))
    total_zone = get_target_duration_zone(_data(60))
    with pytest.raises(pb.SpeechDurationError) as excinfo:
        pb.refuse_out_of_zone_sequence(40.1, speech_zone, total_zone)
    message = str(excinfo.value)
    assert "40.1s" in message
    assert "MORE or LONGER" in message
    assert "fewer or shorter" not in message
