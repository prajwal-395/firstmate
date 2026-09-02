"""The plan says how long a sound plays, inside what the sound measures.

Step 4.04's schema was `{spine_block_position, sfx_id, volume_level,
rationale}` and the sound then ran its own full length.  On the run of
record (001, 2026-08-26) the plan asked for **0.25 s** of a swish under
a defocus blur; the file behind it, `whoosh_impact.mp3`, measures
**8.04 s** with its transient at **0.714 s**.  There was nowhere in the
schema to put the 0.25.

These tests hold what replaced that:

  * the run of record's own request is honoured end to end - the plan
    carries it, the schema accepts it, and the resolved entry runs for
    exactly that long;
  * a length past what the file measures is REFUSED BY NAME, and
    nothing is clamped;
  * a plan declaring no length plays the whole sound, which is the
    absence of a decision;
  * a sound cut short carries the one-frame de-click ramp all the way
    to the OTIO keyframes that deliver the mix.

The library here is built under `tmp_path` and carries
`whoosh_impact.mp3`'s REAL measured numbers - 8.04 s, `punchy`,
transient 0.714 - so the run-of-record arithmetic is reproduced without
any test reaching the captain's library (AGENTS.md 8).
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SFX_STEP = REPO / "library" / "steps" / "step_4_04_plan_sfx"

from library.tools.otio_mix import declick_curve, mix_targets  # noqa: E402
from library.tools.sfx_duration import (  # noqa: E402
    DECLICK_FADE_FRAMES,
    MIN_PLAYED_FRAMES,
    SfxDurationRefused,
    declick_fade_seconds,
    resolve_played_seconds,
)
from library.tools.sfx_library import load_sfx_catalog  # noqa: E402

# The run of record, exactly.
WHOOSH = "whoosh_impact.mp3"
WHOOSH_SECONDS = 8.04
WHOOSH_TRANSIENT = 0.714
RUN_OF_RECORD_REQUEST = 0.25
FPS = 30.0


@pytest.fixture
def library(tmp_path):
    """`whoosh_impact.mp3`'s measurements, on a library of our own."""
    lib = tmp_path / "sfx"
    lib.mkdir()
    audio = lib / WHOOSH
    audio.write_bytes(b"ID3\x03\x00\x00\x00")
    (lib / "sfx_index.json").write_text(json.dumps([{
        "file": WHOOSH,
        "path": str(audio),
        "folder_category": "Whoosh",
        "description": "A sharp air movement resolving into a low impact.",
        "technical": {
            "basic": {"duration": WHOOSH_SECONDS},
            "energy_profile": {"envelope_shape": "punchy"},
        },
        "transient_offset_sec": WHOOSH_TRANSIENT,
    }]), encoding="utf-8")
    return lib


@pytest.fixture
def entry(library):
    catalog = load_sfx_catalog(str(library))
    assert len(catalog) == 1
    return catalog[0]


# ── The bound ─────────────────────────────────────────────────────────

def test_the_run_of_record_request_is_honoured(entry):
    """0.25 s of an 8.04 s whoosh, which is what the plan asked for."""
    source_in = WHOOSH_TRANSIENT          # `punchy`, so the trim applies
    played = resolve_played_seconds(entry, source_in, RUN_OF_RECORD_REQUEST,
                                    FPS)
    assert played == RUN_OF_RECORD_REQUEST


def test_a_length_past_the_measured_one_is_refused_not_clamped(entry):
    """The whole point: a refusal names the numbers, and returns nothing."""
    with pytest.raises(SfxDurationRefused) as raised:
        resolve_played_seconds(entry, 0.0, WHOOSH_SECONDS + 0.5, FPS)
    message = str(raised.value)
    assert WHOOSH in message
    assert "8.04" in message
    assert "Nothing is clamped" in message


def test_the_transient_trim_is_inside_the_bound(entry):
    """`punchy` skips 0.714 s, so 8.04 s is no longer askable."""
    with pytest.raises(SfxDurationRefused) as raised:
        resolve_played_seconds(entry, WHOOSH_TRANSIENT, WHOOSH_SECONDS, FPS)
    assert "after the transient trim" in str(raised.value)
    # ...and everything that is left, is.
    remaining = WHOOSH_SECONDS - WHOOSH_TRANSIENT
    assert resolve_played_seconds(
        entry, WHOOSH_TRANSIENT, remaining, FPS) == pytest.approx(remaining)


def test_declaring_no_length_plays_the_whole_sound(entry):
    """The ABSENCE of a decision, not a decision - and the old behaviour."""
    assert resolve_played_seconds(entry, 0.0, None, FPS) == WHOOSH_SECONDS
    assert resolve_played_seconds(
        entry, WHOOSH_TRANSIENT, None, FPS) == pytest.approx(
            WHOOSH_SECONDS - WHOOSH_TRANSIENT)


def test_the_only_floor_is_the_timebase(entry):
    """Two frames, because a shorter slice is a ramp rather than a sound.

    Mechanical, and it admits the run of record's 0.25 s with room to
    spare. There is no minimum "audible" length here (AGENTS.md 10.5).
    """
    floor = MIN_PLAYED_FRAMES / FPS
    assert RUN_OF_RECORD_REQUEST > floor
    assert resolve_played_seconds(entry, 0.0, floor, FPS) == floor
    with pytest.raises(SfxDurationRefused) as raised:
        resolve_played_seconds(entry, 0.0, floor / 2, FPS)
    assert "not a minimum length for a sound" in str(raised.value)


def test_a_length_that_is_not_a_number_is_refused(entry):
    for value in ("0.25", True, [0.25]):
        with pytest.raises(SfxDurationRefused):
            resolve_played_seconds(entry, 0.0, value, FPS)


