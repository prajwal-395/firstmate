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
                             that no block declares.
    black_beat_reason        Non-empty string saying WHY the beat is there.
                             A declaration without one is a rubber stamp and
                             is rejected at compile time.

They are optional by design: no block needs them, and neither is in
REQUIRED_BLOCK_KEYS.  A declared beat is still bounded - see
`MAX_DECLARED_BLACK_BEAT_SECONDS` in step 5.04.
"""

SPEECH_BLOCK_TYPES = ("speech", "hook")

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


def validate_spine_blocks(blocks: list) -> None:
    """Raise SpineContractError if any block violates the spine contract.

    Called by mesh_spine before it emits, so a malformed spine never
    reaches the creative steps that read it.
    """
    problems = []

    for i, block in enumerate(blocks):
        if not isinstance(block, dict):
            problems.append(f"block[{i}] is {type(block).__name__}, not a dict")
            continue

        label = f"block[{i}] (position={block.get('position', '?')})"

        missing = [k for k in REQUIRED_BLOCK_KEYS if k not in block]
        if missing:
            problems.append(f"{label}: missing required keys {missing}")
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
