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
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "audio_mix_step", os.path.join(MIX, "step.py"))
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


def test_the_withdrawn_values_are_recorded_rather_than_renumbered():
    assert WITHDRAWN_VOLUME_LADDER == {
        "subtle": -18, "low": -14, "medium": -10, "prominent": -6}
    source = open(os.path.join(SFX, "post_bridge.py"),
                  encoding="utf-8").read()
    assert "VOLUME_MAP = {" not in source
    for value in ("-18", "-14", "-10", "-6"):
        assert f'"subtle": {value}' not in source


def test_no_layer_substitutes_a_level():
    for path in (os.path.join(SFX, "post_bridge.py"),
                 os.path.join(REPO, "library", "steps",
                              "step_5_04_compile_manifest", "step.py")):
        source = open(path, encoding="utf-8").read()
        assert 'get("volume_db", -14)' not in source
        assert "get('volume_db', -14)" not in source


# ── What replaces them ───────────────────────────────────────────────

def test_the_level_is_the_plans_own_number():
    assert read_volume_db({"volume_db": -4.5}) == (-4.5, "")
    assert read_volume_db({"volume_db": 0}) == (0.0, "")


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


def test_the_model_is_told_what_the_sound_sits_under():
    """It cannot reason about a relationship it cannot see.

    The definition used to travel beside the candidate table as
    ``SPEECH_REFERENCE_LEGEND`` because 4.04's ``handoff.md`` was under
    the captain's freeze. The freeze lifted 2026-09-09, so it is
    asserted where the model reads it.
    """
    handoff = _handoff()
    assert "0 dB" in handoff
    assert "`bed_under_it`" in handoff
    assert "yours to choose" in handoff
    # And nothing in that section states a target.
    lowered = handoff.lower()
    section = lowered[lowered.index("what a level is measured against"):]
    for banned in ("should be at", "aim for", "typically"):
        assert banned not in section


def test_the_definition_does_not_also_travel_as_data():
    """A definition living in the prompt AND beside the table is worse
    than either: one of them rots and nothing says which."""
    source = open(os.path.join(SFX, "bridge.py"), encoding="utf-8").read()
    assert "SPEECH_REFERENCE_LEGEND" not in source
    assert "sfx_candidates_legend" not in source


def test_the_withdrawn_volume_ladder_is_gone_from_the_prompt():
    """The freeze lifted, so the correction was applied rather than
    carried: the prompt no longer ASKS for `volume_level` or offers the
    four-word ladder, and it says `volume_db` is a number."""
    handoff = _handoff()
    assert "`volume_db`" in handoff
    assert "WITHDRAWN" in handoff, (
        "the prompt should still record that the ladder was withdrawn - "
        "a word that silently vanishes teaches nothing")
    for ladder_ask in ("carries its own volume_level",
                       "Volume levels: `subtle`"):
        assert ladder_ask not in handoff, (
            f"the prompt still asks for the withdrawn ladder: "
            f"{ladder_ask!r}")


# ── The case the captain named ───────────────────────────────────────

CAMERA_CLICK = {
    "sfx_id": "camera soft click.wav",
    "file": "0.459 s, peak -6.4 dB, mean -36.6 dB, transient at the head "
            "(-29.7 dB at 0.00 s decaying to -36.6 dB by 0.37 s)",
    "placement": "source_in 0.0 for the full duration - the right part "
                 "played",
}


def test_the_camera_click_before_and_after(capsys):
    """Before: two engine numbers stacked on a quiet file. After: one
    number, and it is the plan's."""
    before = {
        "audio_mix.track_levels.A3_sfx.base_level_db": -12,
        "audio_mix.track_levels.A3_sfx.notes": "Subtle - felt more than heard",
        "tracks.A3.clips[0].volume_db": -14,
        "how the -14 was reached": "the model said volume_level 'low' and "
                                   "VOLUME_MAP resolved it",
        "who chose either number": "nobody - both are engine constants",
        "what it sits under": "speech at the 0 dB reference, bed at -18 dB",
    }
    mix = _mix_module()
    after = {
        "audio_mix.track_levels.A3_sfx.base_level_db": (
            "REMOVED" if "base_level_db" not in mix.TRACK_LEVELS["A3_sfx"]
            else "still there"),
        "audio_mix.track_levels.A4_transition_audio": (
            "REMOVED" if "A4_transition_audio" not in mix.TRACK_LEVELS
            else "still there"),
        "tracks.A3.clips[0].volume_db": "whatever the plan wrote, verbatim",
        "how it is reached": "the model writes volume_db in dB; there is no "
                             "ladder and no default",
        "if the plan names none": "the entry is dropped with the reason - "
                                  "no level is substituted",
        "what the model is told it sits under": (
            "speech at the 0 dB reference (speech_reference_db) and the "
            "bed's own measured level under that block (bed_under_it)"),
    }
    assert after["audio_mix.track_levels.A3_sfx.base_level_db"] == "REMOVED"
    assert after["audio_mix.track_levels.A4_transition_audio"] == "REMOVED"
    with capsys.disabled():
        print("\n── the camera-click case ──")
        print(json.dumps({"the sound": CAMERA_CLICK,
                          "before": before, "after": after}, indent=2))


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
