"""The timeline spine contract.

`mesh_spine` (step 2.05) produces the timed spine that every creative step
downstream reads.  Historically each consumer guessed at where a block kept
its clip reference and its word timings - `block.get("clip_id", "")`,
`block.get("content", {}).get("clip_id")`, `source_clip_id`, ... - and when
the guess missed, the consumer degraded silently instead of failing.  That
is how beat-aligned cutting sat unreachable for four audits.

There is now exactly one shape.  Every spine block carries:

    position            block identity ("hook" or an int)
    block_type          "hook" | "speech" (speech) | "music" (a
                        music-led moment, see MUSIC_BLOCK_TYPES) |
                        "picture" (a picture-led moment, see
                        PICTURE_BLOCK_TYPES) | "intro_card" |
                        "outro_card" | "end_card" (a template-declared
                        card, see BOOKEND_BLOCK_TYPES) | anything else
                        (non-speech: "intro", "transition_slot", "outro")
    clip_id             source clip id; None for non-speech blocks,
                        EXCEPT a "picture" block, which names the clip
                        its picture is cut from
    source_start        source-domain in point (None for non-speech,
                        except "picture", which carries its cut range)
    source_end          source-domain out point (None for non-speech,
                        except "picture", which carries its cut range)
    timeline_start      timeline-domain in point
    timeline_end        timeline-domain out point
    word_timestamps     list of {word, source_start, source_end};
                        non-empty for every speech/hook block, empty
                        for everything else (a "picture" block shows a
                        clip and says nothing)
    alignment_method    how word_timestamps were derived (non-null for
                        speech/hook blocks, null for everything else)

Consumers read these keys directly (`block["clip_id"]`, not
`block.get("clip_id", "")`).  A missing key is a contract violation and must
raise, not degrade.  `validate_spine_blocks` is the single gate that keeps
that promise, and mesh_spine runs it before emitting.

Two OPTIONAL keys may also be present:

    intentional_black_beat   True when the plan deliberately leaves this
                             block's picture empty for a beat.  Absent means
                             "not declared", which is how an accidental hole
                             stays distinguishable from a chosen one -
                             `compile_manifest` fails any uncovered stretch
                             that no block declares.  Only a non-speech block
                             may declare: speech is never held on black.
    black_beat_reason        Non-empty string saying WHY the beat is there.
                             A declaration without one is a rubber stamp.

`validate_spine_blocks` rejects both malformed shapes - a speech block that
declares, and a declaration with no reason - when mesh_spine emits, so they
fail at the spine gate rather than surviving to `compile_manifest`, which
rejects them again on the stretch of timeline they excuse.  `mesh_spine`'s
handoff.md tells the LLM when a beat is worth declaring.

A third optional key is `music_behavior`, the word for what the bed does
under the block - the vocabulary is `library/tools/music_behavior.py`.
Every block the spine's LLM writes declares one; a bookend card, which
`library/tools/bookends.py` assembles instead, declares none and takes the
default at the one place that resolves it.  A word OUTSIDE the vocabulary
is rejected here, so it cannot become a dB level by default further down.

They are optional by design: no block needs them, and none is in
REQUIRED_BLOCK_KEYS.  A declared beat is still bounded by
`MAX_DECLARED_BLACK_BEAT_SECONDS`, and every gate that has to tell a
chosen hole from an accidental one - `compile_manifest` on the manifest,
`render_qa` on the rendered file - reads that bound and
`declared_black_beat_ranges` from here, so the two cannot drift apart.
"""

from library.tools.music_behavior import is_known_behavior, MUSIC_BEHAVIORS

SPEECH_BLOCK_TYPES = ("speech", "hook")

# Block types that lead with MUSIC rather than speech.  A "music" block
# is a moment the edit builds from a span of a chosen track - a montage
# beat, a music-video passage - instead of from a spoken passage.  It
# keeps the non-speech shape (no clip_id, no source range, no word
# timings); what it PLAYS is carried under `content`, beside the
# contract fields rather than in them, because the block-level
# source_start/source_end are a CLIP range everywhere else and reusing
# them for a track span would teach every consumer a second meaning:
#
#     content.track       the track - the audio_path or title of one of
#                         the tracks music_selection chose, the same
#                         vocabulary a music_bed entry's `track` uses.
#                         Omit it and the conducted bed decides.
#     content.source_in   the second of THAT file this moment starts at
#     content.source_out  the second of THAT file this moment ends at
#
# Layering is NOT an overlapping timeline range: blocks partition the
# timeline (`blocks_overlapping` reads them half-open, and the coverage
# assertion in compile_manifest requires every second to be covered
# exactly once), so two blocks can never share a second.  Music under
# speech is the conducted bed (`music_bed`, resolved in mesh_spine)
# plus the per-block `music_behavior` word - that IS the layered shape,
# and a "music" block is the interleaved one.
MUSIC_BLOCK_TYPES = ("music",)

