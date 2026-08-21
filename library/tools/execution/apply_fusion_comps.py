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


def _source_resolution(mpi):
    """(width, height) of the frame Fusion composites over, or None.

    Read off the MediaPoolItem rather than assumed, and judged by what
    Resolve returns: `GetClipProperty("Resolution")` gives "1920x1080".
    None means "could not tell", and the comp builder then falls back to
    its documented default rather than inventing a size.

    Rotation is deliberately NOT applied. Resolve reports the STORED
    frame, and Fusion's MediaIn delivers the stored frame - the display
    orientation is applied downstream, on the timeline. Swapping the axes
    here would put the band back on exactly the clips that do not have
    it.
    """
    if not mpi:
        return None
    raw = mpi.GetClipProperty("Resolution")
    if not raw or "x" not in str(raw):
        return None
    try:
        width, height = (int(part) for part in str(raw).split("x", 1))
    except (TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    return (width, height)


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

    # Imported at function scope, not inside the `has_any_effects` branch:
    # the generator-overlay pass at the bottom of this function reads
    # `import_effect_to_clip`, and a plan with generator overlays but no
    # per-clip VFX and no drawn transitions never entered that branch, so
    # the name was unbound and the whole subprocess died with a NameError.
    # resolve_build_timeline only records that as a warning, so the
    # generators silently never reached V5.
    #
    # import_customized_effect is deliberately NOT imported - see the
    # built-in branch below.
    try:
        from builtin_effect_loader import (
            list_builtin_effects, import_effect_to_clip, is_generator_effect,
        )
    except ImportError:
        list_builtin_effects = None
        import_effect_to_clip = None
        is_generator_effect = None

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
            # Also fusion_comp_generator is in library/steps/step_6_01_render
            sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'steps', 'step_6_01_render'))
            from fusion_comp_generator import write_comp
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

            # A block-type preset used to be substituted here when a clip
            # carried no effects. It was unreachable twice over and is
            # deleted; see docs/PIPELINE_PLAN.md P3.4.
            effects = dict(per_clip_effects.get(label, {}))

            preset_name = effects.get('_preset', None)
            
            # Exact match only. The substring fallback that used to sit
            # here could turn one effect name into an unrelated built-in -
            # the handoff tells the planner to use the exact snake_case
            # name, so a near miss is a mistake to surface, not to guess at.
            builtin_effect = None
            if preset_name and list_builtin_effects:
                if preset_name in (list_builtin_effects() or {}):
                    builtin_effect = preset_name

            # Defense in depth: reject generator presets at the renderer.
            # A generator has no image input and would cover the clip's
            # picture rather than modify it. An unclassifiable preset is
            # rejected too - the gate must fail closed, not open.
            if builtin_effect and is_generator_effect:
                try:
                    if is_generator_effect(builtin_effect):
                        print(
                            f"  ✗ [{orig_ci}] {label}: Rejected generator "
                            f"preset {builtin_effect} - no image input, "
                            f"cannot modify the picture as a clip effect",
                            file=sys.stderr,
                        )
                        builtin_effect = None
                except ValueError as exc:
                    print(
                        f"  ✗ [{orig_ci}] {label}: Rejected unclassifiable "
                        f"preset {builtin_effect} - classifier raised: {exc}",
                        file=sys.stderr,
                    )
                    builtin_effect = None


            if builtin_effect and import_effect_to_clip:
                # DaVinci's own .setting file goes to Resolve byte-identical,
                # with no parser in the path.
                #
                # This used to branch to import_customized_effect whenever the
                # clip carried any parameter, which is always: compile_manifest
                # merges the film look onto every V1/V2 clip. That path parsed
                # the macro and re-serialized it, which dropped the
                # GroupOperator wrapper and every InstanceInput - including
                # MainInput1, the declaration that gives the macro its image
                # input. Chromatic Aberration came out with its DirectionalBlur
                # wired to nothing.
                #
                # The overrides it was applying are our own comp-engine
                # parameter names (glow_gain, film_grain, vignette...), read
                # only by library/tools/fusion/comp_builder.py. No Resolve tool
                # reads them, so setting them on a macro's nodes never did
                # anything but corrupt the file. Dropping them costs the
                # picture nothing.
                effects.pop('_preset', None)
                tl_clip = v1_items[item_idx]
                for cn in (tl_clip.GetFusionCompNameList() or []):
                    tl_clip.DeleteFusionCompByName(cn)

                import_effect_to_clip(tl_clip, builtin_effect)

                comp_names = tl_clip.GetFusionCompNameList()
                if comp_names and len(comp_names) > 0:
                    print(f"  ✓ [{orig_ci}] {label}: Imported built-in effect {builtin_effect}", file=sys.stderr)
                else:
                    print(f"  ✗ [{orig_ci}] {label}: Import built-in effect {builtin_effect} failed", file=sys.stderr)
                continue

            preset_name = effects.pop('_preset', None)
            tl_clip = v1_items[item_idx]

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
            # The frame FUSION sees, which is the source clip's own, not
            # the delivery format. Every Background node the comp builds
            # is a solid image merged over MediaIn, so the wrong size
            # paints a hard-edged rectangle in the middle of the picture.
            source_res = _source_resolution(mpi)

            # The played segment within the source clip.  The manifest
            # carries source_in/source_out in seconds; convert to frames
            # using the source clip's native FPS so the comp builder
            # places keyframes inside the window the timeline plays.
            src_in_sec = clip_spec.get('source_in', 0.0)
            src_out_sec = clip_spec.get('source_out', 0.0)
            if src_in_sec or src_out_sec:
                src_fps_str = mpi.GetClipProperty('FPS')
                src_fps = float(src_fps_str) if src_fps_str else fps
                effects['source_in_frame'] = round(src_in_sec * src_fps)
                effects['source_out_frame'] = round(src_out_sec * src_fps)

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
            asset_key = clip_asset_key(label, effects, clip_dur,
                                       source_res=source_res)
            custom_asset = get_custom_asset(project_folder, asset_key)
            if custom_asset:
                for cn in (tl_clip.GetFusionCompNameList() or []):
                    tl_clip.DeleteFusionCompByName(cn)

                tl_clip.ImportFusionComp(custom_asset)
                print(f"  ✓ [{orig_ci}] {label}: Imported custom asset {asset_key}", file=sys.stderr)
                continue

            # 3. Generate custom .comp via composable engine
            comp_content = build_effect_comp(effects, clip_dur, source_res)

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


    # ── Generator overlays on V5 ──
    # Generator presets (.setting files) produce content from nothing and
    # are placed on V5 carrier clips by resolve_build_timeline. Here we
    # import the .setting file onto each V5 clip.
    gen_overlays = fusion_effects.get('generator_overlays', [])
    if gen_overlays and import_effect_to_clip:
        v5_items = timeline.GetItemListInTrack("video", 5) or []
        if v5_items:
            print(
                f"\n-- V5 Generator Imports: "
                f"{len(gen_overlays)} overlays, {len(v5_items)} clips --",
                file=sys.stderr,
            )
            for gi, gen in enumerate(gen_overlays):
                if gi >= len(v5_items):
                    print(
                        f"  X [{gi}] {gen['effect_name']}: no V5 clip",
                        file=sys.stderr,
                    )
                    continue
                tl_clip = v5_items[gi]
                effect_name = gen['effect_name']

                # Clear any existing comps on the carrier clip
                for cn in (tl_clip.GetFusionCompNameList() or []):
                    tl_clip.DeleteFusionCompByName(cn)

                import_effect_to_clip(tl_clip, effect_name)

                comp_names = tl_clip.GetFusionCompNameList()
                if comp_names and len(comp_names) > 0:
                    print(
                        f"  V [{gi}] {gen['effect_name']}: "
                        f"imported generator preset",
                        file=sys.stderr,
                    )
                else:
                    print(
                        f"  X [{gi}] {gen['effect_name']}: "
                        f"import failed",
                        file=sys.stderr,
                    )
        else:
            print(
                "  WARNING: generator_overlays present but no V5 clips",
                file=sys.stderr,
            )

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
