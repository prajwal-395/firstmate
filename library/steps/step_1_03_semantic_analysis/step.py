"""
Step 1.03 Bridge: Semantic Analysis
Runs the vision pipeline on new raw footage clips to generate semantic analysis documents.
"""
import json, sys, subprocess, os, glob

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))))
from library.tools.vision_schema_adapter import adapt_semantic_document, is_v3_profile
from library.tools.project_layout import Area, ProjectLayout

# Per-clip ceiling for the vision analyser. It was 600s, which is under
# what a long clip needs: measured on this machine, a 3.6s clip costs 86s
# (five model calls, most of it fixed overhead) and the per-window passes
# scale with duration, so project 001's 188s clip is well past ten
# minutes. A clip that overruns is SKIPPED, not fatal, so a tight ceiling
# silently drops the longest - and therefore usually the most important -
# footage from the analysis. This one exists to break a wedge.
CLIP_ANALYSIS_TIMEOUT_S = int(os.environ.get("PIPELINE_CLIP_ANALYSIS_TIMEOUT_S", 3600))


# Profiles are keyed by the media file's STEM, and that is deliberate.
#
# `vision_pipeline_v3` writes `clip_profile_<stem>_v3.json` and skips a
# clip whose file is already there, so the stem is the analyser's own
# cache key. `semantic_index.build_semantic_lookup` joins those documents
# to the catalog's synthetic `clip_XXX` ids by file path, so nothing
# downstream needs the file renamed.
#
# A `_rename_profile` helper used to rename each fresh profile from the
# stem to the catalog clip_id. It never once fired against v3 output: it
# looked for `clip_profile_<stem>.json` and `clip_profile_<stem>_video_only.json`
# while the analyser writes `clip_profile_<stem>_v3.json`. Meanwhile the
# "already analysed?" check below compared catalog clip_ids (`clip_001`)
# against the stems on disk (`IMG_1806_v3`) and therefore never matched,
# so EVERY clip was re-analysed on EVERY run - 45 to 90 minutes of vision
# on project 001, thrown away and redone each time. Had the rename ever
# worked it would have broken the analyser's own cache instead. Removed
# rather than repaired: the stem is the key, in both places.


# Collision-avoidance suffix appended by _unique() in project_migration.py.
# These are byte-identical duplicates and must be filtered out wherever the
# step globs for profiles, or the output lists phantom clips.
_COLLISION_SUFFIX_RE = __import__('re').compile(r'__\d+$')


def _is_collision_duplicate(filename):
    """True when *filename* was created by the collision-avoiding writer.

    The migration tool appends ``__2``, ``__3``, ... to avoid overwriting.
    These are byte-identical copies of the originals and should never be
    treated as distinct profiles.
    """
    stem = os.path.splitext(filename)[0]
    # strip the `clip_profile_` prefix and the optional `_v3` suffix
    name = stem
    if name.startswith('clip_profile_'):
        name = name[len('clip_profile_'):]
    if name.endswith('_v3'):
        name = name[:-len('_v3')]
    return bool(_COLLISION_SUFFIX_RE.search(name))


def _profile_stems(analysis_dir):
    """File stems that already have a profile in `analysis_dir`.

    Tolerates the two suffixes the analyser has used (`_v3`, and none) and
    ignores the partial `_video_only` documents and collision-avoidance
    duplicates (``__2``, ``__3``, ...).
    """
    stems = set()
    for path in glob.glob(os.path.join(analysis_dir, 'clip_profile_*.json')):
        basename = os.path.basename(path)
        if _is_collision_duplicate(basename):
            continue
        name = basename[len('clip_profile_'):-len('.json')]
        if not name or name.endswith('_video_only'):
            continue
        if name.endswith('_v3'):
            name = name[:-len('_v3')]
        stems.add(name)
    return stems


def _require_keys(obj, keys, context):
    missing = [k for k in keys if k not in obj]
    if missing:
        raise ValueError(f"{context}: missing required keys: {missing}")

def main():
    data = json.loads(sys.stdin.read())
    
    if not isinstance(data, dict):
        raise ValueError("Input data must be a dictionary")
    
    raw_footage_files = data.get('raw_footage_files', [])

    # Profiles are pipeline OUTPUT and land in the output tree.
    #
    # They used to be written to raw/analysis/, inside the captain's own
    # footage directory - so a step's product sat among the source
    # material it was derived from, and `raw/` was not read-only in
    # practice. The layout owner has no writable area under raw/, which
    # is what makes that unrepeatable rather than merely fixed.
    # See library/tools/project_layout.py.
    layout = ProjectLayout(data.get('project_folder') or os.getcwd())
    analysis_dir = str(layout.write_dir(Area.VISION_ANALYSIS, step="semantic_analysis"))
    
    # Path to the vision pipeline tool (repo-relative)
    PILOT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    VISION_PIPELINE = os.path.join(PILOT_ROOT, 'library', 'tools', 'analysis', 'vision_pipeline_v3.py')
    
    if not os.path.exists(VISION_PIPELINE):
        print(f"  ✗ Vision pipeline not found at {VISION_PIPELINE}", file=sys.stderr)
        print(f"    Expected: library/tools/analysis/vision_pipeline_v3.py", file=sys.stderr)
        sys.exit(1)
    
    # Which clips already have a profile, keyed by file stem - the same key
    # the analyser caches on. See the note above _profile_stems.
    existing_profiles = _profile_stems(analysis_dir)

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
        stem = os.path.splitext(os.path.basename(fpath))[0]
        if stem not in existing_profiles:
            missing_clips.append({"path": fpath, "clip_id": clip_id})

    print(f"Semantic Analysis: {len(all_clips)} total clips, {len(existing_profiles)} already analyzed, {len(missing_clips)} remaining", file=sys.stderr)
    
    # Only run vision pipeline on missing clips
    if missing_clips:
        print(f"Running vision pipeline on {len(missing_clips)} new clips...", file=sys.stderr)
        
        # Run on each missing clip individually. Each writes its own profile
        # before the next starts, so an interrupted run resumes where it
        # stopped instead of starting over.
        for n, clip in enumerate(missing_clips, 1):
            clip_path = clip["path"]
            print(f"  [{n}/{len(missing_clips)}] Analyzing: "
                  f"{os.path.basename(clip_path)}", file=sys.stderr)
            try:
                subprocess.run(
                    [sys.executable, VISION_PIPELINE, '--clip', clip_path, '--output-dir', analysis_dir],
                    check=True,
                    timeout=CLIP_ANALYSIS_TIMEOUT_S,
                )
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
        if _is_collision_duplicate(os.path.basename(f)):
            continue  # Skip __N collision-avoidance duplicates
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
