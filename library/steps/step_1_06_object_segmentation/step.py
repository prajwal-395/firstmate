import json
import sys
import os
from pathlib import Path

# Add repo root to PYTHONPATH to ensure library imports work
repo_root = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(repo_root))

from library.tools.analysis.object_segmentation import get_segmenter
from library.tools.project_layout import Area, ProjectLayout

def run_step(raw_footage_files: list, clip_catalog: list, output_dir: str,
             face_boxes_by_clip: dict = None):
    """
    Run object segmentation on each clip in the catalog.

    `face_boxes_by_clip` maps clip_id to the normalized [x1, y1, x2, y2]
    first-frame face box from that clip's `face_presence` block (issue
    #268). When the map is given, every clip is seeded from its face box
    and a clip with no box is honestly declined; when it is None the
    legacy blob seeding runs. The map arriving at all is what opts in -
    a clip merely absent from it reads the same as no face, never as a
    blob run, because mixing the two seedings in one run would make the
    object labels mean different things per clip.
    """
    segmenter = get_segmenter()
    results = []
    
    out_dir_path = Path(output_dir)
    out_dir_path.mkdir(parents=True, exist_ok=True)
    
    for clip in clip_catalog:
        clip_path = clip["path"]
        clip_id = clip["clip_id"]
        
        if not os.path.exists(clip_path):
            print(f"WARNING: File not found {clip_path}", file=sys.stderr)
            continue
            
        print(f"Segmenting {clip_path}...", file=sys.stderr)
        try:
            # Segment the clip. A face-box map opts the whole run into
            # face seeding: box where one arrived, honest decline where not.
            face_seeded = face_boxes_by_clip is not None
            if face_seeded:
                face_box = face_boxes_by_clip.get(clip_id)
                seg_result = segmenter.segment_clip(
                    clip_path, sample_fps=2.0,
                    face_box=face_box, require_face=True)
            else:
                seg_result = segmenter.segment_clip(
                    clip_path, sample_fps=2.0)
            
            # Save the detailed masks and metadata to output_dir
            seg_result.save(str(out_dir_path))
            
            # Rename output to use clip_id instead of stem to prevent collisions
            stem = Path(clip_path).stem
            old_file = out_dir_path / f"{stem}_segmentation.json"
            new_file_name = f"{clip_id}_segmentation.json"
            new_file = out_dir_path / new_file_name
            if old_file.exists() and old_file != new_file:
                os.rename(old_file, new_file)
            
            # Add summary to step results
            results.append({
                "clip_id": clip_id,
                "video_path": clip_path,
                "segmentation_file": new_file_name,
                "object_count": len(seg_result.objects),
                "seed_note": seg_result.seed_note,
            })
            
        except Exception as e:
            print(f"ERROR: Failed to segment {clip_path}: {e}", file=sys.stderr)
            
    return {
        "object_segmentation": results
    }

def main():
    try:
        input_data = json.loads(sys.stdin.read())
    except json.JSONDecodeError:
        print(json.dumps({"error": "Invalid JSON input", "step": "1.06_object_segmentation"}))
        sys.exit(1)
        
    raw_footage_files = input_data.get("raw_footage_files")
    clip_catalog = input_data.get("clip_catalog")

    if not raw_footage_files or not clip_catalog:
        print(json.dumps({
            "error": "Missing required input: raw_footage_files or clip_catalog",
            "step": "1.06_object_segmentation"
        }))
        sys.exit(1)

    # Optional opt-in to face seeding (issue #268): clip_id to normalized
    # first-frame face box. Absent means the legacy blob seeding runs.
    face_boxes_by_clip = input_data.get("face_boxes_by_clip")
    if face_boxes_by_clip is not None and not isinstance(
            face_boxes_by_clip, dict):
        print(json.dumps({
            "error": "face_boxes_by_clip must be a clip_id to box map",
            "step": "1.06_object_segmentation"
        }))
        sys.exit(1)
        
    # Where masks land is the layout owner's call, not this step's.
    # See library/tools/project_layout.py.
    project_folder = input_data.get("project_folder") or os.getcwd()
    output_dir = str(ProjectLayout(project_folder).write_dir(
        Area.SEGMENTATION, step="object_segmentation"))
    
    result = run_step(raw_footage_files, clip_catalog, output_dir,
                      face_boxes_by_clip=face_boxes_by_clip)
    json.dump(result, sys.stdout, indent=2)

if __name__ == "__main__":
    main()
