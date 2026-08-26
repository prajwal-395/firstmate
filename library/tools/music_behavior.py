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


def is_silent(behavior) -> bool:
    """True when the plan says this stretch carries no music."""
    return behavior == SILENT
