"""What the music bed does under one spine block.

One enumeration.  `mesh_spine` (2.05) declares a behaviour on every block
it plans, and four places downstream have to agree on what the word means:

    audio_mix (5.02)        turns the word into the dB the bed sits at
    compile_manifest (5.04) carries the word onto `_spine_blocks`
    render_qa (P3)          judges the render against the planned dB
    resolve_build_timeline  writes the target level as a timeline marker

The vocabulary is FIVE words, and `silent` is one of them.  Silence is a
deliberate tool of the edit - a block planned silent is not a block that
was forgotten - so any reduction that cannot say `silent` cannot carry the
plan.  An unknown word raises rather than defaulting: a behaviour nobody
recognises is a plan nobody is executing.

Undeclared is not the same as unknown.  A block may legitimately carry no
behaviour - a bookend card is built by `library/tools/bookends.py` and
never passes through the LLM that writes the rest of the spine - so
`resolve_music_behavior` supplies the default for such a block, in ONE
place, from the single fact the old reduction got right: a block with no
speech under it has nothing to duck for.


Rules relocated from AGENTS.md 10.1
-----------------------------------
These are the engine's rules for this module.  They lived in AGENTS.md
until it was split by subsystem; the wording is unchanged, so each rule
is findable by its own words, and AGENTS.md 10.1 keeps the headline
and points here.

**`music_behavior` has ONE vocabulary: `library/tools/music_behavior.py`.**
Five words - `prominent`, `background`, `fade_in`, `fade_out`, `silent` - and `silent` is one of them, because a planned silence is a decision.
`mesh_spine` declares it, `spine_contract` rejects a word outside it, `audio_mix` DECIDES the dB for the two words that carry one, `compile_manifest` CARRIES it onto `_spine_blocks` rather than recomputing it, and `render_qa` judges the render against it.
**The five dB are GONE (captain, 2026-09-16) and a word no longer carries a number.** `DECIDED_BEHAVIORS` is the two whose level step 5.02 decides per run over what the bed and the speech measure; `silent` is the absence of music and `fade_in`/`fade_out` are a MOVE that takes the level of the block it moves to (`level_for_block`). `WITHDRAWN_LEVELS_DB` records each old number and where it went. [why](docs/CREATIVE_VALUE_DECISION.md)
Resolve a block that declares none through `resolve_music_behavior`, never with a local default: `WITHDRAWN_BEHAVIORS` records why the two-word `full`/`ducked` form is out. [why](docs/RULE_EVIDENCE.md#silence-lost-in-the-two-word-vocabulary)
`tests/unit/audio/test_music_bed.py`.
**The timeline's length comes from the spine, never from a passage's `end_time`.**
`library/tools/timeline_duration.measure_timeline_duration`: max `timeline_end` over the spine, falling back to `a_roll_assignments`.


Rules relocated from AGENTS.md 10.4
-----------------------------------
These are the engine's rules for this module.  They lived in AGENTS.md
until it was split by subsystem; the wording is unchanged, so each rule
is findable by its own words, and AGENTS.md 10.4 keeps the headline
and points here.

**A clip gain is not a separation, and both halves now SAY which one they are holding.**
**And the SEPARATION is what the model is asked for**, since 2026-09-16: a gain is a number about a FILE, a separation is a number about what a person hears, and only the second is a thing a mix engineer can be asked.
`music_behavior.SEPARATION_TARGETS_DB` stays EMPTY and is not the route: a per-run judgement is not a module-level dict. Step 5.02 asks a mix engineer for the separation over the bed's and the speech's own measurements, and the clip gain is arithmetic (`decided_value._solve_bed_gain`).
`measure_speech_above_bed` reads a declared target or falls back to clip gain, recording `required_margin_basis` per window and `judged_on_clip_gain` on the result - and the decided separation is what now reaches it on `music_automation[].separation_target_db`.
"""

from typing import Any, Mapping, Sequence

# ── The vocabulary, and what is no longer in it ──────────────────────
#
# Five words, and until 2026-09-16 each carried a dB.  The captain ruled
# that day that how loud the bed sits is not a number anybody picks:
#
#     *"these all stand as things that should be covered by creative
#     reasoning by the LLM to see if music is sitting too loud, too soft,
#     or just right. there is no hard coded number that hits this.
#     perhaps a formula or some kind of audio anaysis that allows that
#     judgement to be made is what im referring to"*
#
# So the words stay - the vocabulary was never the defect - and the
# numbers left.  What replaced them is not a different number: step 5.02
# asks a mix engineer how far above the bed the voice should sit in THIS
# piece, and the clip gain is arithmetic over that judgement and two
# measurements (`library/tools/decided_value.py`, slot
# `mix.speech_above_bed_db`).  The five old gains survive ONLY as that
# slot's registered fallback, owned by the series that preferred them.
#
# word -> what it means
MUSIC_BEHAVIORS = {
    "prominent":  "music leads; no competing speech",
    "background": "music plays quietly under speech",
    "fade_in":    "moving up from silent or background",
    "fade_out":   "moving down from prominent",
    "silent":     "no music at all - a chosen hole in the bed",
}

