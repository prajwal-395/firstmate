#!/usr/bin/env python3
"""Step 4.4 pre-bridge: the SFX candidate table the handoff tells the
model to read.

One row per SPINE BLOCK, because `spine_block_position` is what the
step's `llm_outputs` schema asks the model to emit. A table keyed by
anything else names identifiers the answer cannot use.

This table used to be built from `data["a_roll_assignments"]` and it came
out with ZERO rows on every run - see AGENTS.md 10.1 on key-name
mismatches, and the same defect in `cuts_toon` (#218). Three failures in
the same six lines:

  * no DAG edge carries `a_roll_assignments` into `plan_sfx` at all, so
    the `.get()` answered `{}` and the loop never ran;
  * had it been routed, `assign_aroll` emits entries keyed
    `spine_block_position`, not `segment_id`;
  * and those entries carry no `text` key of any kind.

`action_sfx_suggested` was the literal string "No" on every row it would
have produced. It now carries the MEASUREMENT the handoff says it is
derived from - the count of audio transients step 1.04 measured inside
the block's own source range - and not a verdict on whether the block
should get a sound. How many sound effects a piece gets is the model's
call (AGENTS.md 10.5); a pre-computed "Yes" would be this file voting.
A block with no source clip reads `not measured (no source clip)` rather
than reading as a measured absence.

`transitions_toon` is the second table, and it is separate because it
describes different things: a boundary BETWEEN two blocks, not a block.
Pairing a sound with a transition is the first purpose the handoff names
and its second evaluation criterion, and until now no DAG edge carried
`transition_spec` into this step at all - so on the run of record the two
sounds that shipped were placed at the two drawn transitions by an agent
that had planned those transitions itself minutes earlier, out of its own
memory rather than out of anything in this context. The table is keyed by
the spine block the cut leads INTO, because `spine_block_position` is the
identifier the answer names; a table keyed by timeline seconds would make
the model re-derive that join out of `timed_spine`. The plan's per-cut
`rationale` prose is deliberately left behind: it is 4.02 explaining
itself, and it is three quarters of the spec's bytes.
"""
import sys
import json

from library.tools.sfx_library import available_sfx_types
from library.tools.transition_vocabulary import is_cut

# How much of a block's line reaches the summary column. The full text is
# in `timed_spine`, which this step also routes; this table is an index
# into it, not a second copy.
TEXT_SUMMARY_CHARS = 80


def format_toon(headers, rows):
    out = f"[{len(rows)}]{{{','.join(headers)}}}\n"
    for row in rows:
        out += "\t".join(str(row.get(h, "")) for h in headers) + "\n"
    return out


def _spine_blocks(data: dict) -> list:
    """The timeline spine, whichever of its two shapes arrives."""
    spine = data.get("timed_spine") or {}
    if not isinstance(spine, dict):
        return []
    blocks = spine.get("structure")
    if blocks is None:
        blocks = (spine.get("audio_spine") or {}).get("structure", [])
    return blocks if isinstance(blocks, list) else []


def _temporal_lookup(data: dict) -> dict:
    """Per-clip temporal indices, keyed by `clip_id`.

    Step 1.04 keys these by catalog id - the same vocabulary the spine
    speaks - so this join needs no stem translation. The DAG routes
    `full_indices` here under the name `temporal_event_indices`; the
    dict-wrapped shape is accepted because step 1.04's own output holds
    both lists under one key.
    """
    raw = data.get("temporal_event_indices", [])
    if isinstance(raw, dict):
        raw = raw.get("temporal_event_indices", raw.get("full_indices", []))
    if not isinstance(raw, list):
        return {}
    return {ti["clip_id"]: ti for ti in raw
            if isinstance(ti, dict) and ti.get("clip_id")}


def _summary_text(block: dict) -> str:
    """What this segment is, in one line.

    The spoken line where there is one; otherwise the spine's own note
    for the beat, which is the only description a non-speech block has.
    """
    content = block.get("content")
    text = ""
    if isinstance(content, dict):
        text = content.get("text") or ""
    if not text:
        text = block.get("visual_note") or ""
    text = " ".join(str(text).split())
    if len(text) > TEXT_SUMMARY_CHARS:
        text = text[:TEXT_SUMMARY_CHARS - 3] + "..."
    return text


def transient_count(block: dict, temporal_lookup: dict):
    """Audio transients measured inside this block's source range.

    Returns None when nothing measured this block - no source clip
    (a transition slot, a bookend card), or no temporal index for the
    clip it names. None is "not measured", never zero.
    """
    clip_id = block.get("clip_id")
    if not clip_id:
        return None
    index = temporal_lookup.get(clip_id)
    if not index:
        return None
    peaks = (index.get("energy_curve") or {}).get("peak_times")
    if not isinstance(peaks, list):
        return None
    start = block.get("source_start")
    end = block.get("source_end")
    if start is None or end is None:
        return None
    return sum(1 for t in peaks
               if isinstance(t, (int, float)) and start <= t <= end)


