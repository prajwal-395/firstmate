#!/usr/bin/env python3
import sys
import json
import os

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

    temporal_index = data.get("temporal_index", {})
    ti_dir = temporal_index.get("index_dir", "") if isinstance(temporal_index, dict) else ""
    
    # Extract transcript segments
    transcript_rows = []
    if ti_dir and os.path.isdir(ti_dir):
        for fname in os.listdir(ti_dir):
            if fname.endswith(".json"):
                clip_id = fname[:-5]
                with open(os.path.join(ti_dir, fname)) as f:
                    ti = json.load(f)
                    for region in ti.get("speech_regions", []):
                        transcript_rows.append({
                            "clip_id": clip_id,
                            "start": f"{region.get('start', 0.0):.2f}",
                            "end": f"{region.get('end', 0.0):.2f}",
                            "text": region.get("text", "").replace("\n", " ")
                        })
    
    transcript_toon = format_toon(["clip_id", "start", "end", "text"], transcript_rows)
    
    # Extract scene-level topic summaries
    semantic = data.get("semantic_analysis_documents", {})
    topic_rows = []
    for clip_id, doc in semantic.items():
        doc_data = doc.get("document", {}) if isinstance(doc, dict) else {}
        topics = doc_data.get("topics", [])
        if topics:
            topic_rows.append({
                "clip_id": clip_id,
                "topics": ", ".join(topics)
            })
            
    topics_toon = format_toon(["clip_id", "topics"], topic_rows)
    
    compressed = {
        "transcripts_toon": transcript_toon,
        "topics_toon": topics_toon
    }
    
    print(json.dumps(compressed))

if __name__ == "__main__":
    main()
