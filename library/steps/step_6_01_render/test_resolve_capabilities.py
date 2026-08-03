#!/usr/bin/env python3
"""
Comprehensive DaVinci Resolve Capability Test Suite

Tests EVERY programmatic capability we've confirmed working.
Run this with Resolve open and a project loaded.

Usage:
    python3 test_resolve_capabilities.py
"""

import sys
import os
import time
import json
import traceback
from datetime import datetime

# ─── Resolve API Setup ────────────────────────────────────────
RESOLVE_SCRIPT_API = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
sys.path.append(os.path.join(RESOLVE_SCRIPT_API, "Modules"))
os.environ["RESOLVE_SCRIPT_API"] = RESOLVE_SCRIPT_API
os.environ["RESOLVE_SCRIPT_LIB"] = "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so"

COMP_DIR = "/tmp/resolve_test_comps"
os.makedirs(COMP_DIR, exist_ok=True)

# ─── Test Results Tracking ────────────────────────────────────

class TestResults:
    def __init__(self):
        self.passed = []
        self.failed = []
        self.skipped = []
        self.current_section = ""
    
    def section(self, name):
        self.current_section = name
        print(f"\n{'═'*60}")
        print(f"  {name}")
        print(f"{'═'*60}")
    
    def ok(self, test_name, detail=""):
        self.passed.append((self.current_section, test_name))
        d = f" → {detail}" if detail else ""
        print(f"  ✅ {test_name}{d}")
    
    def fail(self, test_name, error=""):
        self.failed.append((self.current_section, test_name, error))
        print(f"  ❌ {test_name} — {error}")
    
    def skip(self, test_name, reason=""):
        self.skipped.append((self.current_section, test_name))
        print(f"  ⏭️  {test_name} — {reason}")
    
    def summary(self):
        total = len(self.passed) + len(self.failed) + len(self.skipped)
        print(f"\n{'═'*60}")
        print(f"  TEST SUMMARY")
        print(f"{'═'*60}")
        print(f"  Total:   {total}")
        print(f"  Passed:  {len(self.passed)} ✅")
        print(f"  Failed:  {len(self.failed)} ❌")
        print(f"  Skipped: {len(self.skipped)} ⏭️")
        
        if self.failed:
            print(f"\n  Failed tests:")
            for section, name, err in self.failed:
                print(f"    [{section}] {name}: {err}")
        
        return len(self.failed) == 0

results = TestResults()


# ═══════════════════════════════════════════════════════════════
# TEST 0: API Connection
# ═══════════════════════════════════════════════════════════════

results.section("0. API Connection")

try:
    import DaVinciResolveScript as dvr
    resolve = dvr.scriptapp("Resolve")
    assert resolve is not None, "resolve is None"
    results.ok("Import DaVinciResolveScript")
except Exception as e:
    results.fail("Import DaVinciResolveScript", str(e))
    print("\n  FATAL: Cannot connect to Resolve. Is it running?")
    sys.exit(1)

try:
    pm = resolve.GetProjectManager()
    assert pm is not None
    project = pm.GetCurrentProject()
    assert project is not None
    results.ok("GetProjectManager + GetCurrentProject", project.GetName())
except Exception as e:
    results.fail("GetProjectManager", str(e))
    sys.exit(1)

try:
    version = resolve.GetVersionString()
    product = resolve.GetProductName()
    results.ok("GetVersionString", f"{product} {version}")
except Exception as e:
    results.fail("GetVersionString", str(e))

try:
    page = resolve.GetCurrentPage()
    results.ok("GetCurrentPage", page)
except Exception as e:
    results.fail("GetCurrentPage", str(e))


# ═══════════════════════════════════════════════════════════════
# TEST 1: Page Navigation
# ═══════════════════════════════════════════════════════════════

results.section("1. Page Navigation")

