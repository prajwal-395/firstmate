#!/usr/bin/env python3
import sys
import os
import json
import argparse
import shutil

sys.path.append("/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules")
os.environ["RESOLVE_SCRIPT_API"] = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
os.environ["RESOLVE_SCRIPT_LIB"] = "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so"
import DaVinciResolveScript as dvr

def apply_fusion_comps(manifest):
    resolve = dvr.scriptapp("Resolve")
    if not resolve:
        print("ERROR: Could not connect to Resolve.", file=sys.stderr)
        return False
        
    pm = resolve.GetProjectManager()
    project = pm.GetCurrentProject()
    if not project:
        print("ERROR: No project open.", file=sys.stderr)
        return False
        
    timeline = project.GetCurrentTimeline()
    if not timeline:
        print("ERROR: No timeline open.", file=sys.stderr)
        return False
        
    fps = float(timeline.GetSetting("timelineFrameRate") or 30)
    
    # Re-build v1_clips from manifest
    tracks = manifest.get('tracks', {})
    v1_clips = tracks.get('V1', {}).get('clips', [])
    
    fusion_effects = manifest.get('fusion_effects', {})
    per_clip_effects = fusion_effects.get('per_clip', {})
    transition_specs = fusion_effects.get('transitions', [])

    transition_by_clip = {}
    macro_transitions_by_clip = {}
    for tspec in transition_specs:
        ttype = tspec.get('type', 'cut')
        if ttype in ('cut', 'hard_cut', '', None):
            continue
            
        after_idx = tspec.get('after_clip', 0)
        
        if ttype == 'macro':
            macro_transitions_by_clip[after_idx] = tspec
            continue
            
        dur_f = tspec.get('duration_frames', 12)
        transition_by_clip.setdefault(after_idx, {})
        transition_by_clip[after_idx]['tail_transition'] = ttype
        transition_by_clip[after_idx]['tail_transition_frames'] = dur_f
        next_idx = after_idx + 1
        transition_by_clip.setdefault(next_idx, {})
        transition_by_clip[next_idx]['head_transition'] = ttype
        transition_by_clip[next_idx]['head_transition_frames'] = dur_f

    has_any_effects = per_clip_effects or transition_by_clip or macro_transitions_by_clip

    # Map legacy vfx_entries
    v1_items = timeline.GetItemListInTrack("video", 1) or []
    
    # Build mapping from original v1_clips index to actual v1_items index
    orig_to_item = {}
    item_idx = 0
    for orig_ci, clip_spec in enumerate(v1_clips):
        if item_idx >= len(v1_items):
            break
        src = clip_spec.get('source_file', '')
        if not src:
            continue
        mpi = v1_items[item_idx].GetMediaPoolItem()
        mpi_path = mpi.GetClipProperty("File Path") if mpi else ""
        if mpi_path == src or os.path.basename(mpi_path) == os.path.basename(src):
            orig_to_item[orig_ci] = item_idx
            item_idx += 1
            
    vfx_entries = manifest.get('vfx', [])
    if vfx_entries and v1_items:
        for vfx in vfx_entries:
            start_sec = vfx.get('timeline_start', 0)
            start_f = round(start_sec * fps)
            params = vfx.get('params', {})
            preset = vfx.get('effect_type', vfx.get('preset', ''))
            
            for idx, clip in enumerate(v1_items):
                if clip.GetStart() <= start_f < clip.GetEnd():
                    orig_ci = next((k for k, v in orig_to_item.items() if v == idx), None)
                    if orig_ci is not None:
                        label = v1_clips[orig_ci].get('label', f'clip_{orig_ci}')
                        if label not in per_clip_effects:
                            per_clip_effects[label] = {}
                        if preset:
                            per_clip_effects[label]['_preset'] = preset
                        for k, v in params.items():
                            per_clip_effects[label][k] = v
                        has_any_effects = True
                    break

    if has_any_effects:
        print(f"\n── Fusion .comp: {len(per_clip_effects)} VFX, {len(transition_specs)} transitions ──", file=sys.stderr)
        
        try:
            # We are inside library/tools/execution, so we need to go up to library/tools
            sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))
            from fusion_macro_loader import apply_macro_to_transition
        except ImportError:
            apply_macro_to_transition = None

        try:
            # Also fusion_comp_generator is in library/steps/step_6_01_render
            sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'steps', 'step_6_01_render'))
            from fusion_comp_generator import generate_comp, write_comp, SEGMENT_PRESETS
        except ImportError as e:
            print(f"Failed to import fusion_comp_generator: {e}", file=sys.stderr)
            return False

        comp_dir = tempfile.mkdtemp(prefix='fusion_comps_')

        for orig_ci, clip_spec in enumerate(v1_clips):
            if orig_ci not in orig_to_item:
                continue
            item_idx = orig_to_item[orig_ci]
            label = clip_spec.get('label', f'clip_{orig_ci}')

            effects = per_clip_effects.get(label, {})
            if not effects and label in SEGMENT_PRESETS:
                effects = dict(SEGMENT_PRESETS[label])
            elif effects:
                effects = dict(effects)
            else:
                effects = {}

            preset_name = effects.pop('_preset', None)
            if preset_name and preset_name in SEGMENT_PRESETS:
                base = dict(SEGMENT_PRESETS[preset_name])
                base.update({k: v for k, v in effects.items() if k != '_preset'})
                effects = base

            trans_params = transition_by_clip.get(orig_ci, {})
            if trans_params:
                effects.update(trans_params)

            macro_trans = macro_transitions_by_clip.get(orig_ci, None)
            if not effects and not macro_trans:
                continue

            tl_clip = v1_items[item_idx]
            mpi = tl_clip.GetMediaPoolItem()
            if not mpi:
                continue
            frames_prop = mpi.GetClipProperty('Frames')
            clip_dur = int(frames_prop) if frames_prop else tl_clip.GetDuration()
                        
            macro_applied = False
            if macro_trans and apply_macro_to_transition:
                macro_data = macro_trans.get("macro_preset", {})
                duration_ms = macro_trans.get("duration_ms", 500)
                macro_applied = apply_macro_to_transition(tl_clip, macro_data, duration_ms)
                if macro_applied:
                    print(f"  ✓ [{orig_ci}] {label}: Applied Fusion Macro transition", file=sys.stderr)
                else:
                    print(f"  ⚠ [{orig_ci}] {label}: Macro transition failed, falling back to dissolve", file=sys.stderr)
                    effects["tail_transition"] = "fade_to_black"
                    effects["tail_transition_frames"] = 12

            if not effects:
                continue

            if 'zoom_start' not in effects:
                effects.setdefault('zoom_start', 1.0)
                effects.setdefault('zoom_mid', 1.0)
                effects.setdefault('zoom_end', 1.0)
                effects.setdefault('vignette', False)

            comp_content = generate_comp(clip_dur, **effects)
            comp_path = write_comp(os.path.join(comp_dir, f"{label.lower()}.comp"), comp_content)

            for cn in (tl_clip.GetFusionCompNameList() or []):
                tl_clip.DeleteFusionCompByName(cn)

            tl_clip.ImportFusionComp(comp_path)
            comp_names = tl_clip.GetFusionCompNameList()

            if comp_names and len(comp_names) > 0:
                comp = tl_clip.GetFusionCompByName(comp_names[0])
                if comp:
                    resolve.OpenPage("fusion")
                    dummy = comp.AddTool("Merge")
                    if dummy:
                        dummy.Delete()
                tools = comp.GetToolList() if comp else {}
                real_tools = [t for t in tools.values() if t.GetAttrs().get('TOOLS_RegID') not in ('MediaIn', 'MediaOut')]
                if len(real_tools) == 0:
                    print(f"  ✗ [{orig_ci}] {label}: empty comp (bad file)", file=sys.stderr)
                    continue
                parts = []
                xf = comp.FindTool("Transform1")
                if xf:
                    v0 = xf.GetInput("Size", 0)
                    vm = xf.GetInput("Size", clip_dur // 2)
                    if v0 and vm:
                        parts.append(f"zoom:{v0:.3f}→{vm:.3f}")
                tt = effects.get('tail_transition')
                ht = effects.get('head_transition')
                if tt: parts.append(f"tail={tt}")
                if ht: parts.append(f"head={ht}")
                detail = f" ({', '.join(parts)})" if parts else ""
                print(f"  ✓ [{orig_ci}] {label}: {len(real_tools)} tools{detail}", file=sys.stderr)
            else:
                print(f"  ✗ [{orig_ci}] {label}: ImportFusionComp failed", file=sys.stderr)

    if vfx_entries:
        for vfx in vfx_entries:
            vfx_type = vfx.get('type', '')
            if vfx_type == 'zoom_pulse':
                vfx_start_f = round(vfx.get('timeline_start', 0) * fps)
                for item in v1_items:
                    if item.GetStart() <= vfx_start_f < item.GetEnd():
                        item.SetProperty("ZoomX", 1.05)
                        item.SetProperty("ZoomY", 1.05)
                        print(f"  ✓ zoom_pulse on {item.GetName()} at {vfx_start_f}f", file=sys.stderr)
                        break
        shutil.rmtree(comp_dir, ignore_errors=True)
    return True

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest")
    args = parser.parse_args()
    
    with open(args.manifest) as f:
        manifest = json.load(f)
        
    success = apply_fusion_comps(manifest)
    sys.exit(0 if success else 1)
