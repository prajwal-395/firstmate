"""The sound effect's level is the model's, and no number replaces it.

Captain, station 9: *"a single sfx that i dont even think played the right
part of the sfx and instead it was just silent"*.

The experience is right and the mechanism is not silence.  The file is
fine, the placement is fine, and it was made inaudible afterwards by two
taste values written into the engine - a track level of -12 dB annotated
*"Subtle - felt more than heard"* and a four-word ladder resolving the
model's word into -18/-14/-10/-6.  Both are gone, neither is renumbered,
and `library/tools/sfx_level.py` carries the record.
"""

import json
import os
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from library.tools.sfx_level import (  # noqa: E402
    SfxLevelRefused,
    WITHDRAWN_TRACK_LEVELS,
    WITHDRAWN_VOLUME_LADDER,
    read_volume_db,
)

SFX = os.path.join(REPO, "library", "steps", "step_4_04_plan_sfx")
MIX = os.path.join(REPO, "library", "steps", "step_5_02_audio_mix")


def _mix_module():
    """Step 5.02's shared half, which owns `TRACK_LEVELS`.

    It was `step.py` until 2026-09-16, when the step became hybrid and
    the deterministic half moved to `mix.py`.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "audio_mix_mix", os.path.join(MIX, "mix.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── The two taste values, gone ───────────────────────────────────────

def test_no_track_level_states_how_loud_a_sound_should_be():
    levels = _mix_module().TRACK_LEVELS
    assert "A4_transition_audio" not in levels
    assert "base_level_db" not in levels["A3_sfx"]
    assert "felt more than heard" not in json.dumps(levels).replace(
        WITHDRAWN_TRACK_LEVELS["A3_sfx.base_level_db"], "")


def test_no_layer_substitutes_a_level():
    for path in (os.path.join(SFX, "post_bridge.py"),
                 os.path.join(REPO, "library", "steps",
                              "step_5_04_compile_manifest", "step.py")):
        source = open(path, encoding="utf-8").read()
        assert 'get("volume_db", -14)' not in source
        assert "get('volume_db', -14)" not in source


# ── What replaces them ───────────────────────────────────────────────


def test_an_entry_naming_no_level_is_dropped_with_the_reason():
    level, reason = read_volume_db({"sfx_id": "click.wav"})
    assert level is None
    assert "no volume_db" in reason


def test_a_level_outside_what_a_clip_can_carry_is_refused_not_clamped():
    with pytest.raises(SfxLevelRefused, match="not clamped"):
        read_volume_db({"volume_db": -400})


def _handoff() -> str:
    return " ".join(open(
        os.path.join(SFX, "handoff.md"), encoding="utf-8").read().split())


# ── The case the captain named ───────────────────────────────────────

CAMERA_CLICK = {
    "sfx_id": "camera soft click.wav",
    "file": "0.459 s, peak -6.4 dB, mean -36.6 dB, transient at the head "
            "(-29.7 dB at 0.00 s decaying to -36.6 dB by 0.37 s)",
    "placement": "source_in 0.0 for the full duration - the right part "
                 "played",
}


def test_the_plan_step_really_places_the_level_the_model_wrote(tmp_path):
    """End to end through step 4.04's own post-bridge."""
    library = tmp_path / "sfx"
    library.mkdir()
    sound = library / "camera soft click.wav"
    sound.write_bytes(b"RIFF....WAVEfmt ")
    (library / "sfx_index.json").write_text(json.dumps([{
        "file": "camera soft click.wav",
        "path": str(sound),
        "folder_category": "Accents",
        "description": "a soft camera shutter",
        "technical": {
            "basic": {"duration": 0.459},
            "energy_profile": {"envelope_shape": "fading"},
        },
        "transient_offset_sec": 0.0,
    }]), encoding="utf-8")

    payload = {
        "timed_spine": {"frame_rate": 30.0, "structure": [
            {"position": 1, "block_type": "speech", "clip_id": "clip_1",
             "timeline_start": 0.0, "timeline_end": 10.0,
             "source_start": 0.117, "source_end": 10.117,
             "music_behavior": "background", "word_timestamps": [],
             "alignment_method": "whisperx",
             "content": {"clip_id": "clip_1"}}]},
        "temporal_event_indices": [], "music_analysis": {},
        "music_selection": {},
        "sfx_creative": [
            {"spine_block_position": 1, "sfx_id": "camera soft click.wav",
             "volume_db": -4, "rationale": "the shutter under the line"},
            {"spine_block_position": 1, "sfx_id": "camera soft click.wav",
             "rationale": "named no level"},
        ],
    }
    env = dict(os.environ, PIPELINE_SFX_LIBRARY=str(library))
    env["PYTHONPATH"] = REPO + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, os.path.join(SFX, "post_bridge.py")],
        input=json.dumps(payload), capture_output=True, text=True,
        encoding="utf-8", env=env, cwd=REPO, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    placed = json.loads(proc.stdout)["sfx_spec"]["sfx_list"]
    assert [p["volume_db"] for p in placed] == [-4.0], (
        "the level the model wrote is placed verbatim, and the entry that "
        "named none is dropped rather than given one")
    assert "names no volume_db" in proc.stderr
