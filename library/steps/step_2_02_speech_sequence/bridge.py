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

    # Which catalogued audio files the voiceover/music spine may draw
    # speech from. `audio_indices` is step 1.04's sound-only index, one
    # entry per catalogued voiceover/music file - the transcript rows
    # below already include those files (they sit in the same index
    # directory under their own `audio_001` names), and this is what
    # names them as voiceover rather than on-camera speech. Entries
    # carrying an `error` failed to index: their speech is heard from
    # nowhere, and the run says so here - at the step that selects
    # speech - rather than failing a stage later on a passage nothing
    # can align.
    audio_indices = data.get("audio_indices") or []
    audio_ids = set()
    for entry in audio_indices:
        if not isinstance(entry, dict):
            continue
        audio_id = entry.get("audio_id", "")
        if audio_id:
            audio_ids.add(audio_id)
        if entry.get("error"):
            print(
                f"  WARNING: audio file {audio_id or '?'} failed to "
                f"index ({entry['error']}) - its speech cannot be "
                f"selected",
                file=sys.stderr,
            )

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
        # Sorted: this table IS the transcript the prompt carries, and
        # `os.listdir` order is the filesystem's, so the same project
        # could present its clips in a different order on every run.
        for fname in sorted(os.listdir(ti_dir)):
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
    
    transcript_rows.sort(key=lambda r: (r["clip_id"], float(r["start"])))

    voiceover_rows = sum(1 for r in transcript_rows
                         if r["clip_id"] in audio_ids)
    if voiceover_rows:
        print(
            f"  {voiceover_rows} voiceover region(s) from "
            f"{len(audio_ids)} catalogued audio file(s) - their "
            f"clip_ids name audio files, and a passage cut from one "
            f"is voiceover over B-roll, not on-camera speech",
            file=sys.stderr,
        )

    # `clip_id, start, end, text`, once. The same 110 lines also reached
    # this step as `view:transcript`, in a different column order and a
    # different sort - 19,844 characters, a quarter of the context, and
    # the two copies disagreed about which column was the start time.
    # This is the copy the handoff names, so the view was withdrawn from
    # 2.02's `context_fields`; `creative_direction`, which has no bridge,
    # still reads it.
    transcript_toon = format_toon(["clip_id", "start", "end", "text"], transcript_rows)
    
    # Extract scene-level topic summaries
    from library.tools.semantic_index import build_semantic_lookup
    semantic = data.get("semantic_analysis_documents", {})
    clip_catalog = data.get("clip_catalog", [])
    if isinstance(clip_catalog, dict):
        clip_catalog = list(clip_catalog.values())
        
    semantic_lookup = {}
    if clip_catalog:
        semantic_lookup = build_semantic_lookup(semantic, clip_catalog)
    else:
        if isinstance(semantic, list):
            for doc in semantic:
                if "clip_id" in doc:
                    semantic_lookup[doc["clip_id"]] = doc
        elif isinstance(semantic, dict):
            for cid, doc in semantic.items():
                semantic_lookup[cid] = doc
    
    topic_rows = []
    for catalog_id, doc in semantic_lookup.items():
        assessment = doc.get("assessment", {})
        keywords = assessment.get("keywords", []) if isinstance(assessment, dict) else []
        if not keywords:
            doc_data = doc.get("document", doc) if isinstance(doc, dict) else {}
            keywords = doc_data.get("topics", [])
        if keywords:
            topic_rows.append({
                "clip_id": catalog_id,
                "topics": ", ".join(str(k) for k in keywords)
            })
            
    topics_toon = format_toon(["clip_id", "topics"], topic_rows)
    
    if not transcript_rows:
        # No transcribed speech - but a BROKEN index and an EMPTY one
        # are different facts. A missing or unreadable index is a
        # defect upstream and refuses loudly. A valid index with zero
        # speech regions is the footage saying nothing (music, montage,
        # no dialogue), and the step answers it deterministically: an
        # EMPTY body. The runner then has nothing left to ask the model
        # (outputs minus what is already in hand is empty), so the call
        # is skipped and the run proceeds speechless - no model, no
        # human, no --skip. The old stub defect cannot recur here:
        # that one built a sequence FROM regions, and there are none.
        if not ti_dir or not os.path.isdir(ti_dir):
            print(json.dumps({
                "error": (
                    f"No transcript regions found and no readable "
                    f"temporal index at {ti_dir!r} - there is "
                    f"nothing to build a speech sequence from"
                ),
                "step": "2.02_bridge",
            }))
            sys.exit(1)
        from library.tools.project_shape import declared_shape
        from library.tools.project_shape import expects_speech
        shape = declared_shape(project_dir)
        if shape and expects_speech(shape):
            reason_excluded = (
                "no transcribed speech in the footage - zero speech "
                "regions in the temporal index, although the project's "
                f"declared shape ({shape}) expects speech"
            )
        else:
            reason_excluded = (
                "no transcribed speech in the footage - zero speech "
                "regions in the temporal index"
            )
        print(
            f"  No speech regions in {ti_dir!r} - emitting an empty "
            f"body_sequence; the edit is led by music or picture",
            file=sys.stderr,
        )
        print(json.dumps({
            "transcripts_toon": format_toon(
                ["clip_id", "start", "end", "text"], []),
            "topics_toon": topics_toon,
            "speech_sequence": {
                "body_sequence": [],
                "excluded_passages": [{
                    "reason_excluded": reason_excluded,
                }],
            },
        }))
        return

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
