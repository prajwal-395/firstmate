#!/usr/bin/env python3
"""
Integration Test: Full Pipeline Demo Timeline

Builds a complete timeline using ALL capabilities we've implemented:
  - V1 clips with animated Fusion .comp effects (zoom, grade, glow, grain, defocus)
  - Transition .comp files at clip boundaries (fade, zoom_blur, defocus, flash)
  - Track management and labeling
  - Clip properties (opacity, crop)
  - Complete verification with keyframe readback

This timeline is left in Resolve for visual inspection — scrub through
to see all the effects working together.
"""

import sys
import os
import time

# ─── Resolve Connection ──────────────────────────────────────

RESOLVE_SCRIPT_API = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
sys.path.append(os.path.join(RESOLVE_SCRIPT_API, "Modules"))
os.environ["RESOLVE_SCRIPT_API"] = RESOLVE_SCRIPT_API
os.environ["RESOLVE_SCRIPT_LIB"] = "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so"

# Add our library to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

import DaVinciResolveScript as dvr
from fusion_comp_generator import generate_comp, write_comp, SEGMENT_PRESETS
from fusion_transition_generator import generate_transition_comp, write_transition_comp

TIMELINE_NAME = "__full_pipeline_demo__"
COMP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fusion_comps', 'demo')

# ─── Test Functions ──────────────────────────────────────────

def log(icon, msg):
    print(f"  {icon} {msg}")