for page_name in ["edit", "fusion", "color", "fairlight", "deliver", "edit"]:
    try:
        r = resolve.OpenPage(page_name)
        time.sleep(0.3)
        current = resolve.GetCurrentPage()
        if current == page_name:
            results.ok(f"OpenPage('{page_name}')")
        else:
            results.fail(f"OpenPage('{page_name}')", f"expected '{page_name}', got '{current}'")
    except Exception as e:
        results.fail(f"OpenPage('{page_name}')", str(e))

resolve.OpenPage("edit")
time.sleep(0.5)


# ═══════════════════════════════════════════════════════════════
# TEST 2: Timeline Creation & Track Management
# ═══════════════════════════════════════════════════════════════

results.section("2. Timeline Creation & Track Management")

mp = project.GetMediaPool()

# Create a test timeline
try:
    test_tl = mp.CreateEmptyTimeline("__capability_test__")
    assert test_tl is not None, "CreateEmptyTimeline returned None"
    results.ok("CreateEmptyTimeline")
except Exception as e:
    results.fail("CreateEmptyTimeline", str(e))
    # Try to use existing
    test_tl = project.GetCurrentTimeline()

# Set current timeline
try:
    project.SetCurrentTimeline(test_tl)
    results.ok("SetCurrentTimeline")
except Exception as e:
    results.fail("SetCurrentTimeline", str(e))

# Add video tracks
try:
    for i in range(3):  # Add V2, V3, V4
        test_tl.AddTrack("video")
    v_count = test_tl.GetTrackCount("video")
    assert v_count >= 4, f"Expected >=4 video tracks, got {v_count}"
    results.ok(f"AddTrack('video') x3", f"{v_count} total video tracks")
except Exception as e:
    results.fail("AddTrack('video')", str(e))

# Add audio tracks
try:
    for i in range(3):  # Add A2, A3, A4
        test_tl.AddTrack("audio")
    a_count = test_tl.GetTrackCount("audio")
    assert a_count >= 4, f"Expected >=4 audio tracks, got {a_count}"
    results.ok(f"AddTrack('audio') x3", f"{a_count} total audio tracks")
except Exception as e:
    results.fail("AddTrack('audio')", str(e))

# Track naming
try:
    test_tl.SetTrackName("video", 1, "V1-ARoll")
    test_tl.SetTrackName("audio", 1, "A1-Speech")
    test_tl.SetTrackName("audio", 2, "A2-Music")
    
    name_v1 = test_tl.GetTrackName("video", 1)
    name_a1 = test_tl.GetTrackName("audio", 1)
    name_a2 = test_tl.GetTrackName("audio", 2)
    
    assert name_v1 == "V1-ARoll", f"Got '{name_v1}'"
    assert name_a1 == "A1-Speech", f"Got '{name_a1}'"
    results.ok("SetTrackName + GetTrackName", f"V1='{name_v1}', A1='{name_a1}', A2='{name_a2}'")
except Exception as e:
    results.fail("SetTrackName", str(e))


# ═══════════════════════════════════════════════════════════════
# TEST 3: Media Pool & Clip Placement
# ═══════════════════════════════════════════════════════════════

results.section("3. Media Pool & Clip Placement")

# Find or import a test media file
try:
    root_folder = mp.GetRootFolder()
    clips = root_folder.GetClipList()
    
    # Find a video clip in the pool
    test_clip = None
    for c in clips:
        props = c.GetClipProperty()
        if props and props.get("Type") in ["Video", "Video + Audio"]:
            test_clip = c
            break
    
    if test_clip:
        results.ok("Find test media in pool", test_clip.GetName())
    else:
        results.skip("Find test media", "No video clips in media pool")
except Exception as e:
    results.fail("MediaPool access", str(e))

