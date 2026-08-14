#!/usr/bin/env python3
"""Step 3.02 pre-bridge: build the B-roll candidate table for the LLM.

The table lists what the vision analysis actually measured about each
clip: what it shows, how it is framed, how steady it is, who is in it and
which stretch of it may be cut from.

It used to be built per A-roll slot, alphabetically, truncated to 12 rows
of a 180-character single-frame caption. Every slot therefore received the
same 12 clips in the same order, four clips were never offered to any slot
at all, and the model - having no content to choose on - selected straight
down the list. One row per clip carries strictly more information in
fewer rows: the slots themselves, with their timings and visual notes,
already reach the model in `timed_spine` and `a_roll_assignments`.

The row order carries NO relevance ranking. A slot-relevance score would
need a matching rule this pipeline has never settled, and inventing one
would repeat the mistake in a subtler form; the table says so explicitly
so the model does not read list order as preference.
"""
import sys
import json

from library.tools.pipeline_validation import require_keys
from library.tools.semantic_index import build_semantic_lookup, clip_observations

# Upper bound on the candidate table. One row per clip, so this only binds
# on unusually large catalogs; whatever it drops is reported, never
# silently truncated.
MAX_CANDIDATE_CLIPS = 60

# Descriptions are time-bounded scene prose, not a caption. Long enough to
# carry every segment of a multi-scene clip.
MAX_DESCRIPTION_CHARS = 600

# Every other cell is a short label or list. Retired-schema documents put
# a whole model transcript in `analysis.objects`, so the cap is what keeps
# one bad legacy field from swamping the table.
MAX_CELL_CHARS = 200

CANDIDATE_HEADERS = [
    "clip_id", "duration_s", "used_as_aroll", "framing", "stability",
    "camera_move", "content_type", "usable_range", "subjects", "description",
]


def format_toon(headers, rows):
    out = f"[{len(rows)}]{{{','.join(headers)}}}\n"
    for row in rows:
        out += "\t".join(str(row.get(h, "")) for h in headers) + "\n"
    return out


def _cell(value, limit: int = MAX_CELL_CHARS) -> str:
    """A TOON cell: single-line, tab-free, length-capped."""
    text = " ".join(str(value or "").split())
    return text[:limit] if limit else text


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

    for clip_id, clip_info in catalog.items():
        doc = semantic.get(clip_id, {})
        observed = clip_observations(doc)
        desc = observed.get("description", "")
        if not desc:
            clips_without_description.append(clip_id)
            continue
        candidates_rows.append({
            "clip_id": clip_id,
            "duration_s": round(clip_info.get("duration_seconds", 0.0), 2),
            "used_as_aroll": "yes" if clip_id in aroll_clips else "no",
            "framing": _cell(observed.get("framing")),
            "stability": _cell(observed.get("stability")),
            "camera_move": _cell(observed.get("movement")),
            "content_type": _cell(observed.get("content_type")),
            "usable_range": _cell(observed.get("usable_ranges")),
            "subjects": _cell(observed.get("subjects")),
            "description": _cell(desc, MAX_DESCRIPTION_CHARS),
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

    # Clips already carrying A-roll are listed last: they are the least
    # interesting cutaway, not a ranking of the rest.
    candidates_rows.sort(key=lambda r: (r["used_as_aroll"] == "yes", r["clip_id"]))

    if len(candidates_rows) > MAX_CANDIDATE_CLIPS:
        dropped = [r["clip_id"] for r in candidates_rows[MAX_CANDIDATE_CLIPS:]]
        print(
            f"  Candidate table capped at {MAX_CANDIDATE_CLIPS} clips; "
            f"not offered: {', '.join(dropped)}",
            file=sys.stderr,
        )
        candidates_rows = candidates_rows[:MAX_CANDIDATE_CLIPS]

    if clips_without_description:
        print(
            f"  {len(clips_without_description)} catalog clip(s) have no "
            f"semantic description and are not offered as B-roll: "
            f"{', '.join(sorted(set(clips_without_description)))}",
            file=sys.stderr,
        )

    candidates_toon = format_toon(CANDIDATE_HEADERS, candidates_rows)

    print(json.dumps({"broll_candidates_toon": candidates_toon}))


if __name__ == "__main__":
    main()