# Block types that lead with PICTURE rather than speech.  A "picture"
# block is a moment the edit builds from a clip span instead of from a
# spoken passage - the spine the captain asked for when the picture is
# the better backbone.  It carries its own clip_id, source_start and
# source_end like a speech block, but no words: word_timestamps is
# empty and alignment_method is None, because there is nothing said to
# align.  assign_aroll places its video the way it places speech (it
# reaches V1 - see `library/tools/transition_carriers.py`), and
# select_broll treats it the way it treats speech: covered already, so
# no mandatory cutaway, but open to interjections.
PICTURE_BLOCK_TYPES = ("picture",)

# Block types that play a CARD rather than footage.  Nothing produces one
# unless a brand template declares it - see `library/tools/bookends.py`,
# which owns the declaration shape and the two ways a card is produced.
# The block carries the card it plays under `content.bookend`;
# `compile_manifest` turns that into a V1 clip, so a card is inside the
# coverage assertion, the manifest duration and render QA like every
# other clip.
#
# Deliberately NOT named "intro"/"outro": those already mean something
# else in this spine - a non-speech pacing beat of music and B-roll, with
# no card in it - and the captured run in tests/fixtures/captured_run/ is
# full of them.  A card and a breath are different things.
BOOKEND_BLOCK_TYPES = ("intro_card", "outro_card", "end_card")

# The longest stretch a spine block may deliberately leave black. Matches
# default_brand.yaml's effect.transition_duration_ms.max of 500ms - the
# longest deliberate moment the brand allows between two shots - so a
# chosen black beat is bounded by the same figure. Hardcoded rather than
# read from the brand template: compile_manifest declares no brand_* input,
# so the runner never resolves one for it, and `brand_registry`'s fallback
# template carries no transition_duration_ms at all.
MAX_DECLARED_BLACK_BEAT_SECONDS = 0.5

# Keys every spine block must carry, whatever its block_type.
REQUIRED_BLOCK_KEYS = (
    "position",
    "block_type",
    "clip_id",
    "source_start",
    "source_end",
    "timeline_start",
    "timeline_end",
    "word_timestamps",
    "alignment_method",
)

# Keys every word timestamp entry must carry.
REQUIRED_WORD_KEYS = ("word", "source_start", "source_end")


class SpineContractError(ValueError):
    """A spine block violates the contract documented in this module."""


def is_speech_block(block: dict) -> bool:
    """True when the block carries speech and therefore needs word timings."""
    return block.get("block_type") in SPEECH_BLOCK_TYPES


def is_music_block(block: dict) -> bool:
    """True when the block is a music-led moment (audio from a track)."""
    return block.get("block_type") in MUSIC_BLOCK_TYPES


def is_picture_block(block: dict) -> bool:
    """True when the block is a picture-led moment (video from a clip)."""
    return block.get("block_type") in PICTURE_BLOCK_TYPES


def is_bookend_block(block: dict) -> bool:
    """True when the block plays an intro, outro or end card."""
    return block.get("block_type") in BOOKEND_BLOCK_TYPES


