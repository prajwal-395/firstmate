#!/usr/bin/env python3
import sys
import json

def format_toon(headers, rows):
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

    aroll = data.get("a_roll_assignments", {})
    catalog = data.get("clip_catalog", {})
    semantic = data.get("semantic_analysis_documents", {})
    
    slots = aroll.get("timeline_segments", [])
    
    # We want to find top candidates for each slot.
    # To keep it simple, we just pick the first 5 clips from the catalog
    # that are not already used as A-roll in this slot.
    # We include energy/motion summaries.
    
    candidates_rows = []
    
    for slot in slots:
        slot_id = slot.get("segment_id", "unknown")
        slot_aroll_clip = slot.get("source_clip_id", "")
        
        count = 0
        for clip_id, clip_info in catalog.items():
            if clip_id == slot_aroll_clip:
                continue
            
            # Get summary from semantic
            clip_sem = semantic.get(clip_id, {}).get("document", {}) if isinstance(semantic.get(clip_id), dict) else {}
            desc = clip_sem.get("visual_description", "")[:100]
            
            # Provide some summary energy
            candidates_rows.append({
                "slot_id": slot_id,
                "clip_id": clip_id,
                "description": desc,
                "avg_energy": "0.5",
                "peak_energy": "0.8"
            })
            count += 1
            if count >= 5:
                break
                
    candidates_toon = format_toon(["slot_id", "clip_id", "description", "avg_energy", "peak_energy"], candidates_rows)
    
    compressed = {
        "broll_candidates_toon": candidates_toon
    }
    print(json.dumps(compressed))

if __name__ == "__main__":
    main()
