"""
Step 1.07: OCR Extraction
Extracts on-screen text from raw footage using EasyOCR.
"""
import json
import sys
import os
import glob
from pathlib import Path
from library.tools.analysis.ocr_extractor import OCRExtractor

def main():
    data = json.loads(sys.stdin.read())
    
    raw_footage_files = data.get('raw_footage_files', [])
    if raw_footage_files:
        first = raw_footage_files[0]
        first_path = first['path'] if isinstance(first, dict) else first
        raw_dir = os.path.dirname(first_path)
    elif 'raw_dir' in data:
        raw_dir = data['raw_dir']
    else:
        raw_dir = os.path.join(data.get('project_folder', '.'), 'raw')
        
    analysis_dir = os.path.join(raw_dir, 'analysis')
    ocr_dir = os.path.join(analysis_dir, 'ocr')
    os.makedirs(ocr_dir, exist_ok=True)
    
    temporal_dir = os.path.join(analysis_dir, 'temporal_index')
    
    extractor = OCRExtractor()
    
    all_clips = []
    for ext in ['*.MOV', '*.MP4', '*.mp4']:
        all_clips.extend(glob.glob(os.path.join(raw_dir, ext)))
        
    all_clips = sorted(all_clips)
    
    ocr_results = {}
    
    for clip_path in all_clips:
        clip_name = os.path.splitext(os.path.basename(clip_path))[0]
        out_subdir = os.path.join(ocr_dir, clip_name)
        out_file = os.path.join(out_subdir, "ocr_result.json")
        
        if os.path.exists(out_file):
            print(f"Skipping {clip_name}, OCR already extracted.", file=sys.stderr)
            with open(out_file) as fp:
                ocr_results[clip_name] = json.load(fp)
            continue
            
        print(f"Extracting OCR for {clip_name}...", file=sys.stderr)
        
        scene_boundaries = None
        temporal_idx = os.path.join(temporal_dir, f"clip_{clip_name}.json")
        if os.path.exists(temporal_idx):
            try:
                with open(temporal_idx) as f:
                    t_data = json.load(f)
                    if 'scene_boundaries' in t_data:
                        scene_boundaries = [
                            sb.get('timestamp', sb.get('time', 0.0))
                            for sb in t_data['scene_boundaries']
                        ]
            except Exception as e:
                print(f"Failed to read temporal index for {clip_name}: {e}", file=sys.stderr)
                
        try:
            result = extractor.extract_text_from_clip(clip_path, sample_fps=1.0, scene_boundaries=scene_boundaries)
            result.save(out_subdir)
            
            with open(out_file) as fp:
                ocr_results[clip_name] = json.load(fp)
                
        except Exception as e:
            print(f"⚠ Error extracting OCR for {clip_name}: {e}", file=sys.stderr)
            
    json.dump({
        'ocr_extraction': ocr_results
    }, sys.stdout, indent=2)

if __name__ == '__main__':
    main()
