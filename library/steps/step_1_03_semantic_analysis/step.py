"""
Step 1.03 Bridge: Semantic Analysis
Runs the vision pipeline on new raw footage clips to generate semantic analysis documents.
"""
import json, sys, subprocess, os, glob

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))))
from library.tools.vision_schema_adapter import adapt_semantic_document, is_v3_profile

def _rename_profile(analysis_dir, clip_basename, clip_id, suffix):
    """Rename a freshly written profile from file stem to catalog clip_id.

    `os.rename` overwrites its destination.  A catalog entry with no
    clip_id used to produce `clip_profile_.json` and silently destroy
    whatever already sat there, and any clip_id collision would do the
    same to another clip's analysis.  Neither is worth a re-analysis, so
    both refuse instead.
    """
    if not clip_id:
        return
    old = os.path.join(analysis_dir, f"clip_profile_{clip_basename}{suffix}.json")
    new = os.path.join(analysis_dir, f"clip_profile_{clip_id}{suffix}.json")
    if not os.path.exists(old) or old == new:
        return
    if os.path.exists(new):
        print(f"  ⚠ Not renaming {os.path.basename(old)} to "
              f"{os.path.basename(new)}: that profile already exists",
              file=sys.stderr)
        return
    os.rename(old, new)


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
    if 'raw_dir' in data:
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
    for file_info in raw_footage_files:
        if isinstance(file_info, dict):
            fpath = file_info["path"]
            clip_id = file_info.get("clip_id", "")
        else:
            fpath = file_info
            clip_id = os.path.splitext(os.path.basename(fpath))[0]
            
        all_clips.append(fpath)
        if clip_id not in existing_profiles:
            missing_clips.append({"path": fpath, "clip_id": clip_id})
    
    print(f"Semantic Analysis: {len(all_clips)} total clips, {len(existing_profiles)} already analyzed, {len(missing_clips)} remaining", file=sys.stderr)
    
    # Only run vision pipeline on missing clips
    if missing_clips:
        print(f"Running vision pipeline on {len(missing_clips)} new clips...", file=sys.stderr)
        
        # Run on each missing clip individually
        for clip in missing_clips:
            clip_path = clip["path"]
            clip_id = clip["clip_id"]
            clip_basename = os.path.splitext(os.path.basename(clip_path))[0]
            print(f"  Analyzing: {os.path.basename(clip_path)}", file=sys.stderr)
            try:
                subprocess.run(
                    ['python3', VISION_PIPELINE, '--clip', clip_path, '--output-dir', analysis_dir],
                    check=True,
                    timeout=600,  # 10 min max per clip
                )
                # Rename the output to use clip_id instead of clip_basename to prevent collisions
                _rename_profile(analysis_dir, clip_basename, clip_id, "")
                _rename_profile(analysis_dir, clip_basename, clip_id, "_video_only")


            except subprocess.TimeoutExpired:
                print(f"  ⚠ Timeout on {os.path.basename(clip_path)}, skipping", file=sys.stderr)
            except subprocess.CalledProcessError as e:
                print(f"  ⚠ Error on {os.path.basename(clip_path)}: {e}", file=sys.stderr)
    else:
        print("All clips already have vision profiles, skipping analysis.", file=sys.stderr)
    
    # Collect ALL clip profiles (existing + new).
    #
    # The v3 analyser emits scene[]/camera[]/actions[]/objects[]/assessment{}
    # while every consumer addresses the retired analysis.*/blocks shape.
    # Passing profiles through verbatim, as this step used to, meant the
    # richest observation of the footage reached nobody - and a re-run of
    # the analysis failed step 3.02 outright, because no clip yielded a
    # description. The adapter derives the consumer-facing view alongside
    # the v3 fields; legacy profiles pass through unchanged.
    profiles = []
    adapted_count = 0

    for f in sorted(glob.glob(os.path.join(analysis_dir, 'clip_profile_*.json'))):
        if '_video_only' in f:
            continue  # Skip partial profiles
        with open(f) as fp:
            try:
                profile_data = json.load(fp)
                if is_v3_profile(profile_data):
                    adapted_count += 1
                profile_data = adapt_semantic_document(profile_data)
                # Inject clip_id for downstream synchronization
                clip_id = os.path.basename(f).replace('clip_profile_', '').replace('.json', '')
                profile_data["clip_id"] = clip_id
                profiles.append(profile_data)
            except json.JSONDecodeError:
                print(f"  ⚠ Invalid JSON in {f}, skipping", file=sys.stderr)

    print(f"Collected {len(profiles)} clip profiles "
          f"({adapted_count} adapted from the v3 vision schema)", file=sys.stderr)
    
    json.dump({
        'semantic_analysis_documents': profiles,
        'total_clips_analyzed': len(profiles)
    }, sys.stdout, indent=2)

if __name__ == '__main__':
    main()
