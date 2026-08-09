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

    sfx_rows = []
    
    # Just an example of how we might extract cuts and actions for SFX planning.
    aroll = data.get("a_roll_assignments", {}).get("timeline_segments", [])
    for slot in aroll:
        sfx_rows.append({
            "segment_id": slot.get("segment_id", "unknown"),
            "text": slot.get("text", "")[:50],
            "action_sfx_suggested": "No"
        })
        
    sfx_toon = format_toon(["segment_id", "text", "action_sfx_suggested"], sfx_rows)
    
    compressed = {
        "sfx_candidates_toon": sfx_toon
    }
    
    print(json.dumps(compressed))

if __name__ == "__main__":
    main()
