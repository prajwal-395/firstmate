#!/usr/bin/env python3
"""The measurements step 5.02 takes, and the spec it assembles from them.

Both halves of the step read this, for the reason 5.01's `grade.py`
exists: the pre-bridge measures and the post-bridge composes, and a step
whose two halves measure separately is a step whose two halves can
disagree.  The loudness passes run ONCE, in the bridge, and travel to the
post-bridge as the pre-bridge's own output.

What is measured here, and what is not
--------------------------------------
MEASURED: the bed's own integrated loudness (step 2.04 folds it onto
`music_selection.measurements`; `music_measurement.bed_reading` is the
reader) and the speech under each window (one ffmpeg `loudnorm` pass per
block over the ranges `a_roll_assignments` names - about 0.23 s a block).

NOT measured, and not invented either: how far above the bed the voice
OUGHT to sit.  That is a judgement, it is the model's, and it arrives
through `library/tools/decided_value.py`.  Until 2026-09-16 it was five
constants in `music_behavior`, and this file's whole reason to exist is
that they are gone.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..'))

from library.tools import decided_value
from library.tools.dialogue_cleanup import (
    deepfilternet_probe,
    measure_source,
    spans_by_source,
    voice_isolation_note,
)
from library.tools.music_behavior import (
    level_for_block,
    resolve_music_behavior,
)
from library.tools.music_measurement import bed_level_after_gain, bed_reading
from library.tools.sfx_level import WITHDRAWN_TRACK_LEVELS
from library.tools.speech_loudness import (
    measure_speech_blocks,
    separation_delivered_db,
)
from library.tools.spine_contract import is_speech_block

SLOT = "mix.speech_above_bed_db"

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
# level is relative to - not a chosen level.
#
# A2_music carried `prominent_level_db` -6 and `background_level_db` -18
# until 2026-09-16, a SECOND copy of `music_behavior`'s five numbers in a
# step that was already reading them from there. Both copies are gone:
# the level is decided per run and the decision is on
# `audio_mix_spec.value_decisions`, so there is one place to read it and
# nowhere for a second answer to live.
TRACK_LEVELS = {
    "A1_speech": {
        "base_level_db": 0,
        "compressor": True,
        "notes": "Speech is the reference — everything else is relative",
    },
    "A2_music": {
        "levels_are_decided_per_run": True,
        "notes": ("the bed's level is decided by this step over what the "
                  "bed and the speech measure, per "
                  "library/tools/decided_value.py slot "
                  "mix.speech_above_bed_db; read `music_automation` and "
                  "`value_decisions`, not a constant here"),
        "fade_duration_seconds": 1.0,
    },
    "A3_sfx": {
        "levels_are_per_sound": True,
        "notes": WITHDRAWN_TRACK_LEVELS["A3_sfx.base_level_db"],
    },
}


def _speech_lufs(speech: dict, block: dict):
    """The measured loudness of this block's speech, or None."""
    reading = speech.get(block.get("position")) or {}
    return reading.get("integrated_lufs") if reading.get("measured") else None


def behaviours_in_order(structure) -> list:
    """The behaviour word for every spine block, in timeline order.

    A block that planned a behaviour keeps it, whatever it is - a planned
    `silent` is a decision, not a gap to fill.  Only a block that planned
    nothing (a bookend card, which never passes through the spine's LLM)
    takes the default.
    """
    return [resolve_music_behavior(block.get("music_behavior"),
                                   block_carries_speech=is_speech_block(block))
            for block in structure or []]