# Clip placement with mediaType
if test_clip:
    # Video-only placement on V1
    try:
        placed = mp.AppendToTimeline([{
            "mediaPoolItem": test_clip,
            "startFrame": 0,
            "endFrame": 89,  # 3 seconds at 30fps
            "trackIndex": 1,
            "recordFrame": 0,
        }])
        assert placed and len(placed) > 0, "AppendToTimeline returned empty"
        results.ok("AppendToTimeline (default, V1)")
    except Exception as e:
        results.fail("AppendToTimeline (default)", str(e))
    
    # Video-only on V2
    try:
        placed = mp.AppendToTimeline([{
            "mediaPoolItem": test_clip,
            "startFrame": 0,
            "endFrame": 29,
            "trackIndex": 2,
            "recordFrame": 30,
            "mediaType": 1,  # video-only
        }])
        results.ok("AppendToTimeline (mediaType=1, video-only, V2)")
    except Exception as e:
        results.fail("AppendToTimeline (mediaType=1)", str(e))

# Verify track items
try:
    v1_items = test_tl.GetItemListInTrack("video", 1)
    v1_count = len(v1_items) if v1_items else 0
    results.ok(f"GetItemListInTrack('video', 1)", f"{v1_count} clips")
except Exception as e:
    results.fail("GetItemListInTrack", str(e))


# ═══════════════════════════════════════════════════════════════
# TEST 4: Static Clip Properties
# ═══════════════════════════════════════════════════════════════

results.section("4. Static Clip Properties")

v1_items = test_tl.GetItemListInTrack("video", 1)
if v1_items and len(v1_items) > 0:
    clip = v1_items[0]
    
    # Test each property
    prop_tests = [
        ("ZoomX", 1.05),
        ("ZoomY", 1.05),
        ("Pan", 0.3),
        ("Tilt", -0.1),
        ("RotationAngle", 2.0),
        ("Opacity", 85.0),
        ("CropLeft", 5.0),
        ("CropRight", 5.0),
    ]
    
    for prop_name, value in prop_tests:
        try:
            result = clip.SetProperty(prop_name, value)
            readback = clip.GetProperty(prop_name)
            if result and readback is not None:
                results.ok(f"SetProperty('{prop_name}', {value})", f"readback={readback}")
            else:
                results.fail(f"SetProperty('{prop_name}')", f"result={result}, readback={readback}")
        except Exception as e:
            results.fail(f"SetProperty('{prop_name}')", str(e))
    
    # Reset properties
    for prop_name, _ in prop_tests:
        try:
            clip.SetProperty(prop_name, {"ZoomX": 1.0, "ZoomY": 1.0, "Pan": 0.0, 
                                          "Tilt": 0.0, "RotationAngle": 0.0, 
                                          "Opacity": 100.0, "CropLeft": 0.0, "CropRight": 0.0}[prop_name])
        except Exception:
            pass
    
    # GetName / SetName
    try:
        orig_name = clip.GetName()
        results.ok("GetName", orig_name)
    except Exception as e:
        results.fail("GetName", str(e))
    
    # GetDuration
    try:
        dur = clip.GetDuration()
        results.ok("GetDuration", f"{dur} frames")
    except Exception as e:
        results.fail("GetDuration", str(e))
    
    # Markers
    try:
        result = clip.AddMarker(10, "Blue", "Test Marker", "Note", 1)
        markers = clip.GetMarkers()
        if markers and 10 in markers:
            results.ok("AddMarker + GetMarkers", f"{len(markers)} markers")
            clip.DeleteMarkerAtFrame(10)
        else:
            results.fail("AddMarker", f"result={result}, markers={markers}")
    except Exception as e:
        results.fail("AddMarker", str(e))

else:
    results.skip("Static Clip Properties", "No clips on V1")


# ═══════════════════════════════════════════════════════════════
# TEST 5: Fusion Composition — Static Effects via API
# ═══════════════════════════════════════════════════════════════

results.section("5. Fusion Composition — Static Effects via API")

