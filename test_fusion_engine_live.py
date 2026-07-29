#!/usr/bin/env python3
"""
Complete showcase: V1 effects + V2 transitions, all in one clean run.
Uses DaVinci default parameter values from the built-in .setting files.
"""

import sys
import os
import time
import subprocess

sys.path.append("/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules")
os.environ["RESOLVE_SCRIPT_API"] = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
os.environ["RESOLVE_SCRIPT_LIB"] = "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'library', 'tools'))

from fusion import CompEngine, fx, write_comp
from fusion.nodes import FusionNode, BezierSpline, FusionComp

import DaVinciResolveScript as dvr


COMP_DIR = os.path.join(os.path.dirname(__file__), 'library', 'steps',
                        'step_6_01_render', 'fusion_comps')
os.makedirs(COMP_DIR, exist_ok=True)
TRANS = 20  # transition duration in frames


def build_v2_transition(dur, label):
    """Build a standalone transition comp for V2 overlay."""
    last = dur - 1
    mid = dur // 2

    comp = FusionComp(duration=dur)
    mi = FusionNode("MediaIn1", "MediaIn")
    mi.set_input("MediaIn1.GlobalStart", 0)
    mi.set_input("MediaIn1.GlobalEnd", last)
    mi.pos = (0, 0)
    comp.add_node(mi)
    prev = "MediaIn1"

    if label == "dip_to_black":
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
        sp = BezierSpline("Blend1")
        sp.add_key(0, 1.0)
        sp.add_key(mid, 0.0)
        sp.add_key(last, 1.0)
        comp.add_node(sp)
        mg = FusionNode("Merge1", "Merge")
        mg.set_input("Background", "MediaIn1")
        mg.set_input("Foreground", "Background1")
        mg.set_input("Blend", sp)
        mg.pos = (220, 0)
        comp.add_node(mg)
        prev = "Merge1"

    elif label == "flash":
        # DaVinci default: Brightness=0.67, Saturation=1.83
        bc = FusionNode("BC1", "BrightnessContrast")
        bc.set_input("Input", "MediaIn1")
        bc.set_input("Brightness", 0.67)
        bc.set_input("Saturation", 1.83)
        sp = BezierSpline("FlashBlend")
        sp.add_key(0, 0.0)
        sp.add_key(mid, 1.0)
        sp.add_key(last, 0.0)
        comp.add_node(sp)
        bc.set_input("Blend", sp)
        bc.pos = (110, 0)
        comp.add_node(bc)
        prev = "BC1"

    elif label == "defocus":
        df = FusionNode("DF1", "Defocus")
        df.set_input("Input", "MediaIn1")
        sp = BezierSpline("DFSize")
        sp.add_key(0, 0.0)
        sp.add_key(mid, 5.0)
        sp.add_key(last, 0.0)
        comp.add_node(sp)
        df.set_input("DefocusSize", sp)
        df.pos = (110, 0)
        comp.add_node(df)
        prev = "DF1"

    elif label == "zoom":
        tf = FusionNode("TF1", "Transform")
        tf.set_input("Input", "MediaIn1")
        tf.set_input("Center", (0.5, 0.5))
        sp = BezierSpline("ZSize")
        sp.add_key(0, 1.0)
        sp.add_key(mid, 1.12)
        sp.add_key(last, 1.0)
        comp.add_node(sp)
        tf.set_input("Size", sp)
        tf.pos = (110, 0)
        comp.add_node(tf)
        prev = "TF1"

    mo = FusionNode("MediaOut1", "MediaOut")
    mo.set_input("Input", prev)
    mo.pos = (440, 0)
    comp.add_node(mo)
    return comp.serialize()


