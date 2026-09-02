#!/usr/bin/env python3
"""
Step 5.2: Define Audio Mix Specification

Define the audio mix parameters — volume levels and spatial
positioning for each audio track. Translates the spine's music_behavior
values into concrete dB levels.

Classification: Deterministic / Specification
Input:  { "audio_spine": {...}, "music_selection": {...} }
Output: { "audio_mix_spec": { track_levels, music_automation, bed, ... } }

**A clip gain is not a separation, and this step can now see the
difference.**  Every level below is a RELATIVE dB applied to whatever
level the music file already carries, so whether the planned offset
lands is decided by the bed's own loudness.  Step 2.04 measures that
(`library/tools/music_measurement.py`) and the chosen track carries its
own scalars forward as `music_selection.measurements`, which is what
this step reads.  The other number the arithmetic needs - how loud the
SPEECH is - is now MEASURED, by one ffmpeg `loudnorm` pass over the
ranges `a_roll_assignments` names (`library/tools/speech_loudness.py`,
about 0.23 s per block; 1.2 s for 001's eight).  So each window records
the separation it will DELIVER.

What no step supplies is the separation a window OUGHT to deliver:
`music_behavior.SEPARATION_TARGETS_DB` is empty and the master loudness
target is an open captain decision.  `separation_target_is_undeclared`
says so on every run.

Nothing here chooses a level.  The five clip gains are
`library/tools/music_behavior.py`'s and are a registered open decision;
this step wires the bed's measurements to them and states what is
missing, so that whoever owns the numbers has something to own.
"""
import json
import os
import sys

# Add parent directories to path so we can import shared tools.
# Both `library/` (for `tools.x`) and the repo root (for `library.tools.x`,
# which the shared tools use to import each other).
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..'))
from library.tools.music_behavior import (
    UNDECLARED_SEPARATION,
    music_level_db,
    resolve_music_behavior,
    separation_target_db,
)
from library.tools.music_measurement import (
    bed_level_after_gain,
    bed_reading,
)
from library.tools.speech_loudness import (
    NO_TARGET_IS_SUPPLIED,
    measure_speech_blocks,
    separation_delivered_db,
)
from library.tools.sfx_level import WITHDRAWN_TRACK_LEVELS
from library.tools.spine_contract import is_speech_block

# Audio level parameters.
#
# A3_sfx and A4_transition_audio are GONE, and were not renumbered. They
# carried `base_level_db` -12 and -10 with the notes "Subtle - felt more
# than heard" and "Brief, paired with transition visuals": a creative
# brief written into the engine and applied to every sound of every
# project, which nothing read and which nonetheless stated a taste in the
# shipped manifest as though the mix had been decided. How loud a sound
# plays is now the plan's own `volume_db`, and the record of what was
# withdrawn is library/tools/sfx_level.WITHDRAWN_TRACK_LEVELS.
#
# A1_speech's 0 dB is the mix's REFERENCE - the definition every other
# level is relative to - not a chosen level. A2_music's two numbers are
# `music_behavior`'s, which is a registered open captain decision.
TRACK_LEVELS = {
    "A1_speech": {
        "base_level_db": 0,
        "compressor": True,
        "notes": "Speech is the reference — everything else is relative",
    },
    "A2_music": {
        "prominent_level_db": -6,
        "background_level_db": -18,
        "fade_duration_seconds": 1.0,
    },
    "A3_sfx": {
        "levels_are_per_sound": True,
        "notes": WITHDRAWN_TRACK_LEVELS["A3_sfx.base_level_db"],
    },
}

# The behaviour → dB mapping is NOT here. It lives in
# library/tools/music_behavior.py, with the vocabulary it belongs to, so
# this step and `compile_manifest` cannot disagree about what a word
# means or about which words exist.

# Both halves of the separation arithmetic are now measured.
# `bed_level_after_gain_lufs` is the bed's own integrated loudness plus
# the clip gain the plan applies to it; `speech_lufs` is one ffmpeg
# loudnorm pass over the range `a_roll_assignments` says this block
# plays. Their difference is `separation_delivered_db`, and it is
# arithmetic over two measurements rather than a prediction from a
# convention.
#
# What is STILL not supplied is the separation a window ought to
# deliver. That is the same registered captain decision as the five clip
# gains, and an engine-supplied one would be a strength nobody chose
# arriving one level up (AGENTS.md 10.4, 10.5).
SEPARATION_TARGET_IS_UNDECLARED = (
    "the separation each window DELIVERS is now measured - speech "
    "loudness minus bed_level_after_gain_lufs, both from real "
    "measurements. What no step supplies is the separation a window "
    "OUGHT to deliver: music_behavior.SEPARATION_TARGETS_DB is empty and "
    "the master loudness target is an open captain decision. Until one "
    "is declared, a check judging against the clip gain is judging "
    "against a number that was never a separation target."
)