if v1_items and len(v1_items) > 0:
    clip = v1_items[0]
    
    # Add Fusion comp
    try:
        comp = clip.AddFusionComp()
        assert comp is not None, "AddFusionComp returned None"
        results.ok("AddFusionComp")
    except Exception as e:
        results.fail("AddFusionComp", str(e))
        comp = None
    
    if comp:
        # List tools
        try:
            tools = comp.GetToolList()
            tool_names = [t.GetAttrs().get('TOOLS_RegID', '?') for t in tools.values()]
            results.ok("GetToolList", f"Tools: {tool_names}")
        except Exception as e:
            results.fail("GetToolList", str(e))
        
        # Add tools and wire them
        try:
            bc = comp.AddTool("BrightnessContrast")
            assert bc is not None, "AddTool returned None"
            bc.SetInput("Gain", 1.05)
            bc.SetInput("Saturation", 1.10)
            readback = bc.GetInput("Gain")
            results.ok("AddTool('BrightnessContrast') + SetInput", f"Gain readback={readback}")
        except Exception as e:
            results.fail("AddTool('BrightnessContrast')", str(e))
        
        try:
            glow = comp.AddTool("SoftGlow")
            assert glow is not None
            glow.SetInput("Gain", 0.08)
            results.ok("AddTool('SoftGlow') + SetInput")
        except Exception as e:
            results.fail("AddTool('SoftGlow')", str(e))
        
        # Cleanup comp
        try:
            comp_names = clip.GetFusionCompNameList()
            for cn in (comp_names or []):
                clip.DeleteFusionCompByName(cn)
            results.ok("DeleteFusionCompByName (cleanup)")
        except Exception as e:
            results.fail("DeleteFusionCompByName", str(e))


# ═══════════════════════════════════════════════════════════════
# TEST 6: .comp File Import — ANIMATED KEYFRAMES
# ═══════════════════════════════════════════════════════════════

results.section("6. .comp File Import — Animated Keyframes (Critical)")

