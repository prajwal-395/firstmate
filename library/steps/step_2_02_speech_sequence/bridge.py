#!/usr/bin/env python3
import sys
import json
import os
from library.tools.pipeline_validation import require_keys
from library.tools.project_layout import Area, ProjectLayout

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

    require_keys(data, ["temporal_index", "semantic_analysis_documents"], "step_2_02_speech_sequence/bridge.py")

    # Bypass manifest filter and read directly from pipeline_data.json
    project_dir = data.get("project_folder", "")
    ti_dir = ""
    if project_dir:
        layout = ProjectLayout(project_dir)
        state_file = str(layout.pipeline_data_path)
        if os.path.exists(state_file):
            with open(state_file, "r", encoding="utf-8") as f:
                state_data = json.load(f)
                ti_dir = state_data.get("step_outputs", {}).get("temporal_index", {}).get("index_dir", "")
        # A run that predates the recorded index_dir still has the files;
        # the layout knows where they are.
        if not ti_dir or not os.path.isdir(ti_dir):
            ti_dir = str(layout.read_dir(Area.TEMPORAL_INDEX))
    
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
    if isinstance(semantic, list):
        for doc in semantic:
            clip_id = doc.get("clip_id", "unknown")
            # Try assessment.keywords (current schema), then fall back to topics
            assessment = doc.get("assessment", {})
            keywords = assessment.get("keywords", []) if isinstance(assessment, dict) else []
            if not keywords:
                doc_data = doc.get("document", doc) if isinstance(doc, dict) else {}
                keywords = doc_data.get("topics", [])
            if keywords:
                topic_rows.append({
                    "clip_id": clip_id,
                    "topics": ", ".join(str(k) for k in keywords)
                })
    elif isinstance(semantic, dict):
        for clip_id, doc in semantic.items():
            assessment = doc.get("assessment", {})
            keywords = assessment.get("keywords", []) if isinstance(assessment, dict) else []
            if not keywords:
                doc_data = doc.get("document", {}) if isinstance(doc, dict) else {}
                keywords = doc_data.get("topics", [])
            if keywords:
                topic_rows.append({
                    "clip_id": clip_id,
                    "topics": ", ".join(str(k) for k in keywords)
                })
            
    topics_toon = format_toon(["clip_id", "topics"], topic_rows)
    
    if not transcript_rows:
        print(json.dumps({
            "error": (
                f"No transcript regions found in {ti_dir!r} - there is "
                f"nothing to build a speech sequence from"
            ),
            "step": "2.02_bridge",
        }))
        sys.exit(1)

    # Context only. This used to also emit a `speech_sequence` stub built
    # from EVERY transcript region, which is not an edit - it is the raw
    # transcript wearing the output's name, and in --auto mode it became
    # the step's answer.
    compressed = {
        "transcripts_toon": transcript_toon,
        "topics_toon": topics_toon,
    }

    
    print(json.dumps(compressed))

if __name__ == "__main__":
    main()
