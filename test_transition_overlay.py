#!/usr/bin/env python3
"""
Adjustment Clip V2 placement — proper approach.

Strategy:
1. Build V1 with clips + effects
2. Add V2 track
3. For each cut point:
   a. Move playhead PAST all V1 clips (to a gap at the end)
   b. Insert Adjustment Clip → it appears at end of V1 (no clip splitting)
   c. Use MCP move_clips to move it to V2 at the correct position
4. Import transition Fusion comps into each Adjustment Clip
"""

import sys
import os
import time

sys.path.append("/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules")
os.environ["RESOLVE_SCRIPT_API"] = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
os.environ["RESOLVE_SCRIPT_LIB"] = "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'library', 'tools'))

from fusion import CompEngine, fx, write_comp
from fusion.nodes import FusionNode, BezierSpline, FusionComp

import DaVinciResolveScript as dvr


def build_transition_overlay(duration_frames, transition_type):
    """Build a .comp for a V2 transition overlay."""
    last = duration_frames - 1
    mid = duration_frames // 2
    comp = FusionComp(duration=duration_frames)

    mi = FusionNode("MediaIn1", "MediaIn")
    mi.set_input("MediaIn1.GlobalStart", 0)
    mi.set_input("MediaIn1.GlobalEnd", last)
    mi.pos = (0, 0)
    comp.add_node(mi)
    prev = "MediaIn1"

    if transition_type == "dip_to_black":
        bg = FusionNode("Background1", "Background")
        bg.set_input("TopLeftRed", 0)
        bg.set_input("TopLeftGreen", 0)
        bg.set_input("TopLeftBlue", 0)
        bg.set_input("TopLeftAlpha", 1)
        bg.set_input("GlobalOut", last)
        bg.set_input("Width", 1080)
        bg.set_input("Height", 1920)
        bg.pos = (110, 82)
        comp.add_node(bg)
        sp = BezierSpline("MergeBlend")
        sp.add_key(0, 1.0, rh=(mid // 3, 1.0))
        sp.add_key(mid, 0.0, lh=(mid - mid // 3, 0.0), rh=(mid + mid // 3, 0.0))
        sp.add_key(last, 1.0, lh=(last - mid // 3, 1.0))
        comp.add_node(sp)
        mg = FusionNode("Merge1", "Merge")
        mg.set_input("Background", "MediaIn1")
        mg.set_input("Foreground", "Background1")
        mg.set_input("Blend", sp)
        mg.pos = (220, 0)
        comp.add_node(mg)
        prev = "Merge1"

    elif transition_type == "flash":
        bc = FusionNode("BrightnessContrast1", "BrightnessContrast")
        bc.set_input("Input", "MediaIn1")
        sp = BezierSpline("FlashGain")
        sp.add_key(0, 1.0, rh=(mid // 3, 1.0))
        sp.add_key(mid, 3.0, lh=(mid - mid // 4, 2.5), rh=(mid + mid // 4, 2.5))
        sp.add_key(last, 1.0, lh=(last - mid // 3, 1.0))
        comp.add_node(sp)
        bc.set_input("Gain", sp)
        bc.pos = (110, 0)
        comp.add_node(bc)
        prev = "BrightnessContrast1"

    elif transition_type == "blur_dissolve":
        df = FusionNode("Defocus1", "Defocus")
        df.set_input("Input", "MediaIn1")
        sp = BezierSpline("DefocusSize")
        sp.add_key(0, 0.0, rh=(mid // 3, 0.0))
        sp.add_key(mid, 5.0, lh=(mid - mid // 3, 4.0), rh=(mid + mid // 3, 4.0))
        sp.add_key(last, 0.0, lh=(last - mid // 3, 0.0))
        comp.add_node(sp)
        df.set_input("DefocusSize", sp)
        df.pos = (110, 0)
        comp.add_node(df)
        prev = "Defocus1"

    elif transition_type == "zoom_punch":
        tf = FusionNode("Transform1", "Transform")
        tf.set_input("Input", "MediaIn1")
        tf.set_input("Center", (0.5, 0.5))
        sp = BezierSpline("ZoomPunch")
        sp.add_key(0, 1.0, rh=(mid // 3, 1.0))
        sp.add_key(mid, 1.12, lh=(mid - mid // 4, 1.08), rh=(mid + mid // 4, 1.08))
        sp.add_key(last, 1.0, lh=(last - mid // 3, 1.0))
        comp.add_node(sp)
        tf.set_input("Size", sp)
        tf.pos = (110, 0)
        comp.add_node(tf)
        prev = "Transform1"

    mo = FusionNode("MediaOut1", "MediaOut")
    mo.set_input("Input", prev)
    mo.pos = (440, 0)
    comp.add_node(mo)
    return comp.serialize()


def frames_to_tc(frame, fps=30):
    h = frame // (fps * 3600)
    m = (frame // (fps * 60)) % 60
    s = (frame // fps) % 60
    f = frame % fps
    return f"{h:02d}:{m:02d}:{s:02d}:{f:02d}"


def main():
    print("=" * 60)
    print("V2 Adjustment Clip Transitions — Final Build")
    print("=" * 60)

    resolve = dvr.scriptapp("Resolve")
    project = resolve.GetProjectManager().GetCurrentProject()
    mp = project.GetMediaPool()

    resolve.OpenPage("edit")
    time.sleep(0.5)

    # ── Clean start ──
    tl_name = "Fusion_Engine_Showcase"
    for i in range(1, project.GetTimelineCount() + 1):
        tl = project.GetTimelineByIndex(i)
        if tl and tl.GetName() == tl_name:
            project.SetCurrentTimeline(tl)
            mp.DeleteTimelines([tl])
            break

    timeline = mp.CreateEmptyTimeline(tl_name)
    project.SetCurrentTimeline(timeline)
    timeline.SetSetting("useCustomSettings", "1")
    timeline.SetSetting("timelineResolutionWidth", "1080")
    timeline.SetSetting("timelineResolutionHeight", "1920")
    timeline.SetSetting("timelineFrameRate", "30.000")

    # ── V1 clips ──
    root = mp.GetRootFolder()
    all_clips = root.GetClipList() or []
    vid_clips = [c for c in all_clips
                 if c.GetClipProperty().get("Type") in ("Video", "Video + Audio")]
    mp.AppendToTimeline(vid_clips[:5])
    time.sleep(1)

    v1_items = timeline.GetItemListInTrack("video", 1) or []
    print(f"\n  V1: {len(v1_items)} clips")
    for i, c in enumerate(v1_items):
        print(f"    [{i}] {c.GetName()} {c.GetStart()}-{c.GetEnd()}")

    v1_end = v1_items[-1].GetEnd()

    # ── V1 effects ──
    comp_dir = os.path.join(os.path.dirname(__file__), 'library', 'steps',
                            'step_6_01_render', 'fusion_comps')
    os.makedirs(comp_dir, exist_ok=True)

    effects = [
        ("dramatic", lambda d: CompEngine(d)
            .add(fx.zoom(d, start=1.0, mid=1.06, end=1.04, pan_end=(0.5, 0.48)))
            .add(fx.grade(gain=1.10, contrast=0.06, saturation=1.20))
            .add(fx.glow(gain=0.12, threshold=0.65))
            .add(fx.vignette(clip_dur=d, blend=0.35))),
        ("film", lambda d: CompEngine(d)
            .add(fx.zoom(d, start=1.01, mid=1.0, end=1.01))
            .add(fx.grade(gain=0.96, contrast=0.05, saturation=0.88))
            .add(fx.grain(power=0.35, size=1.8))
            .add(fx.vignette(clip_dur=d, blend=0.30))),
        ("contrast", lambda d: CompEngine(d)
            .add(fx.zoom(d, start=1.0, mid=1.04, end=1.02))
            .add(fx.grade(gain=1.12, contrast=0.10, saturation=1.28))
            .add(fx.glow(gain=0.18, threshold=0.55, size=6.0))
            .add(fx.vignette(clip_dur=d, blend=0.45, width=1.6, height=1.6))),
        ("dreamy", lambda d: CompEngine(d)
            .add(fx.zoom(d, start=1.0, mid=1.03, end=1.01))
            .add(fx.grade(gain=1.06, saturation=1.12))
            .add(fx.glow(gain=0.20, threshold=0.50, size=8.0))
            .add(fx.vignette(clip_dur=d, blend=0.28))),
        ("burst", lambda d: CompEngine(d)
            .add(fx.zoom(d, start=1.0, mid=1.08, end=1.06, pan_end=(0.48, 0.47)))
            .add(fx.grade(gain=1.08, contrast=0.06, saturation=1.18))
            .add(fx.grain(power=0.20))
            .add(fx.vignette(clip_dur=d, blend=0.40))),
    ]

    print(f"\n── V1 Effects ──")
    for i, clip in enumerate(v1_items[:len(effects)]):
        name, builder = effects[i]
        dur = clip.GetDuration()
        for cn in (clip.GetFusionCompNameList() or []):
            clip.DeleteFusionCompByName(cn)
        comp_str = builder(dur).serialize()
        clip.ImportFusionComp(
            write_comp(os.path.join(comp_dir, f"v1_{name}.comp"), comp_str))
        print(f"  [{i+1}] {name}")

    # ── Add V2 ──
    timeline.AddTrack("video")
    timeline.SetTrackName("video", 2, "Transitions")

    # ── Insert Adjustment Clips at the END of V1 (gap area), then move to V2 ──
    transitions = [
        ("dip_to_black", 30),
        ("flash", 20),
        ("blur_dissolve", 24),
        ("zoom_punch", 20),
    ]

    cut_points = [v1_items[i].GetEnd() for i in range(len(v1_items) - 1)]
    print(f"\n── Inserting Adjustment Clips ──")
    print(f"  V1 ends at frame {v1_end}")
    print(f"  Cut points: {cut_points}")

    adj_clip_ids = []
    for idx, (trans_type, dur) in enumerate(transitions[:len(cut_points)]):
        # Position playhead PAST all content to insert in empty space
        gap_frame = v1_end + 200 + idx * 150
        tc = frames_to_tc(gap_frame)
        timeline.SetCurrentTimecode(tc)
        time.sleep(0.3)

        item = timeline.InsertGeneratorIntoTimeline("Adjustment Clip")
        time.sleep(0.3)

        if item:
            uid = item.GetUniqueId()
            start = item.GetStart()
            item_dur = item.GetDuration()
            print(f"  [{idx+1}] Inserted: uid={uid[:8]}... "
                  f"@ frame {start}, dur={item_dur}")
            adj_clip_ids.append(uid)
        else:
            print(f"  [{idx+1}] FAILED")

    # ── Now use MCP move_clips to relocate each to V2 at the right position ──
    # (will be done via MCP calls from main script)
    # For now, report the state
    print(f"\n── Current State ──")
    for t in range(1, timeline.GetTrackCount("video") + 1):
        items = timeline.GetItemListInTrack("video", t) or []
        print(f"  V{t}: {len(items)} items")
        for item in items:
            print(f"    {item.GetName()} @ {item.GetStart()}-{item.GetEnd()} "
                  f"uid={item.GetUniqueId()[:8]}...")

    # Store the info we need for MCP move_clips
    print(f"\n── MCP Move Instructions ──")
    for idx, uid in enumerate(adj_clip_ids):
        if idx >= len(cut_points):
            break
        trans_type, dur = transitions[idx]
        target_frame = cut_points[idx] - dur // 2
        print(f"  move_clips(clip_ids=['{uid}'], "
              f"target_track_index=2, "
              f"record_frame={target_frame}, "
              f"placement='same_time')")

    print(f"\n  adj_clip_ids = {adj_clip_ids}")
    print(f"  cut_points = {cut_points}")
    print(f"  transitions = {transitions}")


if __name__ == "__main__":
    main()
