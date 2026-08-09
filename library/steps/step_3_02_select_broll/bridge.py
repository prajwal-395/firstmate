#!/usr/bin/env python3
import sys
import json
from library.tools.pipeline_validation import require_keys

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

    require_keys(data, ["clip_catalog", "a_roll_assignments"], "step_3_02_select_broll/bridge.py")

    aroll = data.get("a_roll_assignments", [])
    catalog_list = data.get("clip_catalog", [])
    
    # Handle catalog as list or dict
    catalog = {}
    if isinstance(catalog_list, list):
        for c in catalog_list:
            catalog[c.get("clip_id")] = c
    else:
        catalog = catalog_list

    semantic = data.get("semantic_analysis_documents", {})
    
    slots = aroll if isinstance(aroll, list) else aroll.get("timeline_segments", [])
    
    candidates_rows = []
    
    for slot in slots:
        slot_id = slot.get("segment_id", slot.get("spine_block_position", "unknown"))
        vsegs = slot.get("video_segments", [])
        slot_aroll_clip = vsegs[0].get("clip_id", "") if vsegs else slot.get("source_clip_id", "")
        
        count = 0
        for clip_id, clip_info in catalog.items():
            if clip_id == slot_aroll_clip:
                continue
            
            # Get summary from semantic
            clip_sem = {}
            if isinstance(semantic, list):
                for d in semantic:
                    if d.get("clip_id") == clip_id:
                        clip_sem = d.get("document", d) if isinstance(d, dict) else {}
                        break
            else:
                clip_sem_raw = semantic.get(clip_id, {})
                clip_sem = clip_sem_raw.get("document", clip_sem_raw) if isinstance(clip_sem_raw, dict) else {}
                
            desc = clip_sem.get("visual_description", "")[:100]
            
            candidates_rows.append({
                "slot_id": str(slot_id),
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
        "broll_candidates_toon": candidates_toon,
        "b_roll_assignments": []
    }
    print(json.dumps(compressed))

if __name__ == "__main__":
    main()
