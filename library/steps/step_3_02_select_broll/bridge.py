#!/usr/bin/env python3
"""Step 3.02 pre-bridge: build the B-roll candidate table for the LLM.

For every A-roll slot this lists the clips that could cover it, excluding
the slot's own A-roll clip - covering a talking head with the same take is
not B-roll, it is the same shot twice.
"""
import sys
import json

from library.tools.pipeline_validation import require_keys
from library.tools.semantic_index import build_semantic_lookup, describe_clip

# How many candidate clips to offer per slot. Enough choice for the LLM to
# match content, small enough to keep the prompt affordable.
MAX_CANDIDATES_PER_SLOT = 12


def format_toon(headers, rows):
    out = f"[{len(rows)}]{{{','.join(headers)}}}\n"
    for row in rows:
        out += "\t".join(str(row.get(h, "")) for h in headers) + "\n"
    return out


def _slot_aroll_clip(slot: dict) -> str:
    """The clip this slot's A-roll already shows."""
    vsegs = slot.get("video_segments") or []
    if vsegs and vsegs[0].get("clip_id"):
        return vsegs[0]["clip_id"]
    return slot.get("source_clip_id") or slot.get("clip_id") or ""


def main():
    try:
        data = json.loads(sys.stdin.read())
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)

    require_keys(data, ["clip_catalog", "a_roll_assignments"], "step_3_02_select_broll/bridge.py")

    aroll = data.get("a_roll_assignments", [])
    catalog_list = data.get("clip_catalog", [])

    if isinstance(catalog_list, list):
        catalog = {c.get("clip_id"): c for c in catalog_list}
        catalog_entries = catalog_list
    else:
        catalog = catalog_list
        catalog_entries = list(catalog_list.values())

    semantic = build_semantic_lookup(
        data.get("semantic_analysis_documents", {}), catalog_entries
    )

    slots = aroll if isinstance(aroll, list) else aroll.get(
        "a_roll_assignments", aroll.get("timeline_segments", []))

    # Clips used anywhere as A-roll: usable as B-roll elsewhere, but they
    # are the least interesting choice, so rank them last.
    aroll_clips = {_slot_aroll_clip(s) for s in slots}

    candidates_rows = []
    clips_without_description = []

    for slot in slots:
        slot_id = slot.get("segment_id", slot.get("spine_block_position", "unknown"))
        slot_aroll_clip = _slot_aroll_clip(slot)

        scored = []
        for clip_id, clip_info in catalog.items():
            if clip_id == slot_aroll_clip:
                continue
            desc = describe_clip(semantic.get(clip_id, {}))
            if not desc:
                clips_without_description.append(clip_id)
                continue
            # Prefer clips not already carrying A-roll anywhere.
            rank = 1 if clip_id in aroll_clips else 0
            scored.append((rank, clip_id, desc, clip_info))

        scored.sort(key=lambda s: (s[0], s[1]))
        for _, clip_id, desc, clip_info in scored[:MAX_CANDIDATES_PER_SLOT]:
            candidates_rows.append({
                "slot_id": str(slot_id),
                "clip_id": clip_id,
                "duration_s": round(clip_info.get("duration_seconds", 0.0), 2),
                "description": desc[:180].replace("\t", " ").replace("\n", " "),
            })

    if slots and not candidates_rows:
        # An empty candidate table means the LLM has nothing to choose
        # from and the step can only produce filler. Say so and stop.
        missing = sorted(set(clips_without_description))
        print(json.dumps({
            "error": (
                f"No B-roll candidates for {len(slots)} A-roll slot(s): no "
                f"catalog clip has a usable semantic description. Clips "
                f"without one: {missing[:10]}"
            ),
            "step": "3.02_bridge",
        }))
        sys.exit(1)

    candidates_toon = format_toon(
        ["slot_id", "clip_id", "duration_s", "description"], candidates_rows)

    print(json.dumps({"broll_candidates_toon": candidates_toon}))


if __name__ == "__main__":
    main()
