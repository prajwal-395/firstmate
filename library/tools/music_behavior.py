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
`mesh_spine` declares it, `spine_contract` rejects a word outside it, `audio_mix` turns it into the dB, `compile_manifest` CARRIES it onto `_spine_blocks` rather than recomputing it, and `render_qa` judges the render against it.
Resolve a block that declares none through `resolve_music_behavior`, never with a local default: `WITHDRAWN_BEHAVIORS` records why the two-word `full`/`ducked` form is out. [why](docs/RULE_EVIDENCE.md#silence-lost-in-the-two-word-vocabulary)
`tests/test_music_behavior_vocabulary.py`.
**The timeline's length comes from the spine, never from a passage's `end_time`.**
`library/tools/timeline_duration.measure_timeline_duration`: max `timeline_end` over the spine, falling back to `a_roll_assignments`.


Rules relocated from AGENTS.md 10.4
-----------------------------------
These are the engine's rules for this module.  They lived in AGENTS.md
until it was split by subsystem; the wording is unchanged, so each rule
is findable by its own words, and AGENTS.md 10.4 keeps the headline
and points here.

**A clip gain is not a separation, and both halves now SAY which one they are holding.**
`music_behavior.SEPARATION_TARGETS_DB` is EMPTY - no behaviour declares a separation.
`measure_speech_above_bed` reads a declared target or falls back to clip gain, recording `required_margin_basis` per window and `judged_on_clip_gain` on the result.
"""

# word -> (bed level in dB against a 0 dB speech reference, what it means)
MUSIC_BEHAVIORS = {
    "prominent":  (-6,  "music leads; no competing speech"),
    "background": (-18, "music plays quietly under speech"),
    "fade_in":    (-12, "moving up from silent or background"),
    "fade_out":   (-12, "moving down from prominent"),
    "silent":     (-96, "no music at all - a chosen hole in the bed"),
}

SILENT = "silent"

# ── A clip gain is not a separation ─────────────────────────────────
#
# The dB above is a CLIP GAIN: how far the bed is pushed down from
# whatever level the file already carries.  It is not the gap the ear
# hears between the voice and the bed, and the two agree only when the
# music file's own level sits at or below the speech's.  On project 001
# the bed is mastered about 8.6 dB hotter than the iPhone speech, so -18
# dB of gain buys about 12.5 dB of separation and no mix setting reaches
# 18 (AGENTS.md 10.4; `render_qa.SPEECH_ABOVE_BED_GATES` records the
# per-window numbers).
#
# `render_qa.measure_speech_above_bed` measures the separation.  What it
# has never had is a separation to measure AGAINST, so it reads the clip
# gain as though it were one - which is why it reports 1 of 11 windows
# meeting "the plan's own margin" on an edit whose plan never declared a
# margin at all.
#
# So the plan may now CARRY one, per behaviour, and today none is
# declared.  The number is not the engine's to choose: it is the same
# registered captain decision as the five clip gains above, and an
# engine-supplied separation target would be a strength nobody chose
# arriving one level up (AGENTS.md 10.5, and section 12's rule that no
# element has a default and none has a bound).
SEPARATION_TARGETS_DB: dict = {}

UNDECLARED_SEPARATION = (
    "no separation target is declared for this behaviour. The dB beside "
    "it is a clip gain, which is what the bed is pushed down BY, not the "
    "gap it ends up at under the voice. Declaring one is the same open "
    "decision as the clip gains themselves; until it is taken, a check "
    "judging against the clip gain is judging against a number that was "
    "not a separation target."
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


def music_level_db(behavior: str) -> int:
    """The bed level the behaviour plans, in dB under speech."""
    if not is_known_behavior(behavior):
        raise MusicBehaviorError(
            f"unknown music_behavior {behavior!r}; the vocabulary is "
            f"{sorted(MUSIC_BEHAVIORS)}")
    return MUSIC_BEHAVIORS[behavior][0]


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