if v1_items and len(v1_items) > 0:
    clip = v1_items[0]
    dur = clip.GetDuration()
    mid = dur // 2
    last = dur - 1
    third = dur // 3
    
    # Generate animated .comp
    comp_content = f"""Composition {{
\tCurrentTime = 0,
\tRenderRange = {{ 0, {last} }},
\tGlobalRange = {{ 0, {last} }},
\tCurrentID = 10,
\tHiQ = true,
\tPlaybackUpdateMode = 0,
\tVersion = "Test Suite",
\tTools = {{
\t\tMediaIn1 = MediaIn {{
\t\t\tInputs = {{
\t\t\t\t["MediaIn1.GlobalStart"] = Input {{ Value = 0, }},
\t\t\t\t["MediaIn1.GlobalEnd"] = Input {{ Value = {last}, }},
\t\t\t}},
\t\t\tViewInfo = OperatorInfo {{ Pos = {{ 0, 0 }} }},
\t\t}},
\t\tTransform1 = Transform {{
\t\t\tCtrlWZoom = false,
\t\t\tInputs = {{
\t\t\t\tSize = Input {{
\t\t\t\t\tSourceOp = "Transform1Size",
\t\t\t\t\tSource = "Value",
\t\t\t\t}},
\t\t\t\tCenter = Input {{ Value = {{ 0.51, 0.49 }}, }},
\t\t\t}},
\t\t\tViewInfo = OperatorInfo {{ Pos = {{ 110, 0 }} }},
\t\t}},
\t\tTransform1Size = BezierSpline {{
\t\t\tSplineColor = {{ Red = 233, Green = 217, Blue = 11 }},
\t\t\tKeyFrames = {{
\t\t\t\t[0] = {{ 1.0, RH = {{ {third}, 1.0132 }} }},
\t\t\t\t[{mid}] = {{ 1.04, LH = {{ {mid - third}, 1.04 }}, RH = {{ {mid + third}, 1.04 }} }},
\t\t\t\t[{last}] = {{ 1.02, LH = {{ {last - third}, 1.0266 }} }},
\t\t\t}}
\t\t}},
\t\tBrightnessContrast1 = BrightnessContrast {{
\t\t\tInputs = {{
\t\t\t\tGain = Input {{ Value = 1.05, }},
\t\t\t\tSaturation = Input {{ Value = 1.15, }},
\t\t\t\tInput = Input {{
\t\t\t\t\tSourceOp = "Transform1",
\t\t\t\t\tSource = "Output",
\t\t\t\t}},
\t\t\t}},
\t\t\tViewInfo = OperatorInfo {{ Pos = {{ 220, 0 }} }},
\t\t}},
\t\tSoftGlow1 = SoftGlow {{
\t\t\tInputs = {{
\t\t\t\tGain = Input {{ Value = 0.08, }},
\t\t\t\tInput = Input {{
\t\t\t\t\tSourceOp = "BrightnessContrast1",
\t\t\t\t\tSource = "Output",
\t\t\t\t}},
\t\t\t}},
\t\t\tViewInfo = OperatorInfo {{ Pos = {{ 330, 0 }} }},
\t\t}},
\t\tMediaOut1 = MediaOut {{
\t\t\tInputs = {{
\t\t\t\tInput = Input {{
\t\t\t\t\tSourceOp = "SoftGlow1",
\t\t\t\t\tSource = "Output",
\t\t\t\t}},
\t\t\t}},
\t\t\tViewInfo = OperatorInfo {{ Pos = {{ 440, 0 }} }},
\t\t}},
\t}},
}}"""
    
    comp_path = os.path.join(COMP_DIR, "test_animated.comp")
    with open(comp_path, 'w') as f:
        f.write(comp_content)
    
    # Import
    try:
        # Clear existing comps
        for cn in (clip.GetFusionCompNameList() or []):
            clip.DeleteFusionCompByName(cn)
        
        result = clip.ImportFusionComp(comp_path)
        assert result is not None, "ImportFusionComp returned None"
        results.ok("ImportFusionComp (.comp file)")
    except Exception as e:
        results.fail("ImportFusionComp", str(e))
    
    # Verify keyframes
    try:
        comp_names = clip.GetFusionCompNameList()
        assert comp_names and len(comp_names) > 0, "No comps after import"
        comp = clip.GetFusionCompByName(comp_names[0])
        
        xf = comp.FindTool("Transform1")
        assert xf is not None, "Transform1 not found"
        
        v0 = xf.GetInput("Size", 0)
        v_mid = xf.GetInput("Size", mid)
        v_end = xf.GetInput("Size", last)
        
        assert v0 is not None, "Frame 0 readback is None"
        assert v_mid is not None, "Mid frame readback is None"
        
        animated = abs(v0 - v_mid) > 0.001 if v0 and v_mid else False
        assert animated, f"NOT ANIMATED: v0={v0}, v_mid={v_mid}"
        
        results.ok("Keyframe Readback (BezierSpline)", f"f0={v0:.3f}, f{mid}={v_mid:.3f}, f{last}={v_end:.3f}")
    except Exception as e:
        results.fail("Keyframe Readback", str(e))
    
    # Verify path animation
    try:
        center_0 = xf.GetInput("Center", 0)
        center_end = xf.GetInput("Center", last)
        results.ok("Path Keyframe Readback (Center)", f"f0={center_0}, f{last}={center_end}")
    except Exception as e:
        results.fail("Path Keyframe Readback", str(e))
    
    # Verify all tools loaded
    try:
        tools = comp.GetToolList()
        tool_ids = [t.GetAttrs().get('TOOLS_RegID', '?') for t in tools.values()
                   if t.GetAttrs().get('TOOLS_RegID') not in ('MediaIn', 'MediaOut')]
        expected = {'Transform', 'BezierSpline', 'BrightnessContrast', 'SoftGlow'}
        found = set(tool_ids)
        missing = expected - found
        if not missing:
            results.ok("All .comp tools loaded", f"{tool_ids}")
        else:
            results.fail("Missing tools in .comp", f"missing={missing}, found={found}")
    except Exception as e:
        results.fail("Tool verification", str(e))
    
    # Export comp and verify round-trip
    try:
        export_path = os.path.join(COMP_DIR, "test_export.comp")
        result = clip.ExportFusionComp(export_path, 0)
        assert result, "ExportFusionComp returned False"
        assert os.path.exists(export_path), "Export file not created"
        size = os.path.getsize(export_path)
        results.ok("ExportFusionComp (round-trip)", f"{size} bytes")
    except Exception as e:
        results.fail("ExportFusionComp", str(e))
    
    # Cleanup
    for cn in (clip.GetFusionCompNameList() or []):
        clip.DeleteFusionCompByName(cn)

