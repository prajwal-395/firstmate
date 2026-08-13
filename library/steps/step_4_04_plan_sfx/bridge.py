#!/usr/bin/env python3
import sys
import json

from library.tools.sfx_library import available_sfx_types

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
    aroll_dict = data.get("a_roll_assignments", {})
    aroll = aroll_dict if isinstance(aroll_dict, list) else aroll_dict.get("a_roll_assignments", aroll_dict.get("timeline_segments", []))
    for slot in aroll:
        sfx_rows.append({
            "segment_id": slot.get("segment_id", "unknown"),
            "text": slot.get("text", "")[:50],
            "action_sfx_suggested": "No"
        })
        
    sfx_toon = format_toon(["segment_id", "text", "action_sfx_suggested"], sfx_rows)
    
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

    compressed = {
        "available_sfx_types": available,
        "sfx_candidates_toon": sfx_toon,
        "sfx_spec": {
            "sfx_list": [],
            "fairlight_preset": "default",
            "music_ducking": {
                "ducking_curves": [{
                    "trigger_type": "sfx",
                    "duck_amount_db": -10,
                    "attack_ms": 50,
                    "release_ms": 200
                }]
            }
        }
    }
    
    print(json.dumps(compressed))

if __name__ == "__main__":
    main()
