#!/usr/bin/env python3
import sys
import os
import json
import argparse
import shutil
import tempfile

sys.path.append("/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules")
os.environ["RESOLVE_SCRIPT_API"] = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
os.environ["RESOLVE_SCRIPT_LIB"] = "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so"
import DaVinciResolveScript as dvr

# This module runs both as a script (launched by resolve_build_timeline in
# its own process) and as `library.tools.execution.apply_fusion_comps`, so
# put library/tools on the path rather than assume either entry point.
_TOOLS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)
from transition_vocabulary import canonical_type, is_cut, withdrawal_reason

from fusion.comp_builder import ZOOM_KEYS, build_effect_comp, normalize_effects


def apply_fusion_comps(manifest, project_folder):
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
    # fusion_effects.transitions is the list compile_manifest indexes
    # against the V1 clips and validates. The top-level manifest
    # 'transitions' key is the planner's record and carries no clip index
    # at all, so reading it put every transition on clip 0.
    transition_specs = fusion_effects.get('transitions', [])

    transition_by_clip = {}
    for tspec in transition_specs:
        ttype = canonical_type(tspec.get('type', tspec.get('transition_type')))
        if ttype is None:
            raise ValueError(
                f"Transition {tspec!r} names a type the renderer cannot "
                f"draw: {withdrawal_reason(tspec.get('type', tspec.get('transition_type')))}"
            )
        if is_cut(ttype):
            continue

        if 'after_clip' not in tspec:
            raise ValueError(
                f"Transition {tspec!r} carries no after_clip index. It used "
                f"to default to 0, which stacked every transition on the "
                f"first clip of the timeline."
            )
        after_idx = tspec['after_clip']

        dur_f = tspec.get('duration_frames', 12)
        transition_by_clip.setdefault(after_idx, {})
        transition_by_clip[after_idx]['tail_transition'] = ttype
        transition_by_clip[after_idx]['tail_transition_frames'] = dur_f
        next_idx = after_idx + 1
        transition_by_clip.setdefault(next_idx, {})
        transition_by_clip[next_idx]['head_transition'] = ttype
        transition_by_clip[next_idx]['head_transition_frames'] = dur_f

    has_any_effects = per_clip_effects or transition_by_clip
    comp_dir = None

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
            from builtin_effect_loader import list_builtin_effects, import_effect_to_clip, import_customized_effect
        except ImportError:
            list_builtin_effects = None
            import_effect_to_clip = None
            import_customized_effect = None

        try:
            # Also fusion_comp_generator is in library/steps/step_6_01_render
            sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'steps', 'step_6_01_render'))
            from fusion_comp_generator import write_comp, SEGMENT_PRESETS
            from custom_asset_bank import (
                clip_asset_key, import_custom_asset, save_custom_asset,
                get_custom_asset,
            )
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

            preset_name = effects.get('_preset', None)
            
            # Exact match only. The substring fallback that used to sit
            # here could turn one effect name into an unrelated built-in -
            # the handoff tells the planner to use the exact snake_case
            # name, so a near miss is a mistake to surface, not to guess at.
            builtin_effect = None
            if preset_name and list_builtin_effects:
                if preset_name in (list_builtin_effects() or {}):
                    builtin_effect = preset_name


            if builtin_effect:
                # Remove _preset since we handled it
                effects.pop('_preset', None)
                tl_clip = v1_items[item_idx]
                for cn in (tl_clip.GetFusionCompNameList() or []):
                    tl_clip.DeleteFusionCompByName(cn)
                
                if effects and import_customized_effect:
                    import_customized_effect(tl_clip, builtin_effect, effects)
                else:
                    import_effect_to_clip(tl_clip, builtin_effect)
                    
                comp_names = tl_clip.GetFusionCompNameList()
                if comp_names and len(comp_names) > 0:
                    print(f"  ✓ [{orig_ci}] {label}: Imported built-in effect {builtin_effect}", file=sys.stderr)
                else:
                    print(f"  ✗ [{orig_ci}] {label}: Import built-in effect {builtin_effect} failed", file=sys.stderr)
                continue

            preset_name = effects.pop('_preset', None)
            tl_clip = v1_items[item_idx]

            # 1. Check built-in effects
            if preset_name and preset_name in SEGMENT_PRESETS:
                base = dict(SEGMENT_PRESETS[preset_name])
                base.update({k: v for k, v in effects.items() if k != '_preset'})
                effects = base

            trans_params = transition_by_clip.get(orig_ci, {})
            if trans_params:
                effects.update(trans_params)

            if not effects:
                continue

            mpi = tl_clip.GetMediaPoolItem()
            if not mpi:
                continue
            frames_prop = mpi.GetClipProperty('Frames')
            clip_dur = int(frames_prop) if frames_prop else tl_clip.GetDuration()

            has_zoom = any(k in effects for k in ZOOM_KEYS)
            normalize_effects(effects, has_zoom)

            # 2. Check custom asset bank.
            # A generated comp bakes the clip's own frame count into its
            # keyframes (AGENTS.md section 5), so the key must cover
            # everything the comp is built from - not the preset name
            # (which replays one clip's timing on every clip sharing it)
            # and not the positional label alone (which is stable across
            # runs, so a re-cut of the same block position would replay
            # the previous run's duration). Same key means same bytes.
            asset_key = clip_asset_key(label, effects, clip_dur)
            custom_asset = get_custom_asset(project_folder, asset_key)
            if custom_asset:
                for cn in (tl_clip.GetFusionCompNameList() or []):
                    tl_clip.DeleteFusionCompByName(cn)

                tl_clip.ImportFusionComp(custom_asset)
                print(f"  ✓ [{orig_ci}] {label}: Imported custom asset {asset_key}", file=sys.stderr)
                continue

            # 3. Generate custom .comp via composable engine
            comp_content = build_effect_comp(effects, clip_dur)

            save_custom_asset(project_folder, asset_key, comp_content)
            
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

    for vfx in vfx_entries:
        if vfx.get('type', '') == 'zoom_pulse':
            vfx_start_f = round(vfx.get('timeline_start', 0) * fps)
            for item in v1_items:
                if item.GetStart() <= vfx_start_f < item.GetEnd():
                    item.SetProperty("ZoomX", 1.05)
                    item.SetProperty("ZoomY", 1.05)
                    print(f"  ✓ zoom_pulse on {item.GetName()} at {vfx_start_f}f", file=sys.stderr)
                    break

    if comp_dir:
        shutil.rmtree(comp_dir, ignore_errors=True)
    return True

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest")
    parser.add_argument("--project-folder", default="",
                        help="Project directory - where the custom asset "
                             "bank lives. Without it the bank was keyed to "
                             "the temp directory the manifest was written to")
    args = parser.parse_args()

    with open(args.manifest) as f:
        manifest = json.load(f)

    project_folder = args.project_folder or os.path.abspath(
        os.path.dirname(args.manifest))
    success = apply_fusion_comps(manifest, project_folder)
    sys.exit(0 if success else 1)