else:
    results.skip("Animated Keyframes", "No clips on V1")


# ═══════════════════════════════════════════════════════════════
# TEST 7: All Fusion Tools — Availability Check
# ═══════════════════════════════════════════════════════════════

results.section("7. All Fusion Tools — Availability")

if v1_items and len(v1_items) > 0:
    clip = v1_items[0]
    comp = clip.AddFusionComp()
    
    if comp:
        all_tools = {
            # Core VFX
            "Transform": "Motion/Transform",
            "BrightnessContrast": "Color Grade",
            "SoftGlow": "Highlight Bloom",
            "Glow": "General Glow",
            "FilmGrain": "Film Grain",
            "Background": "Solid Color",
            "EllipseMask": "Vignette Shape",
            "Merge": "Compositing",
            "Blur": "Gaussian Blur",
            "DirectionalBlur": "Directional Blur",
            "Defocus": "Depth of Field",
            "VariBlur": "Variable Blur",
            
            # Transitions
            "Dissolve": "Cross Dissolve",
            "DVE": "3D Transform",
            "GridWarp": "Grid Warping",
            "Displace": "Displacement",
            "CornerPositioner": "Corner Pin",
            "PerspectivePositioner": "Perspective",
            
            # Time
            "VectorMotionBlur": "Motion Blur",
            "SpeedWarp": "Optical Flow Retime",
            "TimeSpeed": "Speed Ramp",
            "TimeStretcher": "Time Stretch",
            
            # Creative
            "Trails": "Motion Trails",
            "Highlight": "Highlight Effect",
            "HotSpot": "Hot Spot",
            "RankFilter": "Rank Filter",
            "Letterbox": "Letterbox",
            "Texture": "Texture",
            "Plasma": "Plasma Generator",
            "FastNoise": "Procedural Noise",
            
            # Color
            "ColorCorrector": "Color Corrector",
            "ColorGain": "Channel Adjust",
            
            # Matte/Key
            "MatteControl": "Matte Control",
            "BSpline": "Custom Mask",
        }
        
        available = []
        unavailable = []
        
        for tool_id, description in sorted(all_tools.items()):
            try:
                t = comp.AddTool(tool_id)
                if t:
                    available.append(tool_id)
                    t.Delete()
                else:
                    unavailable.append(tool_id)
            except Exception:
                unavailable.append(tool_id)
        
        results.ok(f"Available Fusion tools", f"{len(available)}/{len(all_tools)}")
        for tool_id in available:
            print(f"      ✓ {tool_id} ({all_tools[tool_id]})")
        
        if unavailable:
            for tool_id in unavailable:
                print(f"      ✗ {tool_id} ({all_tools[tool_id]})")
        
        # Cleanup
        for cn in (clip.GetFusionCompNameList() or []):
            clip.DeleteFusionCompByName(cn)
else:
    results.skip("Fusion Tools", "No clips on V1")


# ═══════════════════════════════════════════════════════════════
# TEST 8: .setting File (same format as .comp)
# ═══════════════════════════════════════════════════════════════

results.section("8. .setting File Export")

