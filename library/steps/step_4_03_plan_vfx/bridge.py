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

    # Simplified pre-bridge context extraction for VFX planning
    vfx_rows = []
    
    # In a real implementation we would look at semantic_analysis and timed_spine.
    # Here we just pass an empty list or basic summary to the LLM.
    aroll = data.get("a_roll_assignments", {}).get("timeline_segments", [])
    for slot in aroll:
        vfx_rows.append({
            "segment_id": slot.get("segment_id", "unknown"),
            "text": slot.get("text", "")[:50],
            "vfx_suggested": "No"
        })
        
    vfx_toon = format_toon(["segment_id", "text", "vfx_suggested"], vfx_rows)
    
    compressed = {
        "vfx_candidates_toon": vfx_toon
    }
    
    print(json.dumps(compressed))

if __name__ == "__main__":
    main()
