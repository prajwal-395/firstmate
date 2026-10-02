"""How loud a sound effect is, and who decides it.

Captain, station 9, on the one sound in the run of record: *"a single sfx
that i dont even think played the right part of the sfx and instead it was
just silent"*.

The experience is right and the mechanism is not silence.  Measured:

- ``camera soft click.wav`` is 0.459 s, peaks at -6.4 dB and averages
  -36.6 dB, with its transient at the HEAD - -29.7 dB at 0.00 s decaying
  to -36.6 dB by 0.37 s.  The file is fine.
- It was placed at ``source_in`` 0.0 for its whole length, so the right
  part played.
- Then it was made inaudible.  ``assembly_manifest.audio_mix
  .track_levels.A3_sfx`` carried ``base_level_db: -12`` with the note
  *"Subtle - felt more than heard"*, and the clip placement carried
  ``volume_db: -14`` on top of that - applied to a file already averaging
  -36.6 dB, sitting under speech at the 0 dB reference and a music bed at
  -18 dB.  It is present, and it cannot be heard.

Two taste decisions written into code
-------------------------------------
Neither of them was ever a model's:

1. ``TRACK_LEVELS["A3_sfx"]["base_level_db"] = -12``, annotated *"Subtle -
   felt more than heard"*.  That sentence is a creative brief, and no
   project wrote it.  It is REMOVED (:data:`WITHDRAWN_TRACK_LEVELS`), not
   replaced: a different number would be the same defect wearing a
   different value.
2. ``VOLUME_MAP``, which resolved the model's word - ``subtle``, ``low``,
   ``medium``, ``prominent`` - into -18, -14, -10 and -6 dB.  Same shape
   as step 4.03's ``INTENSITY_MAP``, removed by the captain on 2026-09-02
   for exactly this: an engine-supplied ladder is a strength nobody chose
   arriving one level up (AGENTS.md 10.5).  Also REMOVED, and again with
   no replacement ladder.

What replaces them is the model's own number
--------------------------------------------
A sound effect now carries ``volume_db`` - the dB the plan wants it at,
against speech's 0 dB reference - and it is the answering step's decision.
**An entry that names no level is DROPPED with the reason**, exactly the
way an entry naming no ``sfx_id`` and an entry naming no position already
are.  Nothing is substituted, at any layer: ``compile_manifest``'s
``sfx_entry.get("volume_db", -14)`` is gone too.

**The model can only reason about this because it is told what the sound
sits under**, and it already was: step 4.04's candidate table carries
``music_behavior`` and ``bed_under_it`` per block through
``music_measurement.bed_under_block`` - the bed's own measured level
under that block, and what the plan says it is doing there.  What it did not
carry is the other end of the comparison, which is not a measurement but a
definition of the mix: **speech (A1) is the reference at 0 dB**.  Step
4.04's own ``handoff.md`` states it, under "What a level is measured
against", as the definition it is rather than smuggled in as a target.
It travelled beside the table as a ``SPEECH_REFERENCE_LEGEND`` dict only
while that file was under the captain's freeze, lifted 2026-09-09; the
prose carries it now, and the withdrawn ``volume_level`` line it existed
to correct is gone from the prompt rather than contradicted beside it.

**No separation target is declared anywhere in this pipeline** -
``music_behavior.SEPARATION_TARGETS_DB`` is empty and the master loudness
target is the captain's - so nothing here says how far above or below the
bed a sound OUGHT to sit.  Saying it would be the third taste decision.

``tests/unit/audio/test_sfx.py``.


Rules relocated from AGENTS.md 10.5
-----------------------------------
These are the engine's rules for this module.  They lived in AGENTS.md
until it was split by subsystem; the wording is unchanged, so each rule
is findable by its own words, and AGENTS.md 10.5 keeps the headline
and points here.

- **HOW LOUD a sound plays is the plan's own `volume_db`, and no layer substitutes one.** One enumeration, `library/tools/sfx_level.py`. The four-word ladder `VOLUME_MAP` (`subtle|low|medium|prominent` -> -18|-14|-10|-6) is REMOVED on the ruling that removed `INTENSITY_MAP`, `TRACK_LEVELS["A3_sfx"].base_level_db` (-12, *"Subtle - felt more than heard"*) and `A4_transition_audio` are REMOVED, and `compile_manifest`'s `get("volume_db", -14)` is gone. **Nothing is renumbered** - `WITHDRAWN_VOLUME_LADDER` and `WITHDRAWN_TRACK_LEVELS` are the record. An entry naming no level is DROPPED with the reason. **The model can only reason about a relationship it can see**: `bed_under_it` says what the bed does under the block, and step 4.04's `handoff.md` states the other end - speech (A1) is the reference at 0 dB - as the definition it is, never as a target. `tests/unit/audio/test_sfx.py`.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

# The key the model writes its decision under.
VOLUME_KEY = "volume_db"

# Resolve's own bounds on a clip volume, the same pair `otio_mix` clamps
# to. A level outside them cannot be delivered, so a plan naming one is
# refused rather than silently clamped into a level nobody chose.
MIN_VOLUME_DB = -100.0
MAX_VOLUME_DB = 30.0

WITHDRAWN_TRACK_LEVELS = {
    "A3_sfx.base_level_db": (
        "-12 dB, annotated 'Subtle - felt more than heard'. A creative "
        "brief written into the engine, applied to every sound of every "
        "project. Nothing read it - `otio_mix` delivers the per-clip "
        "`volume_db` and nothing else - so it shaped no render, but it "
        "stated a taste in the shipped manifest as though the mix had "
        "been decided. Removed rather than renumbered."
    ),
    "A4_transition_audio.base_level_db": (
        "-10 dB, annotated 'Brief, paired with transition visuals'. The "
        "same defect; A4 is not even a fixed bucket - the allocator "
        "spreads overlapping sounds across A3, A4, A5 as it needs them, "
        "so the row described a lane that does not mean what it says."
    ),
}

WITHDRAWN_VOLUME_LADDER = {
    "subtle": -18, "low": -14, "medium": -10, "prominent": -6,
}
"""`VOLUME_MAP` as it stood, kept as a record of what was withdrawn.

