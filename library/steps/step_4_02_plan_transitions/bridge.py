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
    beat_positions = real_beat_positions(music_analysis)
    bpm = real_bpm(music_analysis) or 0

    semantic_data = data.get("semantic_analysis", {})
    if isinstance(semantic_data, dict) and "semantic_analysis" in semantic_data:
        sem_inner = semantic_data["semantic_analysis"]
        if isinstance(sem_inner, list):
            semantic_clips = sem_inner
        else:
            semantic_clips = sem_inner.get("clips", [])
    elif isinstance(semantic_data, list):
        semantic_clips = semantic_data
    else:
        semantic_clips = semantic_data.get("clips", [])
        
    semantic_lookup = {c.get("clip_id"): c for c in semantic_clips}
    
    cut_rows = []
    
    # We will output a cuts table
    for i in range(1, len(spine_blocks)):
        prev_block = spine_blocks[i-1]
        curr_block = spine_blocks[i]
        
        cut_time = curr_block.get("timeline_start", 0.0)
        cut_type = f"{prev_block.get('type', 'unknown')}-to-{curr_block.get('type', 'unknown')}"
        
        beat_near_cut = "No"
        if beat_positions:
            closest = min(beat_positions, key=lambda b: abs(b - cut_time))
            delta = abs(closest - cut_time)
            if delta <= 0.10:
                beat_near_cut = f"Yes ({delta:.2f}s away)"
                
        prev_cid = prev_block.get("clip_id")
        curr_cid = curr_block.get("clip_id")
        
        prev_sem = semantic_lookup.get(prev_cid, {}) if prev_cid else {}
        curr_sem = semantic_lookup.get(curr_cid, {}) if curr_cid else {}
        
        # Build description strings
        def get_desc(sem):
            if not sem: return "none"
            mood = sem.get("mood", "")
            tags = sem.get("tags", sem.get("keywords", []))
            tags_str = ", ".join(tags) if isinstance(tags, list) else str(tags)
            return f"Mood: {mood} | Tags: {tags_str}"
            
        cut_rows.append({
            "cut_point_position": curr_block.get("position", i),
            "cut_time": f"{cut_time:.2f}",
            "type": cut_type,
            "beat_near_cut": beat_near_cut,
            "outgoing_footage": get_desc(prev_sem),
            "incoming_footage": get_desc(curr_sem)
        })
        
    cuts_toon = format_toon(["cut_point_position", "cut_time", "type", "beat_near_cut", "outgoing_footage", "incoming_footage"], cut_rows)
    
    compressed = {
        "cuts_toon": cuts_toon
    }
    
    print(json.dumps(compressed))

if __name__ == "__main__":
    main()