def validate_passage_coverage(blocks: list, body_sequence_len: int) -> None:
    """Raise SpineContractError when the spine loses or doubles a passage.

    Step 2.05's handoff asks the model for "No content loss: Every
    speech passage from body_sequence appears in exactly one speech
    block" - set coverage over small integers, exactly computable, and
    the script already recomputes everything around it (durations,
    positions, the duration zone). The post-bridge refuses DANGLING
    refs (a `passage_ref` naming no position) where they are found;
    this refuses the other two clerical failures:

    * a passage NO block references - content loss: a passage the
      model chose never reaches the timeline;
    * a passage TWO speech blocks reference - the same source audio
      laid down twice back to back, the repeat 2.02's structural
      overlap check refuses at the source.

    A hook reusing a body passage is NEITHER: the handoff explicitly
    permits the hook as a snippet of a body passage (intentional
    shortform technique). Hook references count toward coverage but
    never toward doubling.

    An EMPTY body_sequence is legal and obligates nothing: a spine with
    no speech blocks (music-led, picture-led, or both) covers zero
    passages out of zero. Speech is optional; this check refuses lost
    or doubled speech, never absent speech.
    """
    referenced = set()
    speech_refs = []
    for block in blocks or []:
        if not isinstance(block, dict):
            continue
        if block.get("block_type") not in SPEECH_BLOCK_TYPES:
            continue
        ref = (block.get("content") or {}).get("passage_ref")
        if ref is None:
            continue
        referenced.add(ref)
        if block.get("block_type") == "speech":
            speech_refs.append(ref)

    expected = set(range(1, (body_sequence_len or 0) + 1))
    problems = []
    missing = sorted(expected - referenced)
    if missing:
        problems.append(
            f"passages {missing} from the speech_sequence body_sequence "
            f"appear in no spine block - content loss: a chosen passage "
            f"never reaches the timeline"
        )
    doubled = sorted({r for r in set(speech_refs) if speech_refs.count(r) > 1})
    if doubled:
        problems.append(
            f"passages {doubled} appear in more than one speech block - "
            f"the same source audio would play twice"
        )
    if problems:
        raise SpineContractError(
            "Spine passage coverage violated:\n  - "
            + "\n  - ".join(problems)
        )


def _bookend_problems(block: dict, label: str) -> list:
    """What is wrong with a bookend block's declaration, if anything.

    A bookend block that carries no playable file is a hole in the picture
    wearing a name.  `compile_manifest` would emit no clip for it and the
    coverage assertion would then fail on a range nothing declared, which
    reads as a planning bug rather than an unrenderable card - so it is
    caught here, at the spine gate, where the cause is still visible.
    """
    problems = []
    bookend = (block.get("content") or {}).get("bookend")
    if not isinstance(bookend, dict):
        return [f"{label}: {block['block_type']} block carries no "
                f"content.bookend declaring what it plays"]

    if not bookend.get("asset_path"):
        problems.append(
            f"{label}: bookend declaration has no asset_path, so nothing "
            f"names the clip this block would play")

    duration = block.get("duration_seconds")
    if not isinstance(duration, (int, float)) or duration <= 0:
        problems.append(
            f"{label}: bookend block has duration_seconds={duration!r} - "
            f"a card with no duration shows nothing")

    if block.get("intentional_black_beat"):
        problems.append(
            f"{label}: bookend block declares an intentional black beat, "
            f"but it plays a card - the two cannot both be true")

    return problems


def _music_ref_problems(block: dict, label: str) -> list:
    """What is wrong with a music block's track reference, if anything.

    The reference is OPTIONAL - a music block without one plays whatever
    the conducted bed puts under it. But a half-written one (a span with
    no track, an end before its start) is a plan nothing can play, and
    it fails here rather than as a hole in the mix downstream.
    """
    problems = []
    content = block.get("content") or {}
    track = content.get("track")
    source_in = content.get("source_in")
    source_out = content.get("source_out")
    if track is None and source_in is None and source_out is None:
        return problems
    if not isinstance(track, str) or not track.strip():
        problems.append(
            f"{label}: music block names a span but no track - "
            f"content.track must name one of the tracks "
            f"music_selection chose")
    for key, value in (("source_in", source_in),
                       ("source_out", source_out)):
        if value is not None and not isinstance(value, (int, float)):
            problems.append(
                f"{label}: music block content.{key}={value!r} is not "
                f"a number - a track span is seconds of that file")
    if (isinstance(source_in, (int, float))
            and isinstance(source_out, (int, float))
            and source_out <= source_in):
        problems.append(
            f"{label}: music block span runs {source_in}-{source_out}s - "
            f"the out point must be after the in point")
    return problems


