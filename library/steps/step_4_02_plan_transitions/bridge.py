#!/usr/bin/env python3
import sys
import json
import math

def format_toon(headers, rows):
    if not rows:
        return f"[0]{{{','.join(headers)}}}\n"
    out = f"[{len(rows)}]{{{','.join(headers)}}}\n"
    for row in rows:
        out += "\t".join(str(row.get(h, "")) for h in headers) + "\n"
    return out

def main():
    try:
        data = json.loads(sys.stdin.read())
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)

    timed_spine = data.get("timed_spine", {})
    spine_blocks = timed_spine.get("structure", timed_spine.get("audio_spine", {}).get("structure", []))

    music_selection = data.get("music_selection", {})

    # The REAL beat grid, so the context the LLM plans against matches the
    # grid post_bridge actually snaps to. This used to synthesise
    # [i * 60/bpm for i in ...] from t=0; see library/tools/beat_grid.py.
    from library.tools.beat_grid import beat_positions as real_beat_positions
    from library.tools.beat_grid import bpm as real_bpm

    music_analysis = data.get("music_analysis", {})
    beat_positions = real_beat_positions(music_analysis, music_selection)
    bpm = real_bpm(music_analysis) or 0

    # Step 1.03 keys its documents by the media file's STEM (`IMG_1816`)
    # and the spine speaks catalog ids (`clip_011`), so keying the lookup
    # on the document's own `clip_id` matched nothing and every row of
    # this table read "none" - AGENTS.md 10.1, and the same join failure
    # that emptied the B-roll candidate table. `build_semantic_lookup` is
    # the one place that join is done.
    from library.tools.semantic_index import (
        build_semantic_lookup,
        clip_observations,
        clip_tags,
    )

    semantic_lookup = build_semantic_lookup(
        data.get("semantic_analysis", {}), data.get("clip_catalog", []) or [])

    # A cutaway block carries no `clip_id` of its own - the spine leaves the
    # slot and step 3.02 fills it - so the picture either side of a cut INTO
    # a cutaway was unattributable and read "none". That is the half of the
    # cut most likely to want a transition, so resolve it through the
    # assignment that filled the slot.
    broll_raw = data.get("b_roll_assignments", [])
    if isinstance(broll_raw, dict):
        broll_raw = broll_raw.get("b_roll_assignments", [])
    broll_by_position = {}
    for assignment in broll_raw or []:
        if not isinstance(assignment, dict):
            continue
        position = assignment.get("spine_block_position")
        if position is not None and assignment.get("clip_id"):
            broll_by_position[str(position)] = assignment["clip_id"]

    def block_clip_id(block):
        return (block.get("clip_id")
                or broll_by_position.get(str(block.get("position"))))

    # Which cuts can carry a DRAWN transition at all, read off the same
    # V1-membership rule compile_manifest builds its V1 track from. Every
    # B-roll placement goes on V2, so a cut whose outgoing block is a
    # transition slot has no V1 clip ending on it and compile_manifest
    # refuses a drawn transition there - it failed 001's run of record
    # that way. This states the fact per cut; nothing is filtered,
    # re-ordered or chosen for the model (AGENTS.md 10.5).
    from library.tools.transition_carriers import CUTS_LEGEND, cut_carriers

    carriers = {str(row["position"]): row for row in cut_carriers(spine_blocks)}

    # Step 3.03 is asked to judge how each cut READS - Check 7 of its
    # handoff, "smooth | acceptable | jarring | broken" - and its answer
    # used to go nowhere: `cut_decisions` appeared once in the whole
    # repository, in its own manifest. This is the reader. It states the
    # review's verdict per cut beside the buildability columns and
    # decides nothing: no rule here turns `jarring` into a transition,
    # and an unjudged cut is spelled `unjudged` rather than borrowing the
    # mild end of the scale (AGENTS.md 10.5).
    from library.tools import cut_verdicts as cv

    verdicts = cv.verdicts_by_cut(data.get("cut_decisions"))

    cut_rows = []
    
    # We will output a cuts table
    for i in range(1, len(spine_blocks)):
        prev_block = spine_blocks[i-1]
        curr_block = spine_blocks[i]
        
        cut_time = curr_block.get("timeline_start", 0.0)
        # `block_type` is the spine's own key (library/tools/spine_contract.py).
        # Reading `type` made every row of this table read
        # "unknown-to-unknown", which is the classification the handoff
        # tells the model to plan against.
        cut_type = (f"{prev_block.get('block_type', 'unknown')}"
                    f"-to-{curr_block.get('block_type', 'unknown')}")
        
        beat_near_cut = "No"
        if beat_positions:
            closest = min(beat_positions, key=lambda b: abs(b - cut_time))
            delta = abs(closest - cut_time)
            if delta <= 0.10:
                beat_near_cut = f"Yes ({delta:.2f}s away)"
                
        prev_cid = block_clip_id(prev_block)
        curr_cid = block_clip_id(curr_block)
        
        prev_sem = semantic_lookup.get(prev_cid, {}) if prev_cid else {}
        curr_sem = semantic_lookup.get(curr_cid, {}) if curr_cid else {}
        
        # What the vision pass MEASURED about the shot either side of the
        # cut, and nothing else. v3 measures no mood and no energy
        # (AGENTS.md 10.1), so no mood is reported - a transition planner
        # handed an invented one would be planning against taste no step
        # produced (AGENTS.md 10.5). Framing and camera movement are what
        # bear on a cut: a moving shot into a static one is a different
        # edit from two locked-off shots of the same subject.
        def get_desc(sem):
            if not sem:
                return "none"
            obs = clip_observations(sem)
            parts = []
            if obs.get("framing"):
                parts.append(f"Framing: {obs['framing']}")
            if obs.get("movement"):
                parts.append(f"Camera: {obs['movement']}")
            if obs.get("stability"):
                parts.append(f"Stability: {obs['stability']}")
            tags = sorted(clip_tags(sem))
            if tags:
                parts.append(f"Tags: {', '.join(tags)}")
            return " | ".join(parts) if parts else "measured nothing"

        # Beside `type`, which is what it is derived from, and ahead of
        # the beat column - because the beat guidance is what steered at
        # these cuts when nothing said they were unbuildable.
        carrier = carriers.get(str(curr_block.get("position", i)), {})

        position = curr_block.get("position", i)

        cut_rows.append({
            "cut_point_position": position,
            "cut_time": f"{cut_time:.2f}",
            "type": cut_type,
            "can_carry_drawn_transition": carrier.get("verdict", ""),
            "carry_basis": carrier.get("basis", ""),
            "narrative_verdict": cv.verdict_column(verdicts, position),
            "verdict_note": cv.note_column(verdicts, position),
            "beat_near_cut": beat_near_cut,
            "outgoing_footage": get_desc(prev_sem),
            "incoming_footage": get_desc(curr_sem)
        })

    cuts_toon = format_toon(["cut_point_position", "cut_time", "type", "can_carry_drawn_transition", "carry_basis", "narrative_verdict", "verdict_note", "beat_near_cut", "outgoing_footage", "incoming_footage"], cut_rows)

    cuts_legend = dict(CUTS_LEGEND)
    cuts_legend.update(cv.CUT_VERDICT_LEGEND)

    compressed = {
        "cuts_toon": cuts_toon,
        # The handoff is frozen and cannot name the derived columns, so
        # their definition travels as data - the route step 2.04 takes
        # for MEASUREMENT_LEGEND.
        "cuts_legend": cuts_legend,
    }

    # How much of the review's judgement is missing, said rather than
    # left to be inferred from a column of `unjudged`. Empty when every
    # cut was judged, and absent from the context in that case.
    unjudged = cv.unjudged_summary(
        verdicts, [row["cut_point_position"] for row in cut_rows])
    if unjudged:
        compressed["cuts_unjudged"] = unjudged
    
    print(json.dumps(compressed))

if __name__ == "__main__":
    main()