def run_integration_test():
    resolve = dvr.scriptapp("Resolve")
    if not resolve:
        print("ERROR: Cannot connect to DaVinci Resolve")
        return False

    pm = resolve.GetProjectManager()
    project = pm.GetCurrentProject()
    media_pool = project.GetMediaPool()

    print("=" * 70)
    print(f"  FULL PIPELINE INTEGRATION TEST")
    print(f"  Project: {project.GetName()}")
    print(f"  Resolve: {resolve.GetVersionString()}")
    print("=" * 70)

    # ════════════════════════════════════════════════════════════
    # STEP 1: Find media in pool
    # ════════════════════════════════════════════════════════════
    print(f"\n{'─'*70}")
    print(f"  Step 1: Media Pool Inventory")
    print(f"{'─'*70}")

    root = media_pool.GetRootFolder()
    pool_clips = {}

    def scan(folder):
        for c in (folder.GetClipList() or []):
            props = c.GetClipProperty()
            ctype = props.get("Type", "") if props else ""
            if ctype in ("Video", "Video + Audio"):
                pool_clips[c.GetName()] = c
        for sub in (folder.GetSubFolderList() or []):
            scan(sub)

    scan(root)

    if not pool_clips:
        print("  ERROR: No video clips in media pool")
        return False

    # Use all available video clips (up to 5)
    clip_names = sorted(pool_clips.keys())[:5]
    log("✅", f"Found {len(pool_clips)} video clips, using {len(clip_names)}")
    for cn in clip_names:
        log("  ", cn)

    # ════════════════════════════════════════════════════════════
    # STEP 2: Delete old test timeline, create new one
    # ════════════════════════════════════════════════════════════
    print(f"\n{'─'*70}")
    print(f"  Step 2: Create Timeline")
    print(f"{'─'*70}")

    for i in range(project.GetTimelineCount(), 0, -1):
        tl = project.GetTimelineByIndex(i)
        if tl and tl.GetName() == TIMELINE_NAME:
            media_pool.DeleteTimelines([tl])
            log("🗑️ ", f"Deleted existing {TIMELINE_NAME}")

    timeline = media_pool.CreateEmptyTimeline(TIMELINE_NAME)
    if not timeline:
        log("❌", "Failed to create timeline")
        return False

    project.SetCurrentTimeline(timeline)
    timeline.SetSetting("timelineResolutionWidth", "1080")
    timeline.SetSetting("timelineResolutionHeight", "1920")
    timeline.SetSetting("timelineFrameRate", "30.000")
    log("✅", f"Created: {TIMELINE_NAME} (1080x1920 @ 30fps)")

    # ════════════════════════════════════════════════════════════
    # STEP 3: Place V1 clips (audio auto-links to A1)
    # ════════════════════════════════════════════════════════════
    print(f"\n{'─'*70}")
    print(f"  Step 3: Place V1 A-Roll Clips")
    print(f"{'─'*70}")

    v1_items = []
    record_pos = 0  # Running record frame position

    for ci, cname in enumerate(clip_names):
        pool_item = pool_clips[cname]
        # Use 90 frames (3 seconds) of each clip
        dur = 90

        result = media_pool.AppendToTimeline([{
            "mediaPoolItem": pool_item,
            "startFrame": 0,
            "endFrame": dur,
            "trackIndex": 1,
            "recordFrame": record_pos,
        }])

        if result:
            placed = result[0] if isinstance(result, list) else result
            v1_items.append(placed)
            actual_dur = placed.GetDuration()
            log("✅", f"[{ci}] {cname}: {actual_dur}f @ TL {record_pos}")
            record_pos += actual_dur
        else:
            log("❌", f"[{ci}] {cname}: failed")

    total_frames = record_pos
    log("📊", f"Total timeline: {total_frames}f ({total_frames/30:.1f}s)")

    # ════════════════════════════════════════════════════════════
    # STEP 4: Add extra tracks
    # ════════════════════════════════════════════════════════════
    print(f"\n{'─'*70}")
    print(f"  Step 4: Track Setup")
    print(f"{'─'*70}")

    while timeline.GetTrackCount("video") < 2:
        timeline.AddTrack("video")
    while timeline.GetTrackCount("audio") < 3:
        timeline.AddTrack("audio")

    timeline.SetTrackName("video", 1, "A-Roll (VFX)")
    timeline.SetTrackName("video", 2, "B-Roll")
    timeline.SetTrackName("audio", 1, "Speech")
    timeline.SetTrackName("audio", 2, "Music")
    timeline.SetTrackName("audio", 3, "SFX")

    log("✅", f"V={timeline.GetTrackCount('video')}, A={timeline.GetTrackCount('audio')}")
    for ti in range(1, timeline.GetTrackCount("video") + 1):
        log("  ", f"V{ti}: {timeline.GetTrackName('video', ti)}")
    for ti in range(1, timeline.GetTrackCount("audio") + 1):
        log("  ", f"A{ti}: {timeline.GetTrackName('audio', ti)}")

    # ════════════════════════════════════════════════════════════
    # STEP 5: Apply Fusion .comp VFX to each V1 clip
    # ════════════════════════════════════════════════════════════
    print(f"\n{'─'*70}")
    print(f"  Step 5: Apply Fusion .comp VFX Per Clip")
    print(f"{'─'*70}")

    os.makedirs(COMP_DIR, exist_ok=True)

    # Map each clip to a different preset to showcase variety
    preset_order = ["HOOK", "CORE_INSIGHT", "EMOTIONAL_PEAK", "TURNING_POINT", "OUTRO"]
    vfx_results = {"pass": 0, "fail": 0}

    for ci, item in enumerate(v1_items):
        preset_name = preset_order[ci % len(preset_order)]
        preset = SEGMENT_PRESETS[preset_name]
        dur = item.GetDuration()

        comp_content = generate_comp(dur, **preset)
        comp_path = write_comp(os.path.join(COMP_DIR, f"clip_{ci}_{preset_name.lower()}.comp"), comp_content)

        # Clear existing comps
        for cn in (item.GetFusionCompNameList() or []):
            item.DeleteFusionCompByName(cn)

        result = item.ImportFusionComp(comp_path)
        if result:
            # Verify keyframes
            comp_names = item.GetFusionCompNameList()
            comp = item.GetFusionCompByName(comp_names[0]) if comp_names else None
            tools = comp.GetToolList() if comp else {}
            tool_ids = sorted(set(
                t.GetAttrs().get('TOOLS_RegID', '?')
                for t in tools.values()
                if t.GetAttrs().get('TOOLS_RegID') not in ('MediaIn', 'MediaOut')
            )) if tools else []

            # Check zoom animation
            xf = comp.FindTool("Transform1") if comp else None
            zoom_info = ""
            if xf:
                v0 = xf.GetInput("Size", 0)
                vm = xf.GetInput("Size", dur // 2)
                if v0 and vm:
                    zoom_info = f" zoom:{v0:.3f}→{vm:.3f}"

            log("✅", f"[{ci}] {preset_name}: {tool_ids}{zoom_info}")
            vfx_results["pass"] += 1
        else:
            log("❌", f"[{ci}] {preset_name}: ImportFusionComp failed")
            vfx_results["fail"] += 1

    # ════════════════════════════════════════════════════════════
    # STEP 6: Apply Transition .comp files at clip boundaries
    # ════════════════════════════════════════════════════════════
    print(f"\n{'─'*70}")
    print(f"  Step 6: Apply Transitions at Clip Boundaries")
    print(f"{'─'*70}")

    transition_types = ["fade_to_black", "zoom_blur", "defocus", "flash"]
    trans_dir = os.path.join(COMP_DIR, "transitions")
    os.makedirs(trans_dir, exist_ok=True)
    trans_results = {"pass": 0, "fail": 0}

    # Refresh V1 items (may have changed after comp import)
    v1_items_fresh = timeline.GetItemListInTrack("video", 1) or []

    for ti in range(len(v1_items_fresh) - 1):
        ttype = transition_types[ti % len(transition_types)]
        dur_frames = 7

        # TAIL comp on outgoing clip
        tail_comp = generate_transition_comp(dur_frames, ttype, position="tail")
        tail_path = write_transition_comp(
            os.path.join(trans_dir, f"t{ti}_{ttype}_tail.comp"), tail_comp
        )
        out_clip = v1_items_fresh[ti]
        tail_result = out_clip.ImportFusionComp(tail_path)

        # HEAD comp on incoming clip
        head_comp = generate_transition_comp(dur_frames, ttype, position="head")
        head_path = write_transition_comp(
            os.path.join(trans_dir, f"t{ti}_{ttype}_head.comp"), head_comp
        )
        in_clip = v1_items_fresh[ti + 1]
        head_result = in_clip.ImportFusionComp(head_path)

        if tail_result and head_result:
            log("✅", f"[{ti}] {ttype}: tail→{out_clip.GetName()}, head→{in_clip.GetName()}")
            trans_results["pass"] += 1
        else:
            log("❌", f"[{ti}] {ttype}: tail={tail_result}, head={head_result}")
            trans_results["fail"] += 1

    # ════════════════════════════════════════════════════════════
    # STEP 7: Apply static clip properties
    # ════════════════════════════════════════════════════════════
    print(f"\n{'─'*70}")
    print(f"  Step 7: Static Clip Properties")
    print(f"{'─'*70}")

    prop_tests = {"pass": 0, "fail": 0}

    # Set different properties on each clip to demonstrate capability
    properties_per_clip = [
        {"ZoomX": 1.02, "ZoomY": 1.02, "Pan": 0.01},
        {"Opacity": 95.0, "CropLeft": 2.0, "CropRight": 2.0},
        {"RotationAngle": 1.0, "Pan": -0.02, "Tilt": 0.01},
        {"ZoomX": 1.05, "ZoomY": 1.05},
        {"Opacity": 90.0, "CropTop": 3.0, "CropBottom": 3.0},
    ]

    for ci, item in enumerate(v1_items_fresh):
        if ci >= len(properties_per_clip):
            break
        props = properties_per_clip[ci]
        ok = True
        for pname, pval in props.items():
            item.SetProperty(pname, pval)
            readback = item.GetProperty(pname)
            if readback != pval:
                ok = False
        if ok:
            log("✅", f"[{ci}] {list(props.keys())}")
            prop_tests["pass"] += 1
        else:
            log("❌", f"[{ci}] Property mismatch")
            prop_tests["fail"] += 1

    # ════════════════════════════════════════════════════════════
    # STEP 8: Add timeline markers at key points
    # ════════════════════════════════════════════════════════════
    print(f"\n{'─'*70}")
    print(f"  Step 8: Timeline Markers")
    print(f"{'─'*70}")

    marker_colors = ["Red", "Green", "Blue", "Yellow", "Cyan"]
    frame_pos = 0
    for ci, item in enumerate(v1_items_fresh):
        color = marker_colors[ci % len(marker_colors)]
        preset_name = preset_order[ci % len(preset_order)]
        timeline.AddMarker(
            frame_pos + 1, color,
            f"Clip {ci}: {preset_name}",
            f"VFX preset applied: {preset_name}",
            1
        )
        frame_pos += item.GetDuration()

    markers = timeline.GetMarkers()
    log("✅", f"Added {len(markers)} markers")

    # ════════════════════════════════════════════════════════════
    # STEP 9: Fairlight preset check
    # ════════════════════════════════════════════════════════════
    print(f"\n{'─'*70}")
    print(f"  Step 9: Fairlight API Check")
    print(f"{'─'*70}")

    presets = resolve.GetFairlightPresets()
    log("ℹ️ ", f"Available presets: {len(presets) if presets else 0}")
    if presets:
        for pname in presets:
            log("  ", pname)
    else:
        log("  ", "(None configured — create in Fairlight → Presets to enable)")

    # ════════════════════════════════════════════════════════════
    # STEP 10: Full Verification
    # ════════════════════════════════════════════════════════════
    print(f"\n{'─'*70}")
    print(f"  Step 10: Full Verification")
    print(f"{'─'*70}")

    resolve.OpenPage("edit")

    # Track inventory
    print(f"\n  Track Inventory:")
    for ti in range(1, timeline.GetTrackCount("video") + 1):
        items = timeline.GetItemListInTrack("video", ti)
        name = timeline.GetTrackName("video", ti)
        count = len(items) if items else 0
        log("  ", f"V{ti} ({name}): {count} clips")

    for ti in range(1, timeline.GetTrackCount("audio") + 1):
        items = timeline.GetItemListInTrack("audio", ti)
        name = timeline.GetTrackName("audio", ti)
        count = len(items) if items else 0
        log("  ", f"A{ti} ({name}): {count} clips")

    # Fusion comp verification on each V1 clip
    print(f"\n  Fusion Comp Verification:")
    v1_final = timeline.GetItemListInTrack("video", 1) or []
    all_comps_ok = True

    for ci, item in enumerate(v1_final):
        comp_names = item.GetFusionCompNameList() or []
        if comp_names:
            comp = item.GetFusionCompByName(comp_names[0])
            tools = comp.GetToolList() if comp else {}
            tool_count = len([
                t for t in tools.values()
                if t.GetAttrs().get('TOOLS_RegID') not in ('MediaIn', 'MediaOut')
            ]) if tools else 0

            # Check for animated keyframes
            xf = comp.FindTool("Transform1") if comp else None
            animated = False
            if xf:
                v0 = xf.GetInput("Size", 0)
                # Fusion comps use source frame range, not timeline duration
                src_dur = item.GetSourceEndFrame() - item.GetSourceStartFrame() + 1
                ve = xf.GetInput("Size", src_dur - 1)
                if v0 and ve and abs(v0 - ve) > 0.001:
                    animated = True

            status = "✅" if tool_count > 0 else "❌"
            anim_str = " (animated ✓)" if animated else ""
            log(status, f"[{ci}] {item.GetName()}: {len(comp_names)} comp(s), {tool_count} tools{anim_str}")
        else:
            log("❌", f"[{ci}] {item.GetName()}: NO FUSION COMP")
            all_comps_ok = False

    # Duration check
    start_f = timeline.GetStartFrame()
    end_f = timeline.GetEndFrame()
    actual_dur = (end_f - start_f) / 30.0
    log("📊", f"Timeline duration: {actual_dur:.1f}s ({end_f - start_f}f)")

    # ════════════════════════════════════════════════════════════
    # SUMMARY
    # ════════════════════════════════════════════════════════════
    print(f"\n{'═'*70}")
    print(f"  INTEGRATION TEST SUMMARY")
    print(f"{'═'*70}")

    total_pass = vfx_results["pass"] + trans_results["pass"] + prop_tests["pass"]
    total_fail = vfx_results["fail"] + trans_results["fail"] + prop_tests["fail"]

    print(f"  VFX .comp imports:    {vfx_results['pass']} passed, {vfx_results['fail']} failed")
    print(f"  Transition .comps:    {trans_results['pass']} passed, {trans_results['fail']} failed")
    print(f"  Static properties:    {prop_tests['pass']} passed, {prop_tests['fail']} failed")
    print(f"  Timeline markers:     {len(markers)}")
    print(f"  Fusion comps OK:      {'✅ All' if all_comps_ok else '❌ Some missing'}")
    print(f"  ───────────────────────────────────────────")
    print(f"  TOTAL: {total_pass} passed, {total_fail} failed")
    print(f"{'═'*70}")

    if total_fail == 0 and all_comps_ok:
        print(f"\n  ✅ ALL TESTS PASSED — Timeline '{TIMELINE_NAME}' ready for review")
        print(f"\n  👉 Open Resolve, go to Edit page, and scrub through the timeline")
        print(f"     to see all effects. Switch to Fusion page on any clip to see")
        print(f"     the node graph with animated keyframes.")
    else:
        print(f"\n  ❌ SOME TESTS FAILED")

    return total_fail == 0


if __name__ == "__main__":
    success = run_integration_test()
    sys.exit(0 if success else 1)