def measure(audio_spine: dict, music_selection: dict,
            a_roll_assignments=None) -> dict:
    """Everything the mix engineer is shown, measured once.

    Returns the pre-bridge's output: the bed's reading, one row per spine
    block, and the fallback the slot registers so the prompt can say what
    the mix would do if nobody decided.
    """
    structure = audio_spine.get("structure", []) or []
    bed = bed_reading(music_selection)
    # One loudnorm pass per speech block. See
    # library/tools/speech_loudness.py for the measured cost.
    speech = measure_speech_blocks(a_roll_assignments)
    behaviours = behaviours_in_order(structure)

    held = decided_value.slot(SLOT).fallback
    windows = []
    for index, block in enumerate(structure):
        behaviour = behaviours[index]
        speech_lufs = _speech_lufs(speech, block)
        # What the separation WOULD be if this run kept the level the
        # engine used to hold. It is the reference point the question is
        # asked against, and it is labelled as the fallback rather than
        # offered as a recommendation: whatever the model reads as a
        # recommendation becomes the chooser (AGENTS.md 10.5).
        held_gain = (held.value.get(behaviour)
                     if held and isinstance(held.value, dict) else None)
        windows.append({
            "spine_block_position": block["position"],
            "block_type": block.get("block_type"),
            "timeline_start": block.get("timeline_start", 0.0),
            "timeline_end": block.get("timeline_end", 0.0),
            "music_behavior": behaviour,
            "carries_speech": is_speech_block(block),
            "speech_lufs": speech_lufs,
            "speech_loudness": speech.get(block["position"]),
            "separation_at_the_fallback_db": separation_delivered_db(
                speech_lufs, bed_level_after_gain(bed, held_gain)),
        })

    return {
        "bed_measurements": bed,
        "mix_windows": windows,
        "mix_decision_legend": legend(bed),
        "cleanup_context": cleanup_context(audio_spine, a_roll_assignments),
    }


def legend(bed: dict) -> dict:
    """What each column IS, and what a separation is measured against.

    The MEASUREMENT_LEGEND route 5.01 uses: it defines the vocabulary and
    never says what to conclude.
    """
    return {
        "speech_lufs": (
            "the integrated loudness of the speech under this window, one "
            "ffmpeg loudnorm pass over the range the edit really plays. "
            "null means nothing measured it - never that it is silent"),
        "separation_at_the_fallback_db": (
            "how far the speech would sit above the bed if this run kept "
            "the level the engine held until 2026-09-16. It is the status "
            "quo, shown so you can say whether it is right for THIS bed and "
            "THIS voice - it is not a recommendation and not a starting "
            "point you are expected to stay near"),
        "bed_integrated_lufs": (
            f"the chosen bed's own loudness, {bed.get('integrated_lufs')}. "
            f"A bed mastered hot needs more gain reduction than a quiet one "
            f"to sit in the same place under the same voice, which is why a "
            f"single number for every project could not work"),
        "what_you_are_deciding": (
            "the SEPARATION - how far above the bed the voice sits. The "
            "clip gain that delivers it is arithmetic over your answer and "
            "the two measurements, and you are not asked for it"),
    }


def assemble(pre_output: dict, decisions: list, automation: list,
             undetermined_windows: list, cleanup: dict | None = None) -> dict:
    """`audio_mix_spec` from the measurements and the decisions."""
    bed = pre_output.get("bed_measurements") or {}
    # A separation target exists only where somebody named a SEPARATION.
    # A fallback carries a GAIN - the number the engine used to hold - and
    # no separation was ever asked for or delivered behind it, so a
    # fallback must not set this flag: `render_qa` reads it to decide
    # whether it is judging a real target or a clip gain read as one, and
    # that is exactly the confusion this whole change removes.
    declared = [d for d in decisions if d.get("answer") is not None]
    return {
        "audio_mix_spec": {
            "track_levels": TRACK_LEVELS,
            "music_automation": automation,
            "bed": bed,
            "value_decisions": decisions,
            "separation_targets_declared": bool(declared),
            "undetermined_windows": undetermined_windows,
            # The dialogue cleanup the plan asked for (fidelity rung
            # R5d). Absent means unasked: the build cleans nothing it
            # was not asked for, and compile_manifest is the reader.
            "dialogue_cleanup": cleanup or {"requests": [], "tools": {},
                                            "sources": []},
            "master_limiter": {
                "threshold_db": -1.0,
                "enabled": True,
            },
            "notes": (
                "Speech (A1) is the reference at 0 dB. How far the bed sits "
                "under it is DECIDED on this run, per behaviour, over what "
                "the bed and the speech measure - read `value_decisions` "
                "for the separation each one was decided at, on what basis, "
                "and by what arithmetic. `target_level_db` is the clip gain "
                "that delivers it: what the bed is pushed down BY, against "
                "its own level, not the gap the ear hears."
            ),
        },
    }


