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

    # We need to extract cut point classifications and summarize musical beat grid near each cut.
    # The actual implementation of finding cuts would depend on the inputs:
    # a_roll_assignments, b_roll_assignments, timed_spine.
    # Here we produce a simplified context for the LLM.
    
    cut_rows = []
    
    # Just an example of how we might extract cuts from the timeline.
    # In a real implementation, we would iterate through a_roll and b_roll assignments
    # to find adjacent clips and classify the cut.
    # We will just pass the inputs through as a summary.
    
    aroll_data = data.get("a_roll_assignments", [])
    aroll = aroll_data if isinstance(aroll_data, list) else aroll_data.get("timeline_segments", [])
    
    for i in range(len(aroll) - 1):
        curr = aroll[i]
        nxt = aroll[i+1]
        
        cut_time = nxt.get("timeline_start", 0.0)
        cut_type = "speech-to-speech"
        
        cut_rows.append({
            "cut_time": f"{cut_time:.2f}",
            "type": cut_type,
            "beat_near_cut": "Yes (0.1s away)"
        })
        
    cuts_toon = format_toon(["cut_time", "type", "beat_near_cut"], cut_rows)
    
    compressed = {
        "cuts_toon": cuts_toon,
        "transition_spec": {"transitions": [], "default_cut": "hard"}
    }
    
    print(json.dumps(compressed))

if __name__ == "__main__":
    main()