def main():
    print("=" * 60)
    print("Complete Showcase — V1 Effects + V2 Transitions")
    print("=" * 60)

    resolve = dvr.scriptapp("Resolve")
    project = resolve.GetProjectManager().GetCurrentProject()
    mp = project.GetMediaPool()
    resolve.OpenPage("edit")
    time.sleep(0.5)

    # ── Step 1: Delete old timeline ──
    tl_name = "Fusion_Engine_Showcase"
    for i in range(1, project.GetTimelineCount() + 1):
        tl = project.GetTimelineByIndex(i)
        if tl and tl.GetName() == tl_name:
            project.SetCurrentTimeline(tl)
            mp.DeleteTimelines([tl])
            print(f"  Deleted '{tl_name}'")
            break
    time.sleep(0.5)

    # ── Step 2: Create fresh timeline ──
    timeline = mp.CreateEmptyTimeline(tl_name)
    project.SetCurrentTimeline(timeline)
    timeline.SetSetting("useCustomSettings", "1")
    timeline.SetSetting("timelineResolutionWidth", "1080")
    timeline.SetSetting("timelineResolutionHeight", "1920")
    timeline.SetSetting("timelineFrameRate", "30.000")
    print(f"  Created: {tl_name}")

    # ── Step 3: Place V1 clips ──
    root = mp.GetRootFolder()
    all_clips = root.GetClipList() or []
    vid_clips = [c for c in all_clips
                 if c.GetClipProperty().get("Type") in ("Video", "Video + Audio")]
    mp.AppendToTimeline(vid_clips[:5])
    time.sleep(1)

    v1 = timeline.GetItemListInTrack("video", 1) or []
    print(f"\n  V1: {len(v1)} clips")
    for i, c in enumerate(v1):
        print(f"    [{i+1}] {c.GetName()} frames {c.GetStart()}-{c.GetEnd()} dur={c.GetDuration()}")

    # ── Step 4: Apply V1 Fusion effects with transitions ──
    v1_specs = [
        ("dramatic", lambda d: CompEngine(d)
            .add(fx.zoom(d, start=1.0, mid=1.06, end=1.04, pan_end=(0.5, 0.48)))
            .add(fx.grade(gain=1.10, contrast=0.06, saturation=1.20))
            .add(fx.glow(gain=0.12, threshold=0.65))
            .add(fx.vignette(clip_dur=d, blend=0.35))
            .add(fx.transition_tail(d, "fade_to_black", TRANS))),

        ("film", lambda d: CompEngine(d)
            .add(fx.zoom(d, start=1.01, mid=1.0, end=1.01))
            .add(fx.grade(gain=0.96, contrast=0.05, saturation=0.88))
            .add(fx.grain(power=0.35, size=1.8))
            .add(fx.vignette(clip_dur=d, blend=0.30))
            .add(fx.transition_head(d, "fade_to_black", TRANS))
            .add(fx.transition_tail(d, "flash", TRANS))),

        ("contrast", lambda d: CompEngine(d)
            .add(fx.zoom(d, start=1.0, mid=1.04, end=1.02))
            .add(fx.grade(gain=1.12, contrast=0.10, saturation=1.28))
            .add(fx.glow(gain=0.18, threshold=0.55, size=6.0))
            .add(fx.vignette(clip_dur=d, blend=0.45, width=1.6, height=1.6))
            .add(fx.transition_head(d, "flash", TRANS))
            .add(fx.transition_tail(d, "defocus", TRANS))),

        ("dreamy", lambda d: CompEngine(d)
            .add(fx.zoom(d, start=1.0, mid=1.03, end=1.01))
            .add(fx.grade(gain=1.06, saturation=1.12))
            .add(fx.glow(gain=0.20, threshold=0.50, size=8.0))
            .add(fx.vignette(clip_dur=d, blend=0.28))
            .add(fx.transition_head(d, "defocus", TRANS))
            .add(fx.transition_tail(d, "zoom_blur", TRANS))),

        ("burst", lambda d: CompEngine(d)
            .add(fx.zoom(d, start=1.0, mid=1.08, end=1.06, pan_end=(0.48, 0.47)))
            .add(fx.grade(gain=1.08, contrast=0.06, saturation=1.18))
            .add(fx.grain(power=0.20))
            .add(fx.vignette(clip_dur=d, blend=0.40))
            .add(fx.transition_head(d, "zoom_blur", TRANS))),
    ]

    print(f"\n── V1 Effects + Transitions ──")
    for i, clip in enumerate(v1[:len(v1_specs)]):
        name, builder = v1_specs[i]
        dur = clip.GetDuration()
        for cn in (clip.GetFusionCompNameList() or []):
            clip.DeleteFusionCompByName(cn)
        comp_str = builder(dur).serialize()
        path = write_comp(os.path.join(COMP_DIR, f"v1_{name}.comp"), comp_str)
        result = clip.ImportFusionComp(path)
        time.sleep(0.3)
        comps = clip.GetFusionCompNameList() or []
        obj = clip.GetFusionCompByName(comps[0]) if comps else None
        tools = obj.GetToolList() if obj else {}
        n = len([t for t in tools.values()
                if t.GetAttrs().get('TOOLS_RegID') not in ('MediaIn', 'MediaOut')])
        print(f"  [{i+1}] {clip.GetName()}: {name} → {n} tools {'✓' if n > 3 else '✗'}")

    # ── Step 5: Add V2 track ──
    timeline.AddTrack("video")
    timeline.SetTrackName("video", 2, "Transitions")

    # ── Step 6: Import unique transparent clips for V2 ──
    base = os.path.abspath("library/assets")
    trans_files = []
    for i in range(4):
        src = os.path.join(base, "transparent_1080x1920_30fps.mov")
        dst = os.path.join(base, f"trans_overlay_{i+1}.mov")
        if not os.path.exists(dst):
            subprocess.run(["cp", src, dst])
        trans_files.append(dst)

    imported = mp.ImportMedia(trans_files)
    if not imported or len(imported) < 4:
        print("\n  ✗ Failed to import transparent clips")
        return

    # Place on V2 at cut points
    cut_points = [v1[i].GetEnd() for i in range(len(v1) - 1)]
    trans_dur = 30  # 1 sec transitions
    v2_labels = ["dip_to_black", "flash", "defocus", "zoom"]

    print(f"\n── V2 Transition Overlays ──")
    for idx, (mpi, cut) in enumerate(zip(imported[:4], cut_points[:4])):
        start = cut - trans_dur // 2
        clip_info = {
            "mediaPoolItem": mpi,
            "startFrame": 0,
            "endFrame": trans_dur - 1,
            "recordFrame": start,
            "trackIndex": 2,
            "mediaType": 1,
        }
        mp.AppendToTimeline([clip_info])
    time.sleep(1)

    # Import transition comps into V2 clips
    v2 = timeline.GetItemListInTrack("video", 2) or []
    print(f"  V2: {len(v2)} clips placed")

    for idx, v2clip in enumerate(v2[:4]):
        label = v2_labels[idx]
        dur = v2clip.GetDuration()
        comp_str = build_v2_transition(dur, label)
        path = write_comp(os.path.join(COMP_DIR, f"v2_{label}.comp"), comp_str)
        result = v2clip.ImportFusionComp(path)
        time.sleep(0.3)
        comps = v2clip.GetFusionCompNameList() or []
        obj = v2clip.GetFusionCompByName(comps[0]) if comps else None
        tools = obj.GetToolList() if obj else {}
        n = len([t for t in tools.values()
                if t.GetAttrs().get('TOOLS_RegID') not in ('MediaIn', 'MediaOut')])
        print(f"  V2[{idx+1}] {label} @ frame {v2clip.GetStart()}: {n} tools")

    # ── Final Summary ──
    print(f"\n{'=' * 60}")
    print("FINAL TIMELINE:")
    for t in range(1, timeline.GetTrackCount("video") + 1):
        items = timeline.GetItemListInTrack("video", t) or []
        tname = timeline.GetTrackName("video", t)
        print(f"  V{t} ({tname}): {len(items)} clips")
        for item in items:
            comps = item.GetFusionCompNameList() or []
            c = "✓ COMP" if comps else "✗ none"
            print(f"    {item.GetName():40s} @ {item.GetStart():>5}-{item.GetEnd():<5} [{c}]")
    print("=" * 60)


if __name__ == "__main__":
    main()
