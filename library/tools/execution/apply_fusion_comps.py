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

ZOOM_KEYS = ('zoom_start', 'zoom_mid', 'zoom_end', 'pan_start', 'pan_end')


def _normalize_effects(effects, has_zoom):
    """Settle every default the comp generator would apply, in place.

    The bank key has to describe the comp that actually gets written, so
    every mutation of `effects` must happen before the key is derived -
    otherwise the lookup asks for a comp nobody ever banks.
    """
    if not has_zoom and 'vignette' not in effects:
        effects.setdefault('zoom_start', 1.0)
        effects.setdefault('zoom_mid', 1.0)
        effects.setdefault('zoom_end', 1.0)
        effects.setdefault('vignette', False)
    return effects


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
    transition_specs = manifest.get('transitions', [])

    transition_by_clip = {}
    macro_transitions_by_clip = {}
    for tspec in transition_specs:
        ttype = tspec.get('transition_type', tspec.get('type', 'cut'))
        if ttype in ('cut', 'hard_cut', '', None):
            continue
            
        after_idx = tspec.get('from_block', tspec.get('after_clip', 0))
        
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
            # We are inside library/tools/execution, so we need to go up to library/tools
            sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))
            from fusion_macro_loader import apply_macro_to_transition
            from builtin_effect_loader import list_builtin_effects, import_effect_to_clip, import_customized_effect
        except ImportError:
            apply_macro_to_transition = None
            list_builtin_effects = None
            import_effect_to_clip = None
            import_customized_effect = None

        try:
            # Also fusion_comp_generator is in library/steps/step_6_01_render
            sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'steps', 'step_6_01_render'))
            from fusion_comp_generator import write_comp, SEGMENT_PRESETS
            from fusion.engine import CompEngine
            from fusion.effects import fx
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
            
            builtin_effect = None
            if preset_name and list_builtin_effects:
                builtin_effects = list_builtin_effects()
                if preset_name in builtin_effects:
                    builtin_effect = preset_name
                else:
                    for b_name in builtin_effects:
                        if preset_name in b_name or b_name in preset_name:
                            builtin_effect = b_name
                            break
            
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

            macro_trans = macro_transitions_by_clip.get(orig_ci, None)
            if not effects and not macro_trans:
                continue

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

            has_zoom = any(k in effects for k in ZOOM_KEYS)
            _normalize_effects(effects, has_zoom)

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
            engine = CompEngine(clip_dur=clip_dur)

            if has_zoom:
                engine.add(fx.zoom(
                    clip_dur,
                    start=effects.get('zoom_start', 1.0),
                    mid=effects.get('zoom_mid', 1.0),
                    end=effects.get('zoom_end', 1.0),
                    pan_start=effects.get('pan_start'),
                    pan_end=effects.get('pan_end')
                ))

            if 'grade_gain' in effects or 'grade_contrast' in effects or 'grade_saturation' in effects:
                engine.add(fx.grade(
                    gain=effects.get('grade_gain', 1.0),
                    contrast=effects.get('grade_contrast', 0.0),
                    saturation=effects.get('grade_saturation', 1.0)
                ))

            if effects.get('glow_gain', 0.0) > 0:
                engine.add(fx.glow(
                    gain=effects.get('glow_gain', 0.0),
                    threshold=effects.get('glow_threshold', 0.75),
                    size=effects.get('glow_size', 3.5)
                ))

            if effects.get('film_grain'):
                engine.add(fx.grain(
                    power=effects.get('film_grain_power', 0.25),
                    size=effects.get('film_grain_size', 1.5)
                ))

            if effects.get('defocus'):
                engine.add(fx.defocus(size=effects.get('defocus_size', 2.0)))
                
            if 'shake_x' in effects or 'shake_y' in effects:
                engine.add(fx.shake(
                    clip_dur,
                    x_amount=effects.get('shake_x', 0.01),
                    y_amount=effects.get('shake_y', 0.01)
                ))
                
            if 'chromatic_aberration' in effects or 'chromatic_aberration_amount' in effects:
                engine.add(fx.chromatic_aberration(amount=effects.get('chromatic_aberration_amount', 0.01)))

            if 'lens_distortion' in effects or 'lens_distortion_amount' in effects:
                engine.add(fx.lens_distortion(distortion=effects.get('lens_distortion_amount', 0.1)))

            if effects.get('vignette', True):
                engine.add(fx.vignette(
                    clip_dur=clip_dur,
                    width=effects.get('vignette_width', 1.0),
                    height=effects.get('vignette_height', 1.0),
                    soft=effects.get('vignette_soft', 0.35),
                    blend=effects.get('vignette_blend', 0.25),
                    color=effects.get('vignette_color', (0.0, 0.0, 0.0))
                ))

            fade_in = effects.get('fade_in_frames', 0)
            fade_out = effects.get('fade_out_frames', 0)
            if fade_in > 0 or fade_out > 0:
                engine.add(fx.fade(clip_dur, fade_in=fade_in, fade_out=fade_out))

            tail_trans = effects.get('tail_transition')
            if tail_trans:
                engine.add(fx.transition_tail(clip_dur, tail_trans, effects.get('tail_transition_frames', 7)))

            head_trans = effects.get('head_transition')
            if head_trans:
                engine.add(fx.transition_head(clip_dur, head_trans, effects.get('head_transition_frames', 7)))

            comp_content = engine.serialize()

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
