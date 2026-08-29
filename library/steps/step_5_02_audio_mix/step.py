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
this step reads.  The one number the arithmetic still needs - how loud
the SPEECH is - is measured nowhere in the pipeline, so the separation
each window will deliver is REPORTED AS UNKNOWN rather than computed
from a guess; `separation_unmeasurable_because` says so on every run.

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
from library.tools.spine_contract import is_speech_block

# Audio level parameters (from style spec)
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
        "base_level_db": -12,
        "notes": "Subtle — felt more than heard",
    },
    "A4_transition_audio": {
        "base_level_db": -10,
        "notes": "Brief, paired with transition visuals",
    },
}

# The behaviour → dB mapping is NOT here. It lives in
# library/tools/music_behavior.py, with the vocabulary it belongs to, so
# this step and `compile_manifest` cannot disagree about what a word
# means or about which words exist.

# The half of the separation arithmetic the pipeline does not have.
# `bed_level_after_gain_lufs` is exact - it is the bed's own integrated
# loudness plus the clip gain the plan applies to it. The separation is
# that number subtracted from the SPEECH's loudness, and no step measures
# the speech's loudness: 1.05 measures prosody, 1.04 measures speech
# REGIONS, and neither reports a level. Naming the gap is the honest
# answer; filling it with a convention would be the same defect one level
# down.
SPEECH_LOUDNESS_IS_UNMEASURED = (
    "the separation a window delivers is speech loudness minus "
    "bed_level_after_gain_lufs, and nothing in the pipeline measures the "
    "loudness of the speech that plays. It would take one ffmpeg "
    "loudnorm pass over the A-roll ranges `a_roll_assignments` names - "
    "the same pass `music_measurement.loudness` already runs on a "
    "candidate, about 3s per track on project 001. Until it exists, "
    "render_qa measures the separation on the finished master and this "
    "plan cannot predict it."
)


def bed_reading(music_selection: dict) -> dict:
    """What is known about the bed that will play, or a stated absence.

    Reads `music_selection.measurements`, which step 2.04's post-bridge
    folds on from the candidate it chose. An unmeasured bed reports
    `measured: False` and its reason - never a level of 0.
    """
    selection = music_selection or {}
    measurements = selection.get("measurements") or {}
    reading = {
        "title": selection.get("title") or "",
        "audio_path": selection.get("audio_path") or "",
        "measured": bool(measurements.get("measured")),
        "measurement_note": measurements.get("measurement_note") or "",
    }
    if not reading["measured"]:
        if not measurements:
            reading["measurement_note"] = (
                "music_selection carries no `measurements` key. Step 2.04 "
                "writes one on every run; a selection recorded before it "
                "did has none, and re-running 2.04 is what supplies it."
            )
        return reading

    for key in ("integrated_lufs", "loudness_range_lu", "true_peak_dbtp",
                "rms_spread_db", "window_spread_db", "speech_band_ratio_db"):
        if key in measurements:
            reading[key] = measurements[key]
    return reading


def _bed_level_after_gain(bed: dict, level_db) -> float:
    """Where the clip gain puts the bed, in LUFS. None when unmeasured."""
    integrated = bed.get("integrated_lufs")
    if not bed.get("measured") or not isinstance(integrated, (int, float)):
        return None
    if not isinstance(level_db, (int, float)):
        return None
    return round(float(integrated) + float(level_db), 2)


def define_audio_mix(audio_spine: dict, music_selection: dict) -> dict:
    """
    Define the audio mix specification from the spine and the chosen bed.
    """
    structure = audio_spine.get("structure", [])
    bed = bed_reading(music_selection)

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
            "bed_level_after_gain_lufs": _bed_level_after_gain(bed, level_db),
            # What the plan asks the ear to hear. Undeclared today; the
            # number is the captain's, not the engine's.
            "separation_target_db": separation_target_db(behavior),
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
            "separation_unmeasurable_because": SPEECH_LOUDNESS_IS_UNMEASURED,
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

    result = define_audio_mix(audio_spine, music_selection)
    json.dump(result, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
