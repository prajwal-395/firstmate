#!/bin/bash
# Usage: ./scripts/run_phase5_6.sh /path/to/project/001 [--apply-to-resolve]
#
# Runs Phase 5 (color grade, audio mix, creative cohesion, compile manifest)
# and Phase 6 (resolve build, validate output) in sequence.
# Requires: DaVinci Resolve open with the project loaded.

set -e

PROJECT_DIR="$1"
APPLY_TO_RESOLVE=0

if [ "$2" == "--apply-to-resolve" ]; then
    APPLY_TO_RESOLVE=1
fi

if [ -z "$PROJECT_DIR" ]; then
    echo "Usage: $0 <project_dir> [--apply-to-resolve]"
    exit 1
fi

if [ ! -d "$PROJECT_DIR" ]; then
    echo "Error: Project directory $PROJECT_DIR not found"
    exit 1
fi

# Find the latest step outputs directory
LATEST_RUN=$(ls -d "$PROJECT_DIR"/run_* 2>/dev/null | sort -V | tail -n1)
if [ -z "$LATEST_RUN" ]; then
    echo "Error: No run_XXX directory found in $PROJECT_DIR"
    exit 1
fi

echo "Using latest run folder: $LATEST_RUN"

# Create new run directory
RUN_NUM=$(basename "$LATEST_RUN" | sed 's/run_//')
NEXT_RUN_NUM=$(printf "%03d" $((10#$RUN_NUM + 1)))
NEW_RUN="$PROJECT_DIR/run_$NEXT_RUN_NUM"
mkdir -p "$NEW_RUN"
echo "Writing outputs to new run folder: $NEW_RUN"

# Copy all previous step JSONs to the new run folder so it has a complete state
cp "$LATEST_RUN"/*.json "$NEW_RUN"/ 2>/dev/null || true

run_step() {
    local step_name=$1
    local script_path="library/steps/$step_name/step.py"
    echo "--------------------------------------------------"
    echo "Running $step_name..."
    
    local input_json="$NEW_RUN/${step_name}_input.tmp.json"
    python3 -c "
import sys, json, glob
state = {'project_folder': sys.argv[1]}
for f in glob.glob(sys.argv[2] + '/step_*.json'):
    try:
        data = json.load(open(f))
        state.update(data)
    except: pass
try:
    man = json.load(open(sys.argv[2] + '/assembly_manifest.json'))
    state.update(man)
except: pass
json.dump(state, open(sys.argv[3], 'w'))
" "$PROJECT_DIR" "$NEW_RUN" "$input_json"

    local output_json="$NEW_RUN/$step_name.json"
    
    if python3 "$script_path" < "$input_json" > "$output_json"; then
        echo "[PASS] $step_name completed."
    else
        echo "[FAIL] $step_name failed."
        exit 1
    fi
    rm -f "$input_json"
}

run_step "step_5_01_color_grade"
python3 -c "import json, sys; d=json.load(open(sys.argv[1])); spec=d.get('color_grade_spec',{}); cdl=spec.get('cdl',{}); print('CDL offset:', cdl.get('offset',[]))" "$NEW_RUN/step_5_01_color_grade.json" || true
mv "$NEW_RUN/step_5_01_color_grade.json" "$NEW_RUN/step_5_01.json"

run_step "step_5_02_audio_mix"
python3 -c "import json, sys; d=json.load(open(sys.argv[1])); spec=d.get('audio_mix_spec',{}); print('LUFS Target:', spec.get('target_lufs', 'N/A'))" "$NEW_RUN/step_5_02_audio_mix.json" || true
mv "$NEW_RUN/step_5_02_audio_mix.json" "$NEW_RUN/step_5_02.json"

run_step "step_5_03_creative_cohesion"
python3 -c "import json, sys; d=json.load(open(sys.argv[1])); rev=d.get('cohesion_review',{}); print('Cohesion Score:', rev.get('cohesion_score', 'N/A'))" "$NEW_RUN/step_5_03_creative_cohesion.json" || true
mv "$NEW_RUN/step_5_03_creative_cohesion.json" "$NEW_RUN/step_5_03.json"

run_step "step_5_04_compile_manifest"
python3 -c "import json, sys; d=json.load(open(sys.argv[1])); man=d.get('assembly_manifest',{}); print('Tracks:', list(man.get('tracks',{}).keys()) if 'tracks' in man else man.get('video_tracks',[]))" "$NEW_RUN/step_5_04_compile_manifest.json" || true
mv "$NEW_RUN/step_5_04_compile_manifest.json" "$NEW_RUN/assembly_manifest.json"

if [ "$APPLY_TO_RESOLVE" -eq 1 ]; then
    run_step "step_6_01_render"
    mv "$NEW_RUN/step_6_01_render.json" "$NEW_RUN/step_6_01.json"
else
    echo "--------------------------------------------------"
    echo "Skipping step_6_01 (Resolve build) - run with --apply-to-resolve to execute."
fi

echo "--------------------------------------------------"
echo "Running step_6_02_validate_output..."
cat > "$NEW_RUN/step_6_02_input.tmp.json" <<EOF
{
    "project_folder": "$PROJECT_DIR",
    "rendered_output": {
        "output_path": "$PROJECT_DIR/fully loaded demo v0.mov"
    }
}
EOF

python3 -c "
import json, sys
data = json.load(open(sys.argv[1]))
try:
    man = json.load(open(sys.argv[2]))
    if 'assembly_manifest' in man:
        data['assembly_manifest'] = man['assembly_manifest']
    else:
        data['assembly_manifest'] = man
except: pass
json.dump(data, open(sys.argv[1], 'w'))
" "$NEW_RUN/step_6_02_input.tmp.json" "$NEW_RUN/assembly_manifest.json"

if python3 "library/steps/step_6_02_validate_output/step.py" < "$NEW_RUN/step_6_02_input.tmp.json" > "$NEW_RUN/step_6_02.json"; then
    echo "[PASS] step_6_02_validate_output completed."
    python3 -c "import json, sys; d=json.load(open(sys.argv[1])); res=d.get('validation_result',{}); print('Validation Status:', res.get('status', 'N/A'))" "$NEW_RUN/step_6_02.json" || true
else
    echo "[FAIL] step_6_02_validate_output failed."
fi
rm -f "$NEW_RUN/step_6_02_input.tmp.json"

echo "--------------------------------------------------"
echo "Phase 5+6 execution complete. Outputs saved in $NEW_RUN/"