def validate_spine_blocks(blocks: list, total_duration: float = None, target_duration_zone: tuple = None) -> None:
    """Raise SpineContractError if any block violates the spine contract.

    Called by mesh_spine before it emits, so a malformed spine never
    reaches the creative steps that read it.
    """
    problems = []

    if target_duration_zone and total_duration is not None:
        min_dur, target_dur, max_dur = target_duration_zone
        if total_duration < min_dur or total_duration > max_dur:
            problems.append(
                f"total duration ({total_duration:.1f}s) is outside the target duration zone "
                f"[{min_dur:.1f}s, {max_dur:.1f}s]"
            )

    if blocks and isinstance(blocks[0], dict):
        first_type = blocks[0].get("block_type")
        if first_type in ("silence", "gap"):
            problems.append(f"leading {first_type} block at the head of the timeline is not allowed")

    for i, block in enumerate(blocks):
        if not isinstance(block, dict):
            problems.append(f"block[{i}] is {type(block).__name__}, not a dict")
            continue

        label = f"block[{i}] (position={block.get('position', '?')})"

        missing = [k for k in REQUIRED_BLOCK_KEYS if k not in block]
        if missing:
            problems.append(f"{label}: missing required keys {missing}")
            continue

        # Optional black-beat declaration: catch malformed ones at the gate
        # so they fail here rather than silently reaching compile_manifest.
        # Only a block that shows NO picture of its own may declare one:
        # speech is never held on black, and neither is a picture block
        # or a bookend card - both play a clip, so a beat on either is a
        # hole wearing a name. A music block may: like a transition_slot
        # it carries no picture, and the beat is the picture's absence
        # said on purpose.
        if block.get("intentional_black_beat"):
            if is_speech_block(block) or is_picture_block(block):
                problems.append(
                    f"{label}: {block['block_type']} block declares "
                    f"intentional_black_beat - a block that plays a clip "
                    f"is never held on black"
                )
            reason = block.get("black_beat_reason")
            if not isinstance(reason, str) or not reason.strip():
                problems.append(
                    f"{label}: intentional_black_beat is set but "
                    f"black_beat_reason is missing or empty"
                )

        # A behaviour is optional - a bookend card is assembled by
        # library/tools/bookends.py and declares none - but a word outside
        # the vocabulary is a plan nothing downstream can execute, and it
        # fails here rather than becoming a dB level by default.
        if "music_behavior" in block and \
                not is_known_behavior(block["music_behavior"]):
            problems.append(
                f"{label}: music_behavior "
                f"{block['music_behavior']!r} is not in the vocabulary "
                f"{sorted(MUSIC_BEHAVIORS)}")

        if is_bookend_block(block):
            problems.extend(_bookend_problems(block, label))

        if is_picture_block(block):
            if not block["clip_id"]:
                problems.append(f"{label}: picture block has no clip_id")
            for key in ("source_start", "source_end"):
                if block[key] is None:
                    problems.append(f"{label}: picture block has {key}=None")
            if block["word_timestamps"]:
                problems.append(
                    f"{label}: picture block carries word_timestamps - "
                    f"a picture-led moment says nothing, so there is "
                    f"nothing to align"
                )
            if block["alignment_method"]:
                problems.append(
                    f"{label}: picture block carries alignment_method - "
                    f"with no word timings there is nothing it could "
                    f"describe"
                )
            continue

        if is_music_block(block):
            problems.extend(_music_ref_problems(block, label))
            continue

        if not is_speech_block(block):
            continue

        if not block["clip_id"]:
            problems.append(f"{label}: speech block has no clip_id")

        for key in ("source_start", "source_end"):
            if block[key] is None:
                problems.append(f"{label}: speech block has {key}=None")

        words = block["word_timestamps"]
        if not isinstance(words, list) or not words:
            problems.append(
                f"{label}: speech block has empty word_timestamps - "
                f"word-level alignment did not run or found no words"
            )
            continue

        if not block["alignment_method"]:
            problems.append(
                f"{label}: word_timestamps present but alignment_method "
                f"is null - the spine cannot say how they were derived"
            )

        for wi, word in enumerate(words):
            if not isinstance(word, dict):
                problems.append(f"{label}: word[{wi}] is not a dict")
                break
            word_missing = [k for k in REQUIRED_WORD_KEYS if k not in word]
            if word_missing:
                problems.append(
                    f"{label}: word[{wi}] missing keys {word_missing}"
                )
                break

    if problems:
        raise SpineContractError(
            "Spine contract violated by "
            f"{len(problems)} block(s):\n  - " + "\n  - ".join(problems)
        )