Nothing reads it.  It is here so that reintroducing a ladder has to be a
decision somebody makes on purpose, and so the numbers the run of record
was mixed at are not lost.
"""


class SfxLevelRefused(ValueError):
    """A planned sound names a level that cannot be delivered."""


def read_volume_db(entry: Optional[Dict[str, Any]]
                   ) -> Tuple[Optional[float], str]:
    """`(level, reason)` - the level the plan named, or why there is none.

    A level of `None` with a reason is an entry to DROP.  It is never a
    level to substitute: the whole point of this module is that no number
    arrives from anywhere but the answer.

    A level outside what Resolve can carry RAISES, because it is a real
    number the plan meant and clamping it would deliver a different one.
    """
    if not isinstance(entry, dict):
        return None, "the plan entry is not an object"
    raw = entry.get(VOLUME_KEY)
    if raw is None:
        return None, (
            f"it names no {VOLUME_KEY}. How loud a sound plays against "
            f"speech's 0 dB reference and the bed under it is the plan's "
            f"decision, and no level is substituted for a missing one")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None, (
            f"its {VOLUME_KEY} is {raw!r}, which is not a number of dB")
    level = float(raw)
    if not (MIN_VOLUME_DB <= level <= MAX_VOLUME_DB):
        raise SfxLevelRefused(
            f"a planned sound asks for {level} dB, which is outside the "
            f"{MIN_VOLUME_DB}..{MAX_VOLUME_DB} dB a clip volume can carry. "
            f"It is not clamped: clamping would deliver a level nobody "
            f"chose, which is the defect this module exists for.")
    return round(level, 2), ""