def _cut_into_block_position(cut_seconds, spine_blocks: list, fps: float):
    """The spine block a cut at `cut_seconds` leads into.

    Step 4.02 records `cut_point_original` as the incoming block's own
    `timeline_start`, so this is a lookup and not a search. The one frame
    of slack is for the rounding on either side of that record - it is a
    timebase tolerance, not a judgement about which cut is meant - and a
    cut that lands further out than that reads `unresolved` rather than
    being attached to the nearest block.

    The FIRST block is skipped: a cut is a boundary before a block, so
    the opening block has no incoming cut and a transition resolving
    onto it would put a sound at the top of the video that the plan
    never asked for.
    """
    if cut_seconds is None or not spine_blocks:
        return None
    tolerance = 1.0 / fps if fps else 1.0 / 30.0
    best = None
    for block in spine_blocks[1:]:
        start = block.get("timeline_start")
        if not isinstance(start, (int, float)):
            continue
        delta = abs(float(start) - float(cut_seconds))
        if best is None or delta < best[0]:
            best = (delta, block.get("position"))
    if best is None or best[0] > tolerance:
        return None
    return best[1]


def build_transition_rows(data: dict) -> list:
    """One row per planned cut, keyed by the block it cuts into."""
    spine_blocks = _spine_blocks(data)
    fps = data.get("project_fps") or 30.0
    spec = data.get("transition_spec") or []
    if isinstance(spec, dict):
        spec = spec.get("transition_spec", [])
    if not isinstance(spec, list):
        return []

    rows = []
    for trans in spec:
        if not isinstance(trans, dict):
            continue
        cut_seconds = trans.get("cut_point_original")
        if cut_seconds is None:
            cut_seconds = trans.get("cut_point_timeline")
        position = _cut_into_block_position(cut_seconds, spine_blocks, fps)
        ttype = trans.get("transition_type", "")
        rows.append({
            "spine_block_position": (
                "unresolved" if position is None else position
            ),
            "transition_type": ttype,
            # A hard cut, a jump cut and a match cut are the absence of
            # decoration (AGENTS.md 10.5) - they are real editorial
            # labels that put nothing on screen. The handoff's second
            # criterion is about a CREATIVE transition, so the model has
            # to be able to tell the two apart.
            "draws_on_screen": "no" if is_cut(ttype) else "yes",
            "duration_frames": trans.get("duration_frames", ""),
            "cut_point_seconds": (
                "" if cut_seconds is None else cut_seconds
            ),
        })
    return rows


def build_sfx_candidates(data: dict) -> list:
    """One row per spine block, keyed by the position the answer names."""
    temporal_lookup = _temporal_lookup(data)
    rows = []
    for block in _spine_blocks(data):
        if not isinstance(block, dict):
            continue
        count = transient_count(block, temporal_lookup)
        rows.append({
            "segment_id": block.get("position"),
            "text": _summary_text(block),
            "action_sfx_suggested": (
                "not measured (no source clip)" if count is None
                else f"{count} audio transient{'' if count == 1 else 's'}"
            ),
        })
    return rows


def main():
    try:
        data = json.loads(sys.stdin.read())
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)

    sfx_toon = format_toon(
        ["segment_id", "text", "action_sfx_suggested"],
        build_sfx_candidates(data),
    )

    available = available_sfx_types()
    if not available:
        print(json.dumps({
            "error": (
                "The SFX library resolves no usable sound types - check "
                "PIPELINE_SFX_LIBRARY and its index"
            ),
            "step": "4.04_bridge",
        }))
        sys.exit(1)

    # No `sfx_spec` stub. This bridge used to emit
    # `{"sfx_list": [], "fairlight_preset": "default"}`, and because a
    # pre-bridge key is restored into the prompt whatever the projection
    # says (AGENTS.md 10.1), the step was handed its own empty output as
    # input on every run. It answered nothing and read as a plan that had
    # already decided to place no sounds. The post-bridge writes the real
    # `sfx_spec` after the model answers.
    compressed = {
        "available_sfx_types": available,
        "sfx_candidates_toon": sfx_toon,
        "transitions_toon": format_toon(
            ["spine_block_position", "transition_type", "draws_on_screen",
             "duration_frames", "cut_point_seconds"],
            build_transition_rows(data),
        ),
    }

    print(json.dumps(compressed))


if __name__ == "__main__":
    main()