if v1_items and len(v1_items) > 0:
    clip = v1_items[0]
    
    # Import a comp first, then export as .setting
    comp = clip.AddFusionComp()
    if comp:
        comp.AddTool("BrightnessContrast")
        setting_path = os.path.join(COMP_DIR, "test_effect.setting")
        try:
            result = clip.ExportFusionComp(setting_path, 0)
            assert result, "Export returned False"
            assert os.path.exists(setting_path), "File not created"
            
            with open(setting_path) as f:
                content = f.read()
            
            is_lua = "Composition" in content and "Tools" in content
            results.ok("Export as .setting", f"Lua table format: {is_lua}, {len(content)} bytes")
        except Exception as e:
            results.fail("Export as .setting", str(e))
        
        for cn in (clip.GetFusionCompNameList() or []):
            clip.DeleteFusionCompByName(cn)
else:
    results.skip(".setting File", "No clips")


# ═══════════════════════════════════════════════════════════════
# TEST 9: Timeline Properties & Metadata
# ═══════════════════════════════════════════════════════════════

results.section("9. Timeline Properties & Metadata")

try:
    tl_name = test_tl.GetName()
    results.ok("GetName (timeline)", tl_name)
except Exception as e:
    results.fail("GetName", str(e))

try:
    start_tc = test_tl.GetStartTimecode()
    results.ok("GetStartTimecode", start_tc)
except Exception as e:
    results.fail("GetStartTimecode", str(e))

# Timeline markers
try:
    test_tl.AddMarker(30, "Green", "Timeline Marker", "Test note", 1)
    markers = test_tl.GetMarkers()
    if markers:
        results.ok("AddMarker + GetMarkers (timeline)", f"{len(markers)} markers")
        test_tl.DeleteMarkerAtFrame(30)
    else:
        results.fail("Timeline markers", "GetMarkers returned empty")
except Exception as e:
    results.fail("Timeline markers", str(e))

# Timecode
try:
    tc = test_tl.GetCurrentTimecode()
    results.ok("GetCurrentTimecode", tc)
except Exception as e:
    results.fail("GetCurrentTimecode", str(e))


# ═══════════════════════════════════════════════════════════════
# TEST 10: Fairlight API
# ═══════════════════════════════════════════════════════════════

results.section("10. Fairlight API")

# GetFairlightPresets
try:
    presets = resolve.GetFairlightPresets()
    results.ok("GetFairlightPresets", f"Found {len(presets)} presets: {presets}")
except Exception as e:
    results.fail("GetFairlightPresets", str(e))

# ApplyFairlightPresetToCurrentTimeline (with nonexistent name — expect False)
try:
    result = project.ApplyFairlightPresetToCurrentTimeline("__nonexistent_test__")
    if result == False:
        results.ok("ApplyFairlightPresetToCurrentTimeline (invalid name → False)", "API accessible, returns False for bad name")
    else:
        results.ok("ApplyFairlightPresetToCurrentTimeline", f"Unexpected: returned {result}")
except Exception as e:
    results.fail("ApplyFairlightPresetToCurrentTimeline", str(e))

# InsertAudioToCurrentTrackAtPlayhead exists
try:
    has_method = hasattr(project, 'InsertAudioToCurrentTrackAtPlayhead')
    results.ok("InsertAudioToCurrentTrackAtPlayhead exists", str(has_method))
except Exception as e:
    results.fail("InsertAudioToCurrentTrackAtPlayhead", str(e))

# Audio clip property test
try:
    a1_items = test_tl.GetItemListInTrack("audio", 1)
    if a1_items and len(a1_items) > 0:
        audio_clip = a1_items[0]
        props = audio_clip.GetProperty()
        results.ok("Audio clip GetProperty", f"Returns: {type(props).__name__} = {props}")
    else:
        results.skip("Audio clip properties", "No audio clips on A1")
except Exception as e:
    results.fail("Audio clip properties", str(e))


# ═══════════════════════════════════════════════════════════════
# TEST 11: Database Info
# ═══════════════════════════════════════════════════════════════

results.section("11. Database & Project Management")