def declares_black_beat(block: dict) -> bool:
    """True when the block carries a declaration a gate may honour.

    A declaration counts only when it comes from a block that shows no
    picture of its own (a music block, intro, transition_slot or outro)
    and carries a non-empty reason - the same two conditions
    `validate_spine_blocks` enforces at emit time.  Anything else is a
    malformed declaration, and honouring it would excuse a hole the spine
    gate would have rejected.
    """
    if not isinstance(block, dict) or not block.get("intentional_black_beat"):
        return False
    if is_speech_block(block) or is_picture_block(block):
        return False
    reason = block.get("black_beat_reason")
    return isinstance(reason, str) and bool(reason.strip())


def declared_black_beat_ranges(blocks: list) -> list:
    """Timeline ranges of the blocks that validly declare a black beat.

    Returned as ``(timeline_start, timeline_end)`` pairs so a gate working
    on the rendered file - which has no spine - can still tell a chosen
    hole from an accidental one.
    """
    return [
        (block["timeline_start"], block["timeline_end"])
        for block in blocks
        if declares_black_beat(block)
    ]


def block_word_end_times(block: dict) -> list:
    """Word end times for a block, in the source domain.

    Reads the block's own `word_timestamps` - the spine is the source of
    truth, so consumers no longer re-derive this from the temporal index
    and no longer need a clip_id lookup to find it.
    """
    return [w["source_end"] for w in block["word_timestamps"]]


def source_to_timeline(source_time: float, block: dict) -> float:
    """Map a source-domain time into the timeline domain for a block."""
    return source_time - block["source_start"] + block["timeline_start"]


def timeline_to_source(timeline_time: float, block: dict) -> float:
    """Map a timeline-domain time into the source domain for a block.

    The inverse of `source_to_timeline`, and it lives beside it because
    the pair is the whole conversion: the spine is the only artifact that
    carries both domains, so it is the only place either direction can be
    computed at all.

    This direction had no implementation for the life of the pipeline,
    which is what made a timeline second unusable as an ADDRESS - "the
    captions at 45.0-72.0s are wrong" could be read by a human and by
    nothing else.  `library/tools/region.py` is the caller that turns it
    into one.

    **There is no project-wide offset, so a conversion without a block is
    not a function.**  Measured on project 001: eight speech-bearing
    blocks, eight distinct offsets, spread over 146.5s on a 56.6s
    timeline, and the sign is not even constant - seven blocks map one
    way and the eighth the other, because a later timeline block may be
    cut from an earlier second of its source clip.  A caller holding a
    time and no block has nothing to convert with.
    """
    return timeline_time - block["timeline_start"] + block["source_start"]


def blocks_overlapping(structure: list, start: float, end: float) -> list:
    """Every spine block a TIMELINE interval touches, in spine order.

    Half-open, `[start, end)`, which is the reading that makes abutting
    blocks partition the timeline rather than both claiming their shared
    instant - the spine's own blocks abut exactly (`post_bridge` lays
    each one's `timeline_start` on the previous one's `timeline_end`), so
    the closed reading would return two blocks for every boundary.

    Two edge cases are DECIDED here rather than discovered by a caller:

    - a **zero-length** interval is a point, and returns the one block
      containing it.  Read half-open the general rule returns nothing for
      `[t, t)`, which would make "what is at 12.0s?" unanswerable.
    - an interval **past the end of the timeline** returns empty rather
      than clamping to the last block.  A region nothing plays at is a
      real answer, and clamping would silently retarget a typo onto the
      outro.

    A reversed interval raises: `[10, 5)` is not an empty region, it is a
    caller with its arguments the wrong way round.
    """
    if end < start:
        raise SpineContractError(
            f"region end {end} precedes start {start}. An interval is "
            f"(start, end) in timeline seconds, both in the same domain."
        )
    if end == start:
        return [b for b in structure
                if b["timeline_start"] <= start < b["timeline_end"]]
    return [b for b in structure
            if b["timeline_end"] > start and b["timeline_start"] < end]


def block_at(structure: list, timeline_time: float):
    """The single block playing at a timeline second, or None."""
    found = blocks_overlapping(structure, timeline_time, timeline_time)
    return found[0] if found else None


def block_word_end_times_timeline(block: dict) -> list:
    """Word end times for a block, mapped into the timeline domain."""
    if not is_speech_block(block) or not block["word_timestamps"]:
        return []
    return [source_to_timeline(t, block) for t in block_word_end_times(block)]
