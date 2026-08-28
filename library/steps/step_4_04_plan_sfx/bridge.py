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
"""
import sys
import json

from library.tools.sfx_library import available_sfx_types

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
    }

    print(json.dumps(compressed))


if __name__ == "__main__":
    main()