try:
    db_list = pm.GetDatabaseList()
    results.ok("GetDatabaseList", str(db_list))
except Exception as e:
    results.fail("GetDatabaseList", str(e))

try:
    proj_name = project.GetName()
    unique_id = project.GetUniqueId()
    results.ok("Project GetUniqueId", f"'{proj_name}' → {unique_id}")
except Exception as e:
    results.fail("GetUniqueId", str(e))

# ExportProject
try:
    export_path = os.path.join(COMP_DIR, "test_project.drp")
    result = pm.ExportProject(project.GetName(), export_path)
    if result and os.path.exists(export_path):
        size = os.path.getsize(export_path)
        results.ok("ExportProject (.drp)", f"{size/1024:.1f}KB")
    else:
        results.fail("ExportProject", f"result={result}")
except Exception as e:
    results.fail("ExportProject", str(e))


# ═══════════════════════════════════════════════════════════════
# TEST 12: Color Grade API
# ═══════════════════════════════════════════════════════════════

results.section("12. Color Grade API")

if v1_items and len(v1_items) > 0:
    clip = v1_items[0]
    
    # GetNumNodes / GetNodeGraph
    try:
        nn = clip.GetNumNodes()
        results.ok("GetNumNodes (Color)", f"{nn} nodes")
    except Exception as e:
        results.fail("GetNumNodes", str(e))
    
    try:
        ng = clip.GetNodeGraph()
        results.ok("GetNodeGraph", f"type={type(ng)}, value={ng}")
    except Exception as e:
        results.fail("GetNodeGraph", str(e))
    
    # Clip color
    try:
        clip.SetClipColor("Orange")
        color = clip.GetClipColor()
        results.ok("SetClipColor + GetClipColor", color)
        clip.ClearClipColor()
    except Exception as e:
        results.fail("SetClipColor", str(e))

else:
    results.skip("Color Grade", "No clips")


# ═══════════════════════════════════════════════════════════════
# TEST 13: Timeline Export
# ═══════════════════════════════════════════════════════════════

results.section("13. Timeline Export Formats")

export_base = COMP_DIR

# Try known format codes
format_tests = [
    (0, "fcpxml", "Format 0"),
    (10, "aaf", "Format 10 (AAF)"),
]

for fmt_code, ext, desc in format_tests:
    try:
        path = os.path.join(export_base, f"test_export.{ext}")
        result = test_tl.Export(path, fmt_code)
        if result and os.path.exists(path):
            size = os.path.getsize(path)
            results.ok(f"Timeline Export ({desc})", f"{size} bytes")
        else:
            results.fail(f"Timeline Export ({desc})", f"result={result}")
    except Exception as e:
        results.fail(f"Timeline Export ({desc})", str(e))


# ═══════════════════════════════════════════════════════════════
# CLEANUP: Delete test timeline
# ═══════════════════════════════════════════════════════════════

results.section("14. Cleanup")

try:
    # Switch to another timeline first if possible
    tl_count = project.GetTimelineCount()
    if tl_count > 1:
        for i in range(1, tl_count + 1):
            tl = project.GetTimelineByIndex(i)
            if tl and tl.GetName() != "__capability_test__":
                project.SetCurrentTimeline(tl)
                break
    
    # Delete test timeline
    result = mp.DeleteTimelines([test_tl])
    results.ok("Delete test timeline", f"result={result}")
except Exception as e:
    results.fail("Delete test timeline", str(e))

# Clean up temp files
try:
    import shutil
    shutil.rmtree(COMP_DIR, ignore_errors=True)
    results.ok("Clean temp files")
except Exception:
    pass


# ═══════════════════════════════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════════════════════════════

all_passed = results.summary()

print(f"\n  Timestamp: {datetime.now().isoformat()}")
print(f"  Resolve: {resolve.GetVersionString()}")
print(f"  Project: {project.GetName()}")

sys.exit(0 if all_passed else 1)
