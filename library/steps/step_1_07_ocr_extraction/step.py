"""
Step 1.07: OCR Extraction
Extracts on-screen text from raw footage using EasyOCR.

The work is `extract_ocr`, which takes its inputs as arguments and
returns the result.  `main()` owns the process: stdin, stdout, and
nothing else.  See AGENTS.md 3.
"""
import json
import sys
import os
import glob
from pathlib import Path
from library.tools.analysis.ocr_extractor import OCRExtractor
from library.tools.project_layout import Area, ProjectLayout

def extract_ocr(raw_footage_files: list, project_folder: str = "",
                temporal_index=None) -> dict:
    """Extract on-screen text for each clip, reusing anything already on disk.

    Returns `{"ocr_extraction": {clip_id: result}}`.  A clip whose
    extraction raises is reported on stderr and left out of the result
    rather than failing the others.
    """
    # OCR results are output, so they land in the output tree rather than
    # in raw/analysis/ocr/ inside the captain's footage directory.
    # See library/tools/project_layout.py.
    layout = ProjectLayout(project_folder or os.getcwd())
    ocr_dir = str(layout.write_dir(Area.OCR, step="ocr_extraction"))

    temporal_dir = ""
    if isinstance(temporal_index, dict):
        temporal_dir = temporal_index.get("index_dir", "")
    elif isinstance(temporal_index, list) and len(temporal_index) > 0:
        first = temporal_index[0]
        if isinstance(first, dict) and "index_path" in first:
            temporal_dir = os.path.dirname(first["index_path"])
            
    if not temporal_dir:
        temporal_dir = str(layout.read_dir(Area.TEMPORAL_INDEX))
    
    extractor = OCRExtractor()
    
    all_clips = []
    clip_ids = {}
    for file_info in raw_footage_files:
        if isinstance(file_info, dict):
            fpath = file_info["path"]
            cid = file_info.get("clip_id", "")
        else:
            fpath = file_info
            cid = ""
        all_clips.append(fpath)
        clip_ids[fpath] = cid
        
    ocr_results = {}
    
    for clip_path in all_clips:
        clip_name = os.path.splitext(os.path.basename(clip_path))[0]
        clip_id = clip_ids.get(clip_path) or clip_name
        
        out_subdir = os.path.join(ocr_dir, clip_id)
        out_file = os.path.join(out_subdir, "ocr_result.json")
        
        if os.path.exists(out_file):
            print(f"Skipping {clip_id}, OCR already extracted.", file=sys.stderr)
            with open(out_file) as fp:
                ocr_results[clip_id] = json.load(fp)
            continue
            
        print(f"Extracting OCR for {clip_id}...", file=sys.stderr)
        
        scene_boundaries = None
        temporal_idx = os.path.join(temporal_dir, f"{clip_id}.json")
        if os.path.exists(temporal_idx):
            try:
                with open(temporal_idx) as f:
                    t_data = json.load(f)
                    if 'scene_boundaries' in t_data:
                        scene_boundaries = [
                            sb.get('time', 0.0)
                            for sb in t_data['scene_boundaries']
                        ]
            except Exception as e:
                print(f"Failed to read temporal index for {clip_id}: {e}", file=sys.stderr)
                
        try:
            result = extractor.extract_text_from_clip(clip_path, sample_fps=1.0, scene_boundaries=scene_boundaries)
            result.save(out_subdir)
            
            with open(out_file) as fp:
                ocr_results[clip_id] = json.load(fp)
                
        except Exception as e:
            print(f"⚠ Error extracting OCR for {clip_id}: {e}", file=sys.stderr)
            
    return {'ocr_extraction': ocr_results}


def main():
    data = json.loads(sys.stdin.read())
    result = extract_ocr(
        raw_footage_files=data.get('raw_footage_files', []),
        project_folder=data.get('project_folder', ''),
        temporal_index=data.get('temporal_index', {}),
    )
    json.dump(result, sys.stdout, indent=2)


if __name__ == '__main__':
    main()
