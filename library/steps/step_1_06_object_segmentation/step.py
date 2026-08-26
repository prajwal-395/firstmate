import json
import sys
import os
from pathlib import Path

# Add repo root to PYTHONPATH to ensure library imports work
repo_root = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(repo_root))

from library.tools.analysis.object_segmentation import get_segmenter
from library.tools.project_layout import Area, ProjectLayout

def run_step(raw_footage_files: list, clip_catalog: list, output_dir: str):
    """
    Run object segmentation on each clip in the catalog.
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
            # Segment the clip
            seg_result = segmenter.segment_clip(clip_path, sample_fps=2.0)
            
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
                "object_count": len(seg_result.objects)
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
        
    # Where masks land is the layout owner's call, not this step's.
    # See library/tools/project_layout.py.
    project_folder = input_data.get("project_folder") or os.getcwd()
    output_dir = str(ProjectLayout(project_folder).write_dir(
        Area.SEGMENTATION, step="object_segmentation"))
    
    result = run_step(raw_footage_files, clip_catalog, output_dir)
    json.dump(result, sys.stdout, indent=2)

if __name__ == "__main__":
    main()