SILENT = "silent"

# The two words whose level is DECIDED, per run, by the step that can see
# what the bed and the speech measure.  The other three are not values
# anybody decides, and saying so is what keeps this a two-word question
# instead of a five-number one:
#
#   `silent` is the ABSENCE of music.  -96 dB is not a level somebody
#   chose, it is the bottom of the fader, and it sits in the same
#   category as `transition_vocabulary.CUT_TYPES` and
#   `series_look.NEUTRAL_CDL` - the absence of decoration rather than a
#   choice of it (AGENTS.md 10.5).
#
#   `fade_in` and `fade_out` are a MOVE between two levels, not a third
#   level.  The old -12 was a plateau in the middle of a ramp
#   `otio_mix.music_curve` already draws, so it stated a level nobody
#   planned; a fade now resolves to the level it is moving TO.
DECIDED_BEHAVIORS = ("prominent", "background")
FADE_BEHAVIORS = ("fade_in", "fade_out")

# The bottom of the fader.  `otio_mix.MIN_VOLUME_DB` is the delivery
# floor this has to sit at or above, and the two are asserted equal-or-
# above in tests/scenarios/test_audio_mix_delivery.py.
SILENT_LEVEL_DB = -96

# What the five words used to carry, and where each number went.  Kept
# because a number that merely disappears is a number a future reader
# re-invents - the same reason `WITHDRAWN_BEHAVIORS` below is kept.
WITHDRAWN_LEVELS_DB = {
    "prominent": (-6, "decided per run; the old gain is the registered "
                      "fallback of decided_value slot "
                      "mix.speech_above_bed_db"),
    "background": (-18, "decided per run; the old gain is the registered "
                        "fallback of the same slot"),
    "fade_in": (-12, "a fade is a move, not a level: it resolves to the "
                     "level it moves TO (`level_for_block`)"),
    "fade_out": (-12, "a fade is a move, not a level: it resolves to the "
                      "level it moves TO, or to silence at the end of the "
                      "piece"),
    "silent": (-96, "not withdrawn and not decided - the absence of "
                    "music, kept as SILENT_LEVEL_DB"),
}

# ── A clip gain is not a separation, and the separation is now DECIDED ─
#
# A clip gain is how far the bed is pushed down from whatever level the
# file already carries.  It is not the gap the ear hears between the
# voice and the bed, and the two agree only when the music file's own
# level sits at or below the speech's.  On project 001 the bed is
# mastered about 8.6 dB hotter than the iPhone speech, so the old -18 dB
# of gain bought about 12.5 dB of separation and no mix setting reached
# 18 (AGENTS.md 10.4).
#
# That gap is why the five numbers could not generalise and why the
# captain removed them.  A gain is a number about a FILE; a separation is
# a number about what a person hears, and it is the one a mix engineer
# can actually be asked for.  So the question runs the other way round
# now: step 5.02 asks for the SEPARATION, over what the bed and the
# speech measure, and the gain that delivers it is arithmetic
# (`decided_value._solve_bed_gain`).
#
# `SEPARATION_TARGETS_DB` stays EMPTY and is not the route.  A per-run
# judgement cannot live in a module-level dict - a constant is exactly
# what it stopped being - and the decided separation travels as a
# decision record on `audio_mix_spec.value_decisions`, where it says who
# decided it and what it was decided over.
SEPARATION_TARGETS_DB: dict = {}

UNDECLARED_SEPARATION = (
    "no separation target is declared for this behaviour as a constant, "
    "and none will be: it is decided per run by step 5.02 over the bed's "
    "and the speech's own measurements, and recorded on "
    "`audio_mix_spec.value_decisions` (library/tools/decided_value.py, "
    "slot mix.speech_above_bed_db). A reader wanting the separation this "
    "run planned reads the decision record, never this dict."
)

# A block that carries speech has a voice to sit under; one that does not
# has nothing to duck for.  These two are the only defaults, and they are
# the same two `mesh_spine`'s handoff table prescribes.
SPEECH_DEFAULT_BEHAVIOR = "background"
NON_SPEECH_DEFAULT_BEHAVIOR = "prominent"

# The two-word form `compile_manifest._spine_block_entry` used to recompute
# onto every `_spine_blocks` entry, from the initial commit until it was
# removed.  It was derived from `block_type` rather than read from the
# plan, so a block the spine marked `silent` reached the manifest marked
# `ducked`, and there is no word in it for silence at all.  Nothing ever
# read it - see docs/RULE_EVIDENCE.md#silence-lost-in-the-two-word-vocabulary.
WITHDRAWN_BEHAVIORS = {
    "full": "recomputed from block_type; say `prominent`, which is planned",
    "ducked": "recomputed from block_type; say `background`, which is planned",
}