# ── The click ─────────────────────────────────────────────────────────

def test_a_truncated_sound_gets_a_ramp_and_a_whole_one_does_not(entry):
    playable = WHOOSH_SECONDS - WHOOSH_TRANSIENT
    cut_short = declick_fade_seconds(RUN_OF_RECORD_REQUEST, playable, FPS)
    assert cut_short == pytest.approx(DECLICK_FADE_FRAMES / FPS)
    assert declick_fade_seconds(playable, playable, FPS) == 0.0


def test_the_ramp_reaches_the_keyframes_that_deliver_the_mix():
    """`otio_mix` is the one route a dB reaches Fairlight (AGENTS.md 5).

    A static level would step to silence at the out point; the curve
    holds the level and then ramps over the last frame.
    """
    frames = int(round(RUN_OF_RECORD_REQUEST * FPS))
    curve = declick_curve(DECLICK_FADE_FRAMES / FPS, fps=FPS,
                          clip_frame_count=frames, level_db=-18.0)
    assert curve[0] == -18.0
    assert curve[frames - 1 - DECLICK_FADE_FRAMES] == -18.0
    assert curve[frames - 1] == -100.0
    # A sound that plays to its own end has nothing to ramp.
    assert declick_curve(0.0, fps=FPS, clip_frame_count=frames,
                         level_db=-18.0) == {}


# ── End to end, through the step the plan is written in ───────────────

def _payload(library, duration=None):
    """The run of record's own entry, on a one-block spine."""
    sfx = {
        "spine_block_position": 1,
        "sfx_id": WHOOSH,
        "volume_db": -18,
        "rationale": "under the defocus blur, where the talking head ends",
    }
    if duration is not None:
        sfx["duration_seconds"] = duration
    return {
        "sfx_creative": [sfx],
        "timed_spine": {"structure": [{
            "position": 1,
            "block_type": "speech",
            "clip_id": "clip_001",
            "source_start": 0.0,
            "source_end": 20.0,
            "timeline_start": 34.615,
            "timeline_end": 54.615,
            "word_timestamps": [],
            "alignment_method": "whisperx",
        }]},
        "temporal_event_indices": [],
        "music_analysis": {},
        "music_selection": {"audio_path": ""},
        "project_fps": FPS,
        "creative_direction": {},
    }


def _run_post_bridge(library, payload):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO) + os.pathsep + env.get("PYTHONPATH", "")
    env["PIPELINE_SFX_LIBRARY"] = str(library)
    return subprocess.run(
        [sys.executable, str(SFX_STEP / "post_bridge.py")],
        input=json.dumps(payload), capture_output=True, text=True,
        encoding="utf-8", cwd=str(REPO), env=env,
    )


def test_the_step_resolves_the_run_of_record_request(library):
    """The plan, the schema value, and the resulting duration."""
    proc = _run_post_bridge(library, _payload(library,
                                              RUN_OF_RECORD_REQUEST))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    placed = json.loads(proc.stdout)["sfx_spec"]["sfx_list"][0]

    assert placed["sfx_id"] == WHOOSH
    assert placed["duration_seconds"] == RUN_OF_RECORD_REQUEST
    assert placed["source_in"] == pytest.approx(WHOOSH_TRANSIENT)
    assert (placed["timeline_out"] - placed["timeline_in"]
            == pytest.approx(RUN_OF_RECORD_REQUEST))
    assert placed["played_whole_sound"] is False
    assert placed["fade_out_seconds"] == pytest.approx(
        DECLICK_FADE_FRAMES / FPS, abs=1e-4)


def test_the_step_refuses_a_length_the_sound_has_not_got(library):
    """Refused by name, at PLAN time, with nothing written."""
    proc = _run_post_bridge(library, _payload(library, 12.0))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    error = json.loads(proc.stdout)["error"]
    assert WHOOSH in error
    assert "12.0" in error and "8.04" in error
    assert "Nothing is clamped" in error


def test_the_step_still_plays_the_whole_sound_when_none_is_asked(library):
    proc = _run_post_bridge(library, _payload(library, None))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    placed = json.loads(proc.stdout)["sfx_spec"]["sfx_list"][0]
    assert placed["duration_seconds"] == pytest.approx(
        WHOOSH_SECONDS - WHOOSH_TRANSIENT)
    assert placed["played_whole_sound"] is True
    assert placed["fade_out_seconds"] == 0.0


def test_the_manifest_carries_the_ramp_to_the_mix_route():
    """Manifest -> `mix_targets` -> keyframes, which is the real seam.

    A capability is only real where the renderer reads it
    (AGENTS.md 10.2), and for a dB that reader is `otio_mix`.
    """
    frames = int(round(RUN_OF_RECORD_REQUEST * FPS))
    manifest = {"tracks": {"A3": {"clips": [
        {"source_file": "/sfx/whoosh_impact.mp3", "label": "sfx_001",
         "timeline_in_frame": 1038, "timeline_out_frame": 1038 + frames,
         "volume_db": -18, "fade_out_seconds": DECLICK_FADE_FRAMES / FPS},
        {"source_file": "/sfx/riser_2.mp3", "label": "sfx_002",
         "timeline_in_frame": 1384, "timeline_out_frame": 1474,
         "volume_db": -14, "fade_out_seconds": 0.0},
    ]}}}
    cut_short, whole = mix_targets(manifest, fps=FPS)

    assert cut_short["role"] == "sfx"
    assert cut_short["keyframes"][frames - 1] == -100.0
    assert cut_short["keyframes"][frames - 1 - DECLICK_FADE_FRAMES] == -18.0
    # A sound that plays to its own end keeps the static level it had.
    assert whole["keyframes"] == {}
    assert whole["level_db"] == -14.0
