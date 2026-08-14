"""The timeline spine contract.

`mesh_spine` (step 2.05) produces the timed spine that every creative step
downstream reads.  Historically each consumer guessed at where a block kept
its clip reference and its word timings - `block.get("clip_id", "")`,
`block.get("content", {}).get("clip_id")`, `source_clip_id`, ... - and when
the guess missed, the consumer degraded silently instead of failing.  That
is how beat-aligned cutting sat unreachable for four audits.

There is now exactly one shape.  Every spine block carries:

    position            block identity ("hook" or an int)
    block_type          "hook" | "speech" | anything else (non-speech)
    clip_id             source clip id, or None for non-speech blocks
    source_start        source-domain in point (None for non-speech)
    source_end          source-domain out point (None for non-speech)
    timeline_start      timeline-domain in point
    timeline_end        timeline-domain out point
    word_timestamps     list of {word, source_start, source_end};
                        non-empty for every speech/hook block
    alignment_method    how word_timestamps were derived (non-null for
                        speech/hook blocks)

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

They are optional by design: no block needs them, and neither is in
REQUIRED_BLOCK_KEYS.  A declared beat is still bounded by
`MAX_DECLARED_BLACK_BEAT_SECONDS`, and every gate that has to tell a
chosen hole from an accidental one - `compile_manifest` on the manifest,
`render_qa` on the rendered file - reads that bound and
`declared_black_beat_ranges` from here, so the two cannot drift apart.
"""

SPEECH_BLOCK_TYPES = ("speech", "hook")

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
        if block.get("intentional_black_beat"):
            if is_speech_block(block):
                problems.append(
                    f"{label}: speech block declares intentional_black_beat "
                    f"- speech is never held on black"
                )
            reason = block.get("black_beat_reason")
            if not isinstance(reason, str) or not reason.strip():
                problems.append(
                    f"{label}: intentional_black_beat is set but "
                    f"black_beat_reason is missing or empty"
                )

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

    A declaration counts only when it comes from a non-speech block and
    carries a non-empty reason - the same two conditions
    `validate_spine_blocks` enforces at emit time.  Anything else is a
    malformed declaration, and honouring it would excuse a hole the spine
    gate would have rejected.
    """
    if not isinstance(block, dict) or not block.get("intentional_black_beat"):
        return False
    if is_speech_block(block):
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


def block_word_end_times_timeline(block: dict) -> list:
    """Word end times for a block, mapped into the timeline domain."""
    if not is_speech_block(block) or not block["word_timestamps"]:
        return []
    return [source_to_timeline(t, block) for t in block_word_end_times(block)]
