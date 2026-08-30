"""Which cutaway covers a spine block, for the tables that describe blocks.

Step 4.03's and step 4.04's pre-bridges both build one row per spine
block, and both read `block.get("clip_id")` off the SPINE.  A
`transition_slot` has none, so both wrote **`not measured (no source
clip)`** on every non-speech block - 5 of 13 rows on project 001, 38% of
each table, and precisely the rows where a whoosh or an impact or a
cutaway effect would go.

`b_roll_assignments` names the clip for every one of them, is 4,394
bytes, and is already in both prompts.  The statement of
un-measurability was false while the answer sat in the same context.

The two tables want different things from that clip, so this module
answers "which clip" and each bridge says its own sentence:

- **The PICTURE is measurable.** A cutaway is a real clip with a real
  camera description, and step 4.03 decides from what the picture is
  doing. Joining it turns a false absence into a measurement.
- **The SOUND is not.** A cutaway is placed `video_only: True`
  (`compile_manifest` writes it unconditionally), so its own audio is
  never heard, and counting the transients in its source would describe
  a sound nobody will hear. The honest cell names the clip AND says the
  audio does not play - it is an admitted absence with a reason, not a
  measurement and not a blank.

Whether a cutaway's ambient SHOULD play is the captain's standing hold
`vep-creative-understanding-layer-decision-cutaway-ambient-audio`; this
module states what the pipeline does today and takes no position.
"""

from __future__ import annotations

# What a bridge says about the sound under a covered non-speech block.
# One sentence, in one place, because two tables would otherwise drift.
VIDEO_ONLY_AUDIO_READING = (
    "video only - the cutaway's own audio is never heard")


def coverage_by_block(b_roll_assignments) -> dict:
    """`spine_block_position` -> the assignment covering it.

    A later assignment for the same position wins, which is the order
    `compile_manifest` resolves V2 overlaps in.
    """
    coverage = {}
    for entry in b_roll_assignments or []:
        if not isinstance(entry, dict):
            continue
        position = entry.get("spine_block_position")
        if position is None:
            continue
        coverage[position] = entry
    return coverage


def covering_assignment(block: dict, coverage: dict):
    """The cutaway covering this spine block, or None."""
    if not isinstance(block, dict):
        return None
    return coverage.get(block.get("position"))


def picture_clip_id(block: dict, coverage: dict):
    """The clip whose PICTURE this block shows, spine first then B-roll.

    A speech block plays its own clip; a non-speech block plays the
    cutaway placed over it. Returns None when neither names one, which
    is the only case that is genuinely unmeasured.
    """
    if not isinstance(block, dict):
        return None
    own = block.get("clip_id")
    if own:
        return own
    entry = covering_assignment(block, coverage)
    if isinstance(entry, dict):
        return entry.get("clip_id") or None
    return None
