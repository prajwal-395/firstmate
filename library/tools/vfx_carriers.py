"""Which track a spine block's picture plays on, and whether an effect
planned there can be drawn at all.

A visual effect is a per-clip Fusion comp.  ``compile_manifest`` looks up
the clip that covers the effect's start and merges the effect's parameters
onto it, and the renderer's Fusion pass walks
``fusion_tracks.FUSION_COMP_TRACKS`` - **V1 and V2** - so a comp reaches a
cutaway exactly as it reaches an A-roll clip.  The lookup did not: it read
V1 alone and RAISED when nothing matched::

    ValueError: 2 VFX entries do not overlap any V1 clip:
        slow_zoom_in@41.322s, slow_zoom_out@55.001s

Why this module exists
----------------------
Step 4.03's candidate table lists every spine block, cutaway blocks
included, with their camera and stability measured, under a handoff that
says *"For each clip in the shot list"*.  Nothing in the prompt said an
effect could only land on V1.  On 001's run the planner put two of its
three effects on cutaway blocks - three of the four blocks measuring
``stationary`` + ``stable`` are cutaways, which is where a drift is most
useful - and ``compile_manifest`` killed the run.

The mechanism was never missing.  What was missing was the same thing
``transition_carriers`` supplies for step 4.02: a column stating the fact,
so the model chooses knowing it (AGENTS.md 10.5).  This module states one
fact per block.  It does not filter, rank, or choose, and it never says a
block should or should not carry an effect.

What it deliberately does not do
--------------------------------
It does not move an effect.  A speech block covered by a cutaway is
reported as covered - the effect still draws on the V1 clip underneath,
which is what ``compile_manifest`` does today - rather than being
silently re-pointed at the cutaway.  Which picture an effect belongs on
when two are stacked is a creative question and nobody has answered it;
stating the stack is what makes it askable.
"""

from __future__ import annotations

import pathlib

from library.tools.broll_coverage import coverage_by_block, covering_assignment
from library.tools.transition_carriers import block_reaches_v1

# Where a block's picture is, as the answer reads in the table.
ON_V1 = "V1"
ON_V2 = "V2"
NO_PICTURE = "none"

# One short phrase per row.  The mechanism behind them is said once in
# step 4.03's handoff.md rather than sixteen times in the table.
BASIS_V1_CLIP = "{block_type} plays on V1: the effect draws on that clip"
BASIS_V1_UNDER_A_CUTAWAY = (
    "{block_type} plays on V1, and cutaway {clip_id} covers {covered} of it "
    "on V2: the effect draws on the V1 clip, which is behind the cutaway "
    "for that stretch")
BASIS_V2_CUTAWAY = (
    "{block_type} puts no clip on V1; cutaway {clip_id} on V2 is the picture "
    "here, and the effect draws on it")
BASIS_NO_PICTURE = (
    "{block_type} puts no clip on V1 and no cutaway covers it: there is no "
    "picture to draw an effect on")

def _overlap_seconds(block: dict, entry: dict) -> float:
    """How much of the block the cutaway is on screen for, in seconds."""
    try:
        b_in = float(block.get("timeline_start"))
        b_out = float(block.get("timeline_end"))
        c_in = float(entry.get("timeline_start"))
        c_out = float(entry.get("timeline_end"))
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(b_out, c_out) - max(b_in, c_in))


def _covered_phrase(block: dict, entry: dict) -> str:
    """'all' or a measured number of seconds - never a bare 'some'."""
    overlap = _overlap_seconds(block, entry)
    try:
        length = float(block.get("timeline_end")) - float(
            block.get("timeline_start"))
    except (TypeError, ValueError):
        length = 0.0
    if length > 0 and overlap >= length - 0.001:
        return "all"
    return f"{round(overlap, 1)}s"


def picture_carriers(structure: list, b_roll_assignments=None) -> list[dict]:
    """One row per spine block, saying where its picture is.

    Returns, per block::

        {"position": ..., "track": "V1"|"V2"|"none",
         "clip_id": str|None, "basis": str}

    ``clip_id`` is the clip the effect would draw on where there is one,
    which is the V1 clip for a V1 block and the cutaway for a V2 one.
    """
    coverage = coverage_by_block(b_roll_assignments)
    rows = []
    for index, block in enumerate(structure or []):
        if not isinstance(block, dict):
            continue
        block_type = block.get("block_type", "unknown")
        entry = covering_assignment(block, coverage)
        cutaway_id = entry.get("clip_id") if isinstance(entry, dict) else None

        if block_reaches_v1(block):
            track = ON_V1
            clip_id = block.get("clip_id")
            if cutaway_id:
                basis = BASIS_V1_UNDER_A_CUTAWAY.format(
                    block_type=block_type, clip_id=cutaway_id,
                    covered=_covered_phrase(block, entry))
            else:
                basis = BASIS_V1_CLIP.format(block_type=block_type)
        elif cutaway_id:
            track = ON_V2
            clip_id = cutaway_id
            basis = BASIS_V2_CUTAWAY.format(
                block_type=block_type, clip_id=cutaway_id)
        else:
            track = NO_PICTURE
            clip_id = None
            basis = BASIS_NO_PICTURE.format(block_type=block_type)

        rows.append({
            "position": block.get("position", index),
            "track": track,
            "clip_id": clip_id,
            "basis": basis,
        })
    return rows


def assert_legend_is_well_formed() -> None:
    """Step 4.03's prompt still says what each derived column IS.

    The definitions used to travel beside the table as
    ``VFX_CANDIDATES_LEGEND`` because ``handoff.md`` was under the
    captain's freeze.  The freeze lifted 2026-09-09 and the prose took
    them over, so this checks the prose - a column that reaches the
    table with the prompt silent about it is the drift the data route
    made impossible for free.
    """
    handoff = (
        pathlib.Path(__file__).resolve().parents[2]
        / "library" / "steps" / "step_4_03_plan_vfx" / "handoff.md"
    ).read_text(encoding="utf-8")
    for column in ("picture_track", "track_basis"):
        if f"`{column}`" not in handoff:
            raise ValueError(
                f"step 4.03's handoff.md never says what {column!r} is, "
                f"and the bridge ships it on every row"
            )


def _main() -> None:
    assert_legend_is_well_formed()
    print("Where a spine block's picture plays\n")
    print("  Defined in library/steps/step_4_03_plan_vfx/handoff.md, "
          "under 'Context data available'.\n")


if __name__ == "__main__":
    _main()