class MusicBehaviorError(ValueError):
    """A music behaviour outside the vocabulary."""


def is_known_behavior(behavior) -> bool:
    """True when `behavior` is one of the five words."""
    return isinstance(behavior, str) and behavior in MUSIC_BEHAVIORS


def resolve_music_behavior(declared, *, block_carries_speech: bool) -> str:
    """The behaviour a block plans, or the default for a block that declares none.

    Raises on a word outside the vocabulary, naming the withdrawn two-word
    form when that is what it was handed, so a reduction reappearing
    anywhere fails at the first hop instead of travelling as a plan.
    """
    if declared is None or declared == "":
        return (SPEECH_DEFAULT_BEHAVIOR if block_carries_speech
                else NON_SPEECH_DEFAULT_BEHAVIOR)
    if not is_known_behavior(declared):
        raise MusicBehaviorError(
            f"unknown music_behavior {declared!r}"
            + (f" - withdrawn: {WITHDRAWN_BEHAVIORS[declared]}"
               if declared in WITHDRAWN_BEHAVIORS else "")
            + f"; the vocabulary is {sorted(MUSIC_BEHAVIORS)}")
    return declared


def is_decided_behavior(behavior) -> bool:
    """True when this word's level is DECIDED rather than structural."""
    return behavior in DECIDED_BEHAVIORS


def is_fade(behavior) -> bool:
    """True when this word is a MOVE between two levels."""
    return behavior in FADE_BEHAVIORS


def level_for_block(index: int, behaviors: Sequence[str],
                    decided: Mapping[str, Any]):
    """The bed level one block plays at, given the decided levels.

    `decided` maps the two DECIDED_BEHAVIORS to the clip gain step 5.02
    solved for them this run.  The other three words are structural and
    are resolved here rather than decided anywhere:

    * `silent` is :data:`SILENT_LEVEL_DB` - the absence of music.
    * a fade takes the level it is moving TO: the nearest following
      block that is not itself a fade.  A fade with nothing after it
      looks BACKWARD instead, and a `fade_out` that ends the piece ends
      in silence, which is what a fade out at the end of a video is.

    Returns `(level, why, scope)`.  `level` is None when nothing decided
    it - never 0, which reads as silence, and never a stand-in.  `scope`
    is the DECIDED_BEHAVIORS word whose decision this block's level came
    from, or None when no decision is behind it (silence, or a fade that
    ends the piece), so a reader can join a window back to the judgement
    that set it without re-deriving the fade rule.
    """
    behavior = behaviors[index]
    if not is_known_behavior(behavior):
        raise MusicBehaviorError(
            f"unknown music_behavior {behavior!r}; the vocabulary is "
            f"{sorted(MUSIC_BEHAVIORS)}")

    if is_silent(behavior):
        return SILENT_LEVEL_DB, "silent: no music plays under this block", None

    if is_decided_behavior(behavior):
        level = decided.get(behavior)
        if level is None:
            return None, (f"nothing decided a level for {behavior!r} on this "
                          f"run"), behavior
        return level, f"the level decided for {behavior!r} this run", behavior

    # A fade. It resolves FORWARD and only forward, because a fade is a
    # move TO something: taking the level it moved FROM would plateau it
    # at its own starting point, which is a fade that does not fade.
    cursor = index + 1
    while cursor < len(behaviors):
        if not is_fade(behaviors[cursor]):
            level, why, scope = level_for_block(cursor, behaviors, decided)
            if level is None:
                return None, (f"{behavior!r} resolves to the block it moves "
                              f"to, and {why}"), scope
            return level, (f"{behavior!r} is a move, not a level: it takes "
                           f"the level of the block it moves to ({why})"), scope
        cursor += 1

    if behavior == "fade_out":
        return SILENT_LEVEL_DB, ("fade_out with nothing after it: the piece "
                                 "ends in silence"), None
    # A fade_in that moves to nothing is a plan nobody can deliver, and
    # the mix says so rather than inventing a level for it.
    return None, (f"{behavior!r} is the last block and has nothing to move "
                  f"to, so there is no level for it to take"), None


def separation_target_db(behavior: str):
    """The separation this behaviour asks for, or None when none is declared.

    None means UNDECLARED, never zero and never "no separation wanted".
    A reader that cannot tell the two apart is the defect this exists to
    remove - see :data:`UNDECLARED_SEPARATION`.
    """
    if not is_known_behavior(behavior):
        raise MusicBehaviorError(
            f"unknown music_behavior {behavior!r}; the vocabulary is "
            f"{sorted(MUSIC_BEHAVIORS)}")
    return SEPARATION_TARGETS_DB.get(behavior)


def is_silent(behavior) -> bool:
    """True when the plan says this stretch carries no music."""
    return behavior == SILENT
