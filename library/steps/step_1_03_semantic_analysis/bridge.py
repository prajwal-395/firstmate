"""
Step 1.03 Bridge: Semantic Analysis
Runs the vision pipeline on new raw footage clips to generate semantic analysis documents.
"""
import json, sys, subprocess, os, glob

def _require_keys(obj, keys, context):
    missing = [k for k in keys if k not in obj]
    if missing:
        raise ValueError(f"{context}: missing required keys: {missing}")

def main():
    data = json.loads(sys.stdin.read())
    
    if not isinstance(data, dict):
        raise ValueError("Input data must be a dictionary")
    
    # Derive raw_dir from DAG-provided data
    raw_footage_files = data.get('raw_footage_files', [])
    if raw_footage_files:
        # raw_footage_files can be dicts (from scan step) or strings
        first = raw_footage_files[0]
        first_path = first['path'] if isinstance(first, dict) else first
        raw_dir = os.path.dirname(first_path)
    elif 'raw_dir' in data:
        raw_dir = data['raw_dir']
    else:
        raw_dir = os.path.join(data.get('project_folder', '.'), 'raw')
    
    # Analysis outputs go alongside the raw dir (raw/analysis/)
    analysis_dir = os.path.join(raw_dir, 'analysis')
    os.makedirs(analysis_dir, exist_ok=True)
    
    # Path to the vision pipeline tool (repo-relative)
    PILOT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    VISION_PIPELINE = os.path.join(PILOT_ROOT, 'library', 'tools', 'analysis', 'vision_pipeline_v3.py')
    
    if not os.path.exists(VISION_PIPELINE):
        print(f"  ✗ Vision pipeline not found at {VISION_PIPELINE}", file=sys.stderr)
        print(f"    Expected: library/tools/analysis/vision_pipeline_v3.py", file=sys.stderr)
        sys.exit(1)
    
    # Check which clips already have profiles
    existing_profiles = set()
    for f in glob.glob(os.path.join(analysis_dir, 'clip_profile_*.json')):
        # Extract clip name: clip_profile_IMG_1806.json → IMG_1806
        basename = os.path.basename(f)
        clip_name = basename.replace('clip_profile_', '').replace('.json', '')
        if '_video_only' not in clip_name:
            existing_profiles.add(clip_name)
    
    # Find clips that need analysis
    all_clips = []
    missing_clips = []
    # C8 fix: Use state raw_footage_files instead of hardcoded glob to support all video formats
    # and to preserve clip_id assigned in step_1_01 for pipeline synchronization.
    for file_info in raw_footage_files:
        if isinstance(file_info, dict):
            fpath = file_info["path"]
            clip_id = file_info.get("clip_id", "")
        else:
            fpath = file_info
            clip_id = ""
            
        clip_name = os.path.splitext(os.path.basename(fpath))[0]
        all_clips.append(fpath)
        if clip_name not in existing_profiles:
            missing_clips.append(fpath)
    
    print(f"Semantic Analysis: {len(all_clips)} total clips, {len(existing_profiles)} already analyzed, {len(missing_clips)} remaining", file=sys.stderr)
    
    # Only run vision pipeline on missing clips
    if missing_clips:
        print(f"Running vision pipeline on {len(missing_clips)} new clips...", file=sys.stderr)
        
        # Run on each missing clip individually
        for clip_path in missing_clips:
            print(f"  Analyzing: {os.path.basename(clip_path)}", file=sys.stderr)
            try:
                subprocess.run(
                    ['python3', VISION_PIPELINE, '--clip', clip_path, '--output-dir', analysis_dir],
                    check=True,
                    timeout=600,  # 10 min max per clip
                )
            except subprocess.TimeoutExpired:
                print(f"  ⚠ Timeout on {os.path.basename(clip_path)}, skipping", file=sys.stderr)
            except subprocess.CalledProcessError as e:
                print(f"  ⚠ Error on {os.path.basename(clip_path)}: {e}", file=sys.stderr)
    else:
        print("All clips already have vision profiles, skipping analysis.", file=sys.stderr)
    
    # Collect ALL clip profiles (existing + new)
    profiles = []
    
    # Create lookup map for clip_id from raw_footage_files
    clip_id_map = {}
    for file_info in raw_footage_files:
        if isinstance(file_info, dict) and "path" in file_info and "clip_id" in file_info:
            clip_name = os.path.splitext(os.path.basename(file_info["path"]))[0]
            clip_id_map[clip_name] = file_info["clip_id"]

    for f in sorted(glob.glob(os.path.join(analysis_dir, 'clip_profile_*.json'))):
        if '_video_only' in f:
            continue  # Skip partial profiles
        with open(f) as fp:
            try:
                profile_data = json.load(fp)
                # Inject clip_id for downstream synchronization
                clip_name = os.path.basename(f).replace('clip_profile_', '').replace('.json', '')
                if clip_name in clip_id_map:
                    profile_data["clip_id"] = clip_id_map[clip_name]
                profiles.append(profile_data)
            except json.JSONDecodeError:
                print(f"  ⚠ Invalid JSON in {f}, skipping", file=sys.stderr)
    
    print(f"Collected {len(profiles)} clip profiles", file=sys.stderr)
    
    json.dump({
        'semantic_analysis_documents': profiles,
        'total_clips_analyzed': len(profiles)
    }, sys.stdout, indent=2)

if __name__ == '__main__':
    main()
