#!/usr/bin/env python3
"""
Step 5.2: Define Audio Mix Specification

Define the audio mix parameters — volume levels and spatial
positioning for each audio track. Translates the spine's music_behavior
values into concrete dB levels.

Classification: Deterministic / Specification
Input:  { "audio_spine": {...}, "enhancement_spec": {...} }
Output: { "audio_mix_spec": { track_levels, music_automation, ... } }
"""
import json
import os
import sys

# Add parent directories to path so we can import shared tools.
# Both `library/` (for `tools.x`) and the repo root (for `library.tools.x`,
# which the shared tools use to import each other).
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..'))
from library.tools.music_behavior import music_level_db, resolve_music_behavior
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


def define_audio_mix(audio_spine: dict, enhancement_spec: dict) -> dict:
    """
    Define the audio mix specification from spine + enhancement data.
    """
    structure = audio_spine.get("structure", [])

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
        music_automation.append({
            "spine_block_position": block["position"],
            "timeline_start": block.get("timeline_start", 0.0),
            "timeline_end": block.get("timeline_end", 0.0),
            "music_behavior": behavior,
            "target_level_db": music_level_db(behavior),
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

    return {
        "audio_mix_spec": {
            "track_levels": TRACK_LEVELS,
            "music_automation": music_automation,
            "master_limiter": {
                "threshold_db": -1.0,
                "enabled": True,
            },
            "notes": (
                "Speech (A1) is the reference at 0dB. Music plays at "
                f"{TRACK_LEVELS['A2_music']['background_level_db']}dB under speech, "
                f"rises to {TRACK_LEVELS['A2_music']['prominent_level_db']}dB during "
                "non-speech moments. Fade duration: "
                f"{TRACK_LEVELS['A2_music']['fade_duration_seconds']}s."
            ),
        },
    }


def main():
    input_data = json.loads(sys.stdin.read())
    audio_spine = input_data.get("audio_spine", {})
    enhancement_spec = input_data.get("enhancement_spec", {})

    result = define_audio_mix(audio_spine, enhancement_spec)
    json.dump(result, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