def _speech_lufs(speech: dict, block: dict):
    """The measured loudness of this block's speech, or None."""
    reading = speech.get(block.get("position")) or {}
    return reading.get("integrated_lufs") if reading.get("measured") else None


def define_audio_mix(audio_spine: dict, music_selection: dict,
                     a_roll_assignments=None) -> dict:
    """
    Define the audio mix specification from the spine and the chosen bed.

    `a_roll_assignments` names the source ranges the edit really plays,
    which is what makes the speech measurable. A caller that has none
    still gets the whole spec; every window then records `speech_lufs`
    as an admitted absence rather than a level.
    """
    structure = audio_spine.get("structure", [])
    bed = bed_reading(music_selection)
    # One loudnorm pass per speech block. See
    # library/tools/speech_loudness.py for the measured cost.
    speech = measure_speech_blocks(a_roll_assignments)

    # Build music automation from spine blocks
    music_automation = []
    for block in structure:
        # A block that planned a behaviour keeps it, whatever it is - a
        # planned `silent` is a decision, not a gap to fill. Only a block
        # that planned nothing (a bookend card, which never passes through
        # the spine's LLM) takes the default.
        behavior = resolve_music_behavior(
            block.get("music_behavior"),
            block_carries_speech=is_speech_block(block))
        level_db = music_level_db(behavior)
        music_automation.append({
            "spine_block_position": block["position"],
            "timeline_start": block.get("timeline_start", 0.0),
            "timeline_end": block.get("timeline_end", 0.0),
            "music_behavior": behavior,
            # The clip gain: how far the bed is pushed down from its own
            # level. This is the number the OTIO route delivers.
            "target_level_db": level_db,
            # Where that gain puts the bed, given what the bed measures.
            # None when nothing measured it, never 0.
            "bed_level_after_gain_lufs": bed_level_after_gain(bed, level_db),
            # What the plan asks the ear to hear. Undeclared today; the
            # number is the captain's, not the engine's.
            "separation_target_db": separation_target_db(behavior),
            # How loud the speech under this window is, measured over the
            # range a_roll_assignments says it plays. None with a stated
            # reason when nothing measured it - never 0, which would read
            # as silence.
            "speech_lufs": _speech_lufs(speech, block),
            "speech_loudness": speech.get(block["position"]),
            # The separation this window WILL deliver. Arithmetic over
            # two measurements; compared with nothing.
            "separation_delivered_db": separation_delivered_db(
                _speech_lufs(speech, block),
                bed_level_after_gain(bed, level_db)),
        })

    # --- Verification ---
    # Every spine block's music_behavior is translated
    for ma in music_automation:
        assert ma["target_level_db"] is not None, \
            f"No dB level for block {ma['spine_block_position']}"

    # Speech is loudest when present (check music background < speech 0)
    for ma in music_automation:
        if ma["music_behavior"] == "background":
            assert ma["target_level_db"] < 0, \
                f"Music background level ({ma['target_level_db']}dB) not below speech (0dB)"

    if bed["measured"]:
        print(
            f"  Bed: {bed.get('title') or 'untitled'} at "
            f"{bed.get('integrated_lufs')} LUFS integrated, speech-band "
            f"ratio {bed.get('speech_band_ratio_db')} dB.",
            file=sys.stderr,
        )
    else:
        print(f"  Bed level unknown: {bed['measurement_note']}",
              file=sys.stderr)

    return {
        "audio_mix_spec": {
            "track_levels": TRACK_LEVELS,
            "music_automation": music_automation,
            "bed": bed,
            "separation_targets_declared": False,
            "separation_target_note": UNDECLARED_SEPARATION,
            "separation_target_is_undeclared": SEPARATION_TARGET_IS_UNDECLARED,
            "speech_loudness_note": NO_TARGET_IS_SUPPLIED,
            "master_limiter": {
                "threshold_db": -1.0,
                "enabled": True,
            },
            "notes": (
                "Speech (A1) is the reference at 0dB. Music plays at "
                f"{TRACK_LEVELS['A2_music']['background_level_db']}dB under speech, "
                f"rises to {TRACK_LEVELS['A2_music']['prominent_level_db']}dB during "
                "non-speech moments. Fade duration: "
                f"{TRACK_LEVELS['A2_music']['fade_duration_seconds']}s. "
                "Those are CLIP GAINS against the bed's own level, not the "
                "separation the ear hears - see `bed` for what the bed "
                "measures and `separation_target_note` for what is not "
                "declared."
            ),
        },
    }


def main():
    input_data = json.loads(sys.stdin.read())
    audio_spine = input_data.get("audio_spine", {})
    music_selection = input_data.get("music_selection", {})

    result = define_audio_mix(
        audio_spine, music_selection,
        input_data.get("a_roll_assignments"))
    json.dump(result, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