def solve_automation(pre_output: dict, decisions_by_scope: dict) -> tuple:
    """`music_automation`, from the measurements and the decided levels.

    Returns `(automation, undetermined_windows)`.  A window whose level
    nothing decided carries NO gain and names itself; nothing downstream
    may substitute one.
    """
    bed = pre_output.get("bed_measurements") or {}
    windows = pre_output.get("mix_windows") or []
    behaviours = [w["music_behavior"] for w in windows]
    decided = {scope: d["value"] for scope, d in decisions_by_scope.items()
               if d.get("value") is not None}

    automation = []
    undetermined = []
    for index, window in enumerate(windows):
        level_db, why, scope = level_for_block(index, behaviours, decided)
        behaviour = behaviours[index]
        if level_db is None:
            undetermined.append({
                "spine_block_position": window["spine_block_position"],
                "music_behavior": behaviour,
                "why": why,
            })
        # The separation the plan asks the ear for, which is what
        # `render_qa.measure_speech_above_bed` judges the render against.
        # It comes from the decision this window's level came FROM, which
        # is the scope `level_for_block` names - so a fade carries the
        # separation of the block it moves to rather than one of its own,
        # and a `silent` window carries none: there is no voice-to-bed gap
        # to plan when no music plays.
        target = (decisions_by_scope.get(scope) or {}).get("answer")
        automation.append({
            "spine_block_position": window["spine_block_position"],
            "timeline_start": window["timeline_start"],
            "timeline_end": window["timeline_end"],
            "music_behavior": behaviour,
            # The clip gain: how far the bed is pushed down from its own
            # level. This is the number the OTIO route delivers. None
            # when nothing decided it - never 0, which is a real level.
            "target_level_db": level_db,
            "target_level_basis": why,
            # Where that gain puts the bed, given what the bed measures.
            "bed_level_after_gain_lufs": bed_level_after_gain(bed, level_db),
            # What the plan asks the ear to hear. A real number now, on
            # every window whose behaviour was decided.
            "separation_target_db": target,
            "speech_lufs": window["speech_lufs"],
            "speech_loudness": window["speech_loudness"],
            # The separation this window WILL deliver.
            "separation_delivered_db": separation_delivered_db(
                window["speech_lufs"],
                bed_level_after_gain(bed, level_db)),
        })
    return automation, undetermined


def cleanup_context(audio_spine: dict, a_roll_assignments) -> dict:
    """The noise record the cleanup judgement is made over, measured once.

    One row per distinct played source: the ranges the edit really
    plays (from `a_roll_assignments` video segments), the word timings
    in source seconds (from the audio spine blocks' `word_timestamps`,
    read directly per the spine contract), and the floor plus speech
    level `dialogue_cleanup.measure_source` reads off them. A floor
    that refuses is stated with its reason - never a default. Costs one
    decode per distinct source plus the loudnorm passes the speech
    windows already paid; a run with long masters pays seconds here,
    once, instead of every consumer re-decoding.

    `tools` states what the build can actually do: DeepFilterNet
    availability is probed in this interpreter (the pip install is
    deliberately NOT in requirements.txt - see `dialogue_cleanup`),
    Voice Isolation applies at build with read-back on Studio.
    """
    structure = (audio_spine or {}).get("structure", []) or []
    played, spans = spans_by_source(structure, a_roll_assignments)

    sources = []
    for source in sorted(played):
        record = measure_source(source, played[source], spans[source])
        sources.append(record)

    return {
        "sources": sources,
        "tools": {
            "deepfilternet": deepfilternet_probe(),
            "voice_isolation": voice_isolation_note(),
        },
        "legend": (
            "floor.level_dbfs is the measured quiet of this source - the "
            "room tone rung 5a stages fills from. A request that names no "
            "measured floor is still plannable, but the build applies only "
            "what its tool can verify: voice_isolation needs Resolve "
            "Studio with read-back, deepfilternet needs its binary on "
            "this machine. tools says which answers here."
        ),
    }
